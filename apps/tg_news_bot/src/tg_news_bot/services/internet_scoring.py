"""Internet-level scoring for trend discovery."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

import feedparser
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_news_bot.config import InternetScoringSettings, TrendsSettings
from tg_news_bot.logging import get_logger
from tg_news_bot.repositories.trend_signals import TrendSignalRepository


_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "will",
    "with",
}


@dataclass(slots=True)
class InternetScoringContext:
    signal_boosts: dict[str, float]
    provider_stats: dict[str, int]
    collected_at: datetime


@dataclass(slots=True)
class InternetScoreResult:
    total: float
    components: dict[str, float]
    signal_hits: list[str]


class InternetScoringService:
    def __init__(
        self,
        *,
        settings: InternetScoringSettings,
        trends_settings: TrendsSettings,
        session_factory: async_sessionmaker[AsyncSession],
        signals_repo: TrendSignalRepository | None = None,
    ) -> None:
        self._settings = settings
        self._trends_settings = trends_settings
        self._session_factory = session_factory
        self._signals_repo = signals_repo or TrendSignalRepository()
        self._log = get_logger(__name__)
        self._cached_context: InternetScoringContext | None = None
        self._cached_until: datetime | None = None

    async def build_context(self) -> InternetScoringContext:
        now = datetime.now(timezone.utc)
        if self._cached_context and self._cached_until and now < self._cached_until:
            return self._cached_context

        boosts: dict[str, float] = {}
        provider_stats: dict[str, int] = {}

        db_boosts = await self._load_db_signal_boosts()
        _merge_boosts(
            target=boosts,
            updates=db_boosts,
            cap=self._settings.max_signal_boost_per_keyword,
        )
        provider_stats["trend_db"] = len(db_boosts)

        if self._settings.google_trends_enabled:
            google_boosts = await self._load_google_trends_boosts()
            _merge_boosts(
                target=boosts,
                updates=google_boosts,
                cap=self._settings.max_signal_boost_per_keyword,
            )
            provider_stats["google_trends"] = len(google_boosts)

        wordstat_boosts: dict[str, float] = {}
        for keyword, weight in self._settings.wordstat_keyword_boosts.items():
            normalized = _normalize_keyword(keyword)
            if not normalized:
                continue
            boost = max(min(float(weight), self._settings.max_signal_boost_per_keyword), 0.0)
            if boost <= 0:
                continue
            wordstat_boosts[normalized] = boost
        _merge_boosts(
            target=boosts,
            updates=wordstat_boosts,
            cap=self._settings.max_signal_boost_per_keyword,
        )
        provider_stats["wordstat"] = len(wordstat_boosts)

        if boosts:
            ordered = sorted(boosts.items(), key=lambda item: item[1], reverse=True)
            boosts = dict(ordered[: self._settings.max_signal_keywords])

        context = InternetScoringContext(
            signal_boosts=boosts,
            provider_stats=provider_stats,
            collected_at=now,
        )
        self._cached_context = context
        self._cached_until = now + timedelta(seconds=180)
        return context

    def score_item(
        self,
        *,
        text: str,
        source_name: str,
        seed_hits: list[str],
        exclude_hits: list[str],
        trusted_domain_match: bool,
        source_trust_score: float | None,
        signal_boosts: dict[str, float],
    ) -> InternetScoreResult:
        compact_text = _compact(text).lower()
        if not compact_text:
            return InternetScoreResult(total=0.0, components={}, signal_hits=[])

        score = 0.0
        components: dict[str, float] = {}

        seed_score = float(len(seed_hits)) * self._settings.seed_hit_weight
        if seed_score > 0:
            score += seed_score
            components["profile_seed_hits"] = seed_score

        if exclude_hits:
            penalty = -float(len(exclude_hits)) * self._settings.exclude_hit_penalty
            score += penalty
            components["profile_exclude_penalty"] = penalty

        source_weight = float(
            self._settings.source_weights.get(
                source_name.strip().upper(),
                self._settings.default_source_weight,
            )
        )
        score += source_weight
        components["network_source_weight"] = source_weight

        if trusted_domain_match:
            score += self._settings.trusted_domain_bonus
            components["trusted_domain_bonus"] = self._settings.trusted_domain_bonus

        trust_boost = 0.0
        if source_trust_score is not None:
            trust_boost = max(
                min(
                    float(source_trust_score) * self._settings.source_trust_multiplier,
                    self._settings.source_trust_boost_cap,
                ),
                -self._settings.source_trust_boost_cap,
            )
            score += trust_boost
            components["source_trust"] = trust_boost

        signal_hits: list[str] = []
        signal_score = 0.0
        for keyword, weight in sorted(
            signal_boosts.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            if keyword not in compact_text:
                continue
            applied = min(
                float(weight) * self._settings.signal_keyword_multiplier,
                self._settings.max_signal_boost_per_keyword,
            )
            if applied <= 0:
                continue
            signal_hits.append(keyword)
            signal_score += applied
            if signal_score >= self._settings.max_total_signal_boost:
                break
            if len(signal_hits) >= self._settings.max_signal_matches_per_item:
                break
        if signal_score > 0:
            signal_score = min(signal_score, self._settings.max_total_signal_boost)
            score += signal_score
            components["internet_signal_boost"] = signal_score

        return InternetScoreResult(total=score, components=components, signal_hits=signal_hits)

    async def _load_db_signal_boosts(self) -> dict[str, float]:
        if not self._settings.enabled:
            return {}
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=self._settings.lookback_hours)
        async with self._session_factory() as session:
            async with session.begin():
                rows = await self._signals_repo.list_recent_keyword_scores(
                    session,
                    since=since,
                    limit=self._settings.max_signal_keywords,
                )
        boosts: dict[str, float] = {}
        for keyword, weight_sum in rows:
            normalized = _normalize_keyword(keyword)
            if not normalized:
                continue
            boost = min(
                float(weight_sum) * self._settings.db_signal_multiplier,
                self._settings.max_signal_boost_per_keyword,
            )
            if boost <= 0:
                continue
            boosts[normalized] = boost
        return boosts

    async def _load_google_trends_boosts(self) -> dict[str, float]:
        feeds = list(self._settings.google_trends_feeds)
        if not feeds:
            return {}
        counter: Counter[str] = Counter()
        per_feed = max(1, self._settings.google_trends_top_n // max(len(feeds), 1))
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
            for feed_url in feeds:
                try:
                    response = await http.get(feed_url)
                    response.raise_for_status()
                    parsed = feedparser.parse(response.text)
                    for entry in parsed.entries[:per_feed]:
                        title = str(entry.get("title") or "")
                        counter.update(_extract_keywords(title, self._trends_settings))
                except Exception:
                    self._log.exception(
                        "internet_scoring.google_trends_fetch_failed",
                        feed_url=feed_url,
                    )
        boosts: dict[str, float] = {}
        for keyword, raw_count in counter.most_common(self._settings.max_signal_keywords):
            boost = min(
                float(raw_count) * self._settings.google_trends_token_weight,
                self._settings.max_signal_boost_per_keyword,
            )
            if boost <= 0:
                continue
            boosts[keyword] = boost
        return boosts


def _extract_keywords(text: str, trends_settings: TrendsSettings) -> list[str]:
    tokens = [item.strip() for item in re.split(r"[^\w+#-]+", text) if item.strip()]
    result: list[str] = []
    for token in tokens:
        normalized = _normalize_keyword(token)
        if not normalized:
            continue
        if normalized in _STOPWORDS:
            continue
        if len(normalized) < trends_settings.min_keyword_length:
            continue
        if len(normalized) > trends_settings.max_keyword_length:
            continue
        if normalized.isdigit():
            continue
        result.append(normalized)
    return result


def _normalize_keyword(value: str) -> str:
    normalized = _compact(value).lower().lstrip("#")
    if not normalized:
        return ""
    if normalized.startswith("http"):
        return ""
    return normalized


def _merge_boosts(*, target: dict[str, float], updates: dict[str, float], cap: float) -> None:
    for key, value in updates.items():
        current = float(target.get(key, 0.0))
        target[key] = min(current + float(value), cap)


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())
