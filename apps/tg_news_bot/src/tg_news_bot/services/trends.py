"""Trend signal collection and scoring influence."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

import feedparser
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_news_bot.config import TrendsSettings
from tg_news_bot.db.models import TrendSignalSource
from tg_news_bot.logging import get_logger
from tg_news_bot.repositories.trend_signals import TrendSignalInput, TrendSignalRepository
from tg_news_bot.services.metrics import metrics


_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "over",
    "under",
    "about",
    "after",
    "before",
    "study",
    "new",
    "news",
    "report",
    "show",
    "shows",
    "using",
    "use",
    "based",
    "have",
    "has",
    "had",
    "will",
    "would",
    "could",
}


@dataclass(slots=True)
class TrendCollectionStats:
    inserted: int = 0
    sources_ok: int = 0
    sources_failed: int = 0
    keywords_total: int = 0


class TrendCollector:
    def __init__(
        self,
        *,
        settings: TrendsSettings,
        session_factory: async_sessionmaker[AsyncSession],
        repository: TrendSignalRepository | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._repo = repository or TrendSignalRepository()
        self._log = get_logger(__name__)
        self._cached_boosts: dict[str, float] = {}
        self._cached_at: datetime | None = None

    async def run(self) -> None:
        while True:
            try:
                await self.collect_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.exception("trends.collect_loop_error")
            await asyncio.sleep(self._settings.collect_interval_seconds)

    async def collect_once(self) -> TrendCollectionStats:
        if not self._settings.enabled:
            return TrendCollectionStats()

        counters: list[tuple[TrendSignalSource, Counter[str]]] = []
        stats = TrendCollectionStats()
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
            arxiv_counter = await self._collect_arxiv(http)
            counters.append((TrendSignalSource.ARXIV, arxiv_counter))
            stats.sources_ok += 1

            hn_counter = await self._collect_hn(http)
            counters.append((TrendSignalSource.HN, hn_counter))
            stats.sources_ok += 1

            reddit_counter = await self._collect_reddit(http)
            counters.append((TrendSignalSource.REDDIT, reddit_counter))
            stats.sources_ok += 1

            x_counter = await self._collect_x(http)
            counters.append((TrendSignalSource.X, x_counter))
            stats.sources_ok += 1

        now = datetime.now(timezone.utc)
        to_insert: list[TrendSignalInput] = []
        for source, counter in counters:
            for keyword, raw_weight in counter.most_common(self._settings.max_keywords):
                weight = min(float(raw_weight) / 10.0, self._settings.max_boost_per_keyword)
                to_insert.append(
                    TrendSignalInput(
                        source=source,
                        keyword=keyword,
                        weight=weight,
                        observed_at=now,
                        meta={"raw_weight": float(raw_weight)},
                    )
                )
        stats.keywords_total = len(to_insert)
        if to_insert:
            async with self._session_factory() as session:
                async with session.begin():
                    stats.inserted = await self._repo.add_many(session, items=to_insert)
                    before = now - timedelta(hours=self._settings.lookback_hours * 3)
                    await self._repo.delete_older_than(session, before=before)
        metrics.inc_counter("trends_collect_total")
        metrics.set_gauge("trends_keywords_total", float(stats.keywords_total))
        self._cached_at = None
        return stats

    async def get_keyword_boosts(self, *, max_items: int = 80) -> dict[str, float]:
        if not self._settings.enabled:
            return {}
        now = datetime.now(timezone.utc)
        if self._cached_at and (now - self._cached_at).total_seconds() < 120:
            return dict(self._cached_boosts)

        since = now - timedelta(hours=self._settings.lookback_hours)
        async with self._session_factory() as session:
            async with session.begin():
                rows = await self._repo.list_recent_keyword_scores(
                    session,
                    since=since,
                    limit=max_items,
                )

        boosts: dict[str, float] = {}
        for keyword, weight_sum in rows:
            boost = min(weight_sum / 10.0, self._settings.max_boost_per_keyword)
            if boost <= 0:
                continue
            boosts[keyword] = boost
        self._cached_boosts = boosts
        self._cached_at = now
        return dict(boosts)

    async def list_recent_signals(self, *, hours: int, limit: int) -> list[tuple[str, str, float, datetime]]:
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=hours)
        async with self._session_factory() as session:
            async with session.begin():
                rows = await self._repo.list_recent(session, since=since, limit=limit)
        return [
            (row.source.value, row.keyword, float(row.weight), row.observed_at)
            for row in rows
        ]

    async def _collect_arxiv(self, http: httpx.AsyncClient) -> Counter[str]:
        counter: Counter[str] = Counter()
        for url in self._settings.arxiv_feeds:
            try:
                response = await http.get(url)
                response.raise_for_status()
                parsed = feedparser.parse(response.text)
                for entry in parsed.entries[:60]:
                    title = str(entry.get("title") or "")
                    counter.update(_extract_keywords(title, self._settings))
            except Exception:
                self._log.exception("trends.arxiv_fetch_failed", url=url)
        return counter

    async def _collect_hn(self, http: httpx.AsyncClient) -> Counter[str]:
        counter: Counter[str] = Counter()
        try:
            top = await http.get("https://hacker-news.firebaseio.com/v0/topstories.json")
            top.raise_for_status()
            story_ids = list(top.json() or [])[: self._settings.hn_top_n]
            for story_id in story_ids:
                try:
                    item = await http.get(
                        f"https://hacker-news.firebaseio.com/v0/item/{int(story_id)}.json"
                    )
                    item.raise_for_status()
                    payload = item.json() or {}
                    title = str(payload.get("title") or "")
                    counter.update(_extract_keywords(title, self._settings))
                except Exception:
                    continue
        except Exception:
            self._log.exception("trends.hn_fetch_failed")
        return counter

    async def _collect_reddit(self, http: httpx.AsyncClient) -> Counter[str]:
        counter: Counter[str] = Counter()
        headers = {"User-Agent": "tg-news-bot/1.0"}
        for url in self._settings.reddit_feeds:
            try:
                response = await http.get(url, headers=headers)
                response.raise_for_status()
                payload = response.json() or {}
                children = (
                    payload.get("data", {}).get("children", [])
                    if isinstance(payload, dict)
                    else []
                )
                for row in children[:80]:
                    data = row.get("data", {}) if isinstance(row, dict) else {}
                    title = str(data.get("title") or "")
                    counter.update(_extract_keywords(title, self._settings))
            except Exception:
                self._log.exception("trends.reddit_fetch_failed", url=url)
        return counter

    async def _collect_x(self, http: httpx.AsyncClient) -> Counter[str]:
        counter: Counter[str] = Counter()
        for url in self._settings.x_feeds:
            try:
                response = await http.get(url)
                response.raise_for_status()
                parsed = feedparser.parse(response.text)
                for entry in parsed.entries[:60]:
                    title = str(entry.get("title") or "")
                    counter.update(_extract_keywords(title, self._settings))
            except Exception:
                self._log.exception("trends.x_fetch_failed", url=url)
        return counter


def _extract_keywords(text: str, settings: TrendsSettings) -> list[str]:
    tokens = [item.strip().lower() for item in re.split(r"[^a-zA-Z0-9+#-]+", text) if item.strip()]
    result: list[str] = []
    for token in tokens:
        if token in _STOPWORDS:
            continue
        if len(token) < settings.min_keyword_length:
            continue
        if len(token) > settings.max_keyword_length:
            continue
        if token.startswith("http"):
            continue
        if token.isdigit():
            continue
        result.append(token)
    return result
