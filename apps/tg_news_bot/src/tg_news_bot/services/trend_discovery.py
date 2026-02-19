"""Trend discovery service with moderation-ready candidates."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import html
import re
from urllib.parse import urlparse

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_publisher import ButtonSpec, keyboard_from_specs
from tg_news_bot.config import Settings, TrendDiscoveryProfileSettings
from tg_news_bot.db.models import (
    BotSettings,
    Source,
    TrendArticleCandidate,
    TrendCandidateStatus,
    TrendSourceCandidate,
    TrendTopic,
)
from tg_news_bot.logging import get_logger
from tg_news_bot.ports.publisher import (
    PublisherEditNotAllowed,
    PublisherNotFound,
    PublisherNotModified,
    PublisherPort,
)
from tg_news_bot.repositories.bot_settings import BotSettingsRepository
from tg_news_bot.repositories.sources import SourceRepository
from tg_news_bot.repositories.trend_candidates import (
    TrendArticleCandidateInput,
    TrendCandidateRepository,
    TrendSourceCandidateInput,
    TrendTopicInput,
)
from tg_news_bot.repositories.trend_topic_profiles import (
    TrendTopicProfileInput,
    TrendTopicProfileRepository,
)
from tg_news_bot.services.text_generation import OpenAICompatClient
from tg_news_bot.utils.url import extract_domain, normalize_url

if False:  # pragma: no cover
    from tg_news_bot.services.ingestion import IngestionRunner


_SOURCE_WEIGHTS = {
    "ARXIV": 1.25,
    "HN": 1.0,
    "REDDIT": 0.9,
    "X": 0.8,
}


@dataclass(slots=True)
class NetworkTrendItem:
    title: str
    url: str
    normalized_url: str
    domain: str
    summary: str
    source_name: str
    source_ref: str | None
    observed_at: datetime


@dataclass(slots=True)
class ProfileMatchedItem:
    item: NetworkTrendItem
    score: float
    seed_hits: list[str]
    exclude_hits: list[str]
    trust_boost: float


@dataclass(slots=True)
class TrendScanResult:
    mode: str
    scanned_items: int
    topics_created: int
    article_candidates: int
    source_candidates: int
    announced_messages: int
    auto_ingested: int
    auto_sources_added: int


@dataclass(slots=True)
class TrendCandidateActionResult:
    ok: bool
    message: str
    draft_id: int | None = None
    source_id: int | None = None


class TrendDiscoveryService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: PublisherPort | None = None,
        ingestion_runner: "IngestionRunner | None" = None,
        bot_settings_repo: BotSettingsRepository | None = None,
        profiles_repo: TrendTopicProfileRepository | None = None,
        candidates_repo: TrendCandidateRepository | None = None,
        sources_repo: SourceRepository | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._publisher = publisher
        self._ingestion_runner = ingestion_runner
        self._bot_settings_repo = bot_settings_repo or BotSettingsRepository()
        self._profiles_repo = profiles_repo or TrendTopicProfileRepository()
        self._candidates_repo = candidates_repo or TrendCandidateRepository()
        self._sources_repo = sources_repo or SourceRepository()
        self._log = get_logger(__name__)
        self._llm_client = self._build_llm_client()

    def _build_llm_client(self) -> OpenAICompatClient | None:
        discovery = self._settings.trend_discovery
        llm = self._settings.llm
        if not discovery.ai_enrichment:
            return None
        if not llm.enabled or llm.provider != "openai_compat" or not llm.api_key:
            return None
        return OpenAICompatClient(
            api_key=llm.api_key,
            base_url=llm.base_url,
            model=llm.model,
            timeout_seconds=llm.timeout_seconds,
            temperature=llm.temperature,
            max_retries=llm.max_retries,
            retry_backoff_seconds=llm.retry_backoff_seconds,
            circuit_breaker_threshold=llm.circuit_breaker_threshold,
            circuit_breaker_cooldown_seconds=llm.circuit_breaker_cooldown_seconds,
        )

    async def ensure_default_profiles(self) -> int:
        defaults = self._settings.trend_discovery.profiles
        created = 0
        if not defaults:
            return created
        async with self._session_factory() as session:
            async with session.begin():
                existing = await self._profiles_repo.list_all(session)
                existing_names = {item.name.lower() for item in existing}
                for profile in defaults:
                    if profile.name.lower() in existing_names:
                        continue
                    await self._profiles_repo.create(
                        session,
                        payload=TrendTopicProfileInput(
                            name=profile.name,
                            enabled=profile.enabled,
                            seed_keywords=list(profile.seed_keywords),
                            exclude_keywords=list(profile.exclude_keywords),
                            trusted_domains=list(profile.trusted_domains),
                            min_article_score=float(profile.min_article_score),
                            tags={"seeded": True},
                        ),
                    )
                    created += 1
        return created

    async def scan(
        self,
        *,
        hours: int | None = None,
        limit: int | None = None,
    ) -> TrendScanResult:
        discovery = self._settings.trend_discovery
        mode = discovery.mode
        if not discovery.enabled or mode == "off":
            return TrendScanResult(
                mode=mode,
                scanned_items=0,
                topics_created=0,
                article_candidates=0,
                source_candidates=0,
                announced_messages=0,
                auto_ingested=0,
                auto_sources_added=0,
            )

        await self.ensure_default_profiles()
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=self._clamp_hours(hours))
        topic_limit = self._clamp_topic_limit(limit)
        items = await self._collect_network_items(since=since)

        if not items:
            return TrendScanResult(
                mode=mode,
                scanned_items=0,
                topics_created=0,
                article_candidates=0,
                source_candidates=0,
                announced_messages=0,
                auto_ingested=0,
                auto_sources_added=0,
            )

        topic_ids: list[int] = []
        article_ids: list[int] = []
        source_ids: list[int] = []

        async with self._session_factory() as session:
            async with session.begin():
                profiles = await self._profiles_repo.list_enabled(session)
                if not profiles:
                    return TrendScanResult(
                        mode=mode,
                        scanned_items=len(items),
                        topics_created=0,
                        article_candidates=0,
                        source_candidates=0,
                        announced_messages=0,
                        auto_ingested=0,
                        auto_sources_added=0,
                    )

                existing_sources = await self._sources_repo.list_all(session)
                trust_by_domain = self._build_trust_by_domain(existing_sources)
                known_domains = set(trust_by_domain.keys())

                topic_candidates = await self._build_topic_candidates(
                    items=items,
                    profiles=profiles,
                    trust_by_domain=trust_by_domain,
                    topic_limit=topic_limit,
                )

                for profile, matched, topic_name, topic_score, confidence, reasons in topic_candidates:
                    topic = await self._candidates_repo.create_topic(
                        session,
                        payload=TrendTopicInput(
                            profile_id=profile.id,
                            topic_name=topic_name,
                            topic_slug=_slug(topic_name),
                            trend_score=topic_score,
                            confidence=confidence,
                            reasons=reasons,
                            discovered_at=now,
                        ),
                    )
                    topic_ids.append(topic.id)

                    min_article_score = float(profile.min_article_score or 0.0)
                    for row in matched[: discovery.max_articles_per_topic]:
                        if row.score < min_article_score:
                            continue
                        candidate = await self._candidates_repo.create_or_update_article_candidate(
                            session,
                            payload=TrendArticleCandidateInput(
                                topic_id=topic.id,
                                title=row.item.title,
                                url=row.item.url,
                                normalized_url=row.item.normalized_url,
                                domain=row.item.domain,
                                snippet=_trim(row.item.summary, discovery.article_snippet_chars),
                                score=row.score,
                                reasons={
                                    "seed_hits": row.seed_hits,
                                    "exclude_hits": row.exclude_hits,
                                    "trust_boost": round(row.trust_boost, 3),
                                    "source": row.item.source_name,
                                },
                                source_name=row.item.source_name,
                                source_ref=row.item.source_ref,
                            ),
                        )
                        article_ids.append(candidate.id)

                    source_candidates = self._build_source_candidates(
                        matched_items=matched,
                        known_domains=known_domains,
                        max_items=discovery.max_sources_per_topic,
                    )
                    for source_payload in source_candidates:
                        source_candidate = await self._candidates_repo.create_or_update_source_candidate(
                            session,
                            payload=TrendSourceCandidateInput(
                                topic_id=topic.id,
                                domain=source_payload["domain"],
                                source_url=source_payload["source_url"],
                                score=source_payload["score"],
                                reasons=source_payload["reasons"],
                            ),
                        )
                        source_ids.append(source_candidate.id)

        announced = await self.publish_pending_candidates(topic_ids=topic_ids)

        auto_ingested = 0
        auto_sources_added = 0
        if mode == "auto":
            for candidate_id in article_ids:
                result = await self.ingest_article_candidate(
                    candidate_id=candidate_id,
                    user_id=0,
                    min_score=discovery.auto_ingest_min_score,
                )
                if result.ok and result.draft_id is not None:
                    auto_ingested += 1
            for candidate_id in source_ids:
                result = await self.add_source_candidate(
                    candidate_id=candidate_id,
                    user_id=0,
                    min_score=discovery.auto_add_source_min_score,
                )
                if result.ok and result.source_id is not None:
                    auto_sources_added += 1

        return TrendScanResult(
            mode=mode,
            scanned_items=len(items),
            topics_created=len(topic_ids),
            article_candidates=len(article_ids),
            source_candidates=len(source_ids),
            announced_messages=announced,
            auto_ingested=auto_ingested,
            auto_sources_added=auto_sources_added,
        )

    async def list_topics(self, *, hours: int, limit: int) -> list[TrendTopic]:
        since = datetime.now(timezone.utc) - timedelta(hours=self._clamp_hours(hours))
        async with self._session_factory() as session:
            async with session.begin():
                return await self._candidates_repo.list_topics_since(
                    session,
                    since=since,
                    limit=min(max(limit, 1), self._settings.trend_discovery.max_topic_limit),
                )

    async def list_articles(self, *, topic_id: int, limit: int) -> list[TrendArticleCandidate]:
        async with self._session_factory() as session:
            async with session.begin():
                return await self._candidates_repo.list_article_candidates(
                    session,
                    topic_id=topic_id,
                    limit=min(max(limit, 1), 50),
                )

    async def list_sources(self, *, topic_id: int, limit: int) -> list[TrendSourceCandidate]:
        async with self._session_factory() as session:
            async with session.begin():
                return await self._candidates_repo.list_source_candidates(
                    session,
                    topic_id=topic_id,
                    limit=min(max(limit, 1), 50),
                )

    async def ingest_article_candidate(
        self,
        *,
        candidate_id: int,
        user_id: int,
        min_score: float | None = None,
    ) -> TrendCandidateActionResult:
        if self._ingestion_runner is None:
            return TrendCandidateActionResult(False, "Ingestion runner недоступен.")

        async with self._session_factory() as session:
            async with session.begin():
                candidate = await self._candidates_repo.get_article_candidate(session, candidate_id)
                if candidate is None:
                    return TrendCandidateActionResult(False, f"Кандидат статьи #{candidate_id} не найден.")
                if candidate.status == TrendCandidateStatus.REJECTED:
                    return TrendCandidateActionResult(False, "Кандидат уже отклонён.")
                if candidate.status == TrendCandidateStatus.INGESTED and candidate.draft_id is not None:
                    return TrendCandidateActionResult(
                        True,
                        f"Уже загружено ранее: Draft #{candidate.draft_id}",
                        draft_id=candidate.draft_id,
                    )
                if min_score is not None and float(candidate.score) < float(min_score):
                    return TrendCandidateActionResult(
                        False,
                        f"Score {candidate.score:.2f} ниже порога {float(min_score):.2f}.",
                    )
                url = candidate.url

        ingest_result = await self._ingestion_runner.ingest_url(url=url)
        reviewed_at = datetime.now(timezone.utc)

        async with self._session_factory() as session:
            async with session.begin():
                candidate = await self._candidates_repo.get_article_candidate(session, candidate_id)
                if candidate is None:
                    return TrendCandidateActionResult(False, f"Кандидат статьи #{candidate_id} не найден.")
                candidate.reviewed_by_user_id = user_id if user_id > 0 else None
                candidate.reviewed_at = reviewed_at
                if ingest_result.created:
                    candidate.status = TrendCandidateStatus.INGESTED
                    candidate.draft_id = ingest_result.draft_id
                    await session.flush()
                    await self._clear_candidate_keyboard(candidate.group_chat_id, candidate.message_id)
                    if ingest_result.draft_id is None:
                        return TrendCandidateActionResult(True, "Статья отправлена во Входящие.")
                    return TrendCandidateActionResult(
                        True,
                        f"Статья отправлена во Входящие как Draft #{ingest_result.draft_id}",
                        draft_id=ingest_result.draft_id,
                    )
                candidate.status = TrendCandidateStatus.FAILED
                reasons = candidate.reasons if isinstance(candidate.reasons, dict) else {}
                reasons["last_ingest_reason"] = ingest_result.reason
                candidate.reasons = reasons
                await session.flush()
                await self._clear_candidate_keyboard(candidate.group_chat_id, candidate.message_id)

        return TrendCandidateActionResult(
            False,
            f"Не удалось загрузить статью: {ingest_result.reason or 'unknown'}",
        )

    async def reject_article_candidate(self, *, candidate_id: int, user_id: int) -> TrendCandidateActionResult:
        reviewed_at = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            async with session.begin():
                candidate = await self._candidates_repo.get_article_candidate(session, candidate_id)
                if candidate is None:
                    return TrendCandidateActionResult(False, f"Кандидат статьи #{candidate_id} не найден.")
                if candidate.status == TrendCandidateStatus.REJECTED:
                    return TrendCandidateActionResult(True, "Кандидат уже отклонён.")
                candidate.status = TrendCandidateStatus.REJECTED
                candidate.reviewed_by_user_id = user_id if user_id > 0 else None
                candidate.reviewed_at = reviewed_at
                await session.flush()
                await self._clear_candidate_keyboard(candidate.group_chat_id, candidate.message_id)
        return TrendCandidateActionResult(True, f"Кандидат статьи #{candidate_id} отклонён.")

    async def add_source_candidate(
        self,
        *,
        candidate_id: int,
        user_id: int,
        min_score: float | None = None,
    ) -> TrendCandidateActionResult:
        reviewed_at = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            async with session.begin():
                candidate = await self._candidates_repo.get_source_candidate(session, candidate_id)
                if candidate is None:
                    return TrendCandidateActionResult(False, f"Кандидат источника #{candidate_id} не найден.")
                if candidate.status == TrendCandidateStatus.REJECTED:
                    return TrendCandidateActionResult(False, "Кандидат уже отклонён.")
                if candidate.status == TrendCandidateStatus.APPROVED and candidate.source_id is not None:
                    return TrendCandidateActionResult(
                        True,
                        f"Источник уже добавлен: #{candidate.source_id}",
                        source_id=candidate.source_id,
                    )
                if min_score is not None and float(candidate.score) < float(min_score):
                    return TrendCandidateActionResult(
                        False,
                        f"Score {candidate.score:.2f} ниже порога {float(min_score):.2f}.",
                    )

                source_url = self._normalize_source_url(candidate.source_url or f"https://{candidate.domain}")
                existing = await self._sources_repo.get_by_url(session, source_url)
                if existing is None:
                    existing = await self._find_source_by_domain(session, candidate.domain)

                created = False
                if existing is None:
                    existing = await self._sources_repo.create(
                        session,
                        name=f"Trend: {candidate.domain}",
                        url=source_url,
                        enabled=False,
                        tags={
                            "trend_candidate": {
                                "candidate_id": candidate.id,
                                "topic_id": candidate.topic_id,
                                "created_at": reviewed_at.isoformat(),
                            }
                        },
                    )
                    created = True

                candidate.status = TrendCandidateStatus.APPROVED
                candidate.source_id = existing.id
                candidate.reviewed_by_user_id = user_id if user_id > 0 else None
                candidate.reviewed_at = reviewed_at
                await session.flush()
                await self._clear_candidate_keyboard(candidate.group_chat_id, candidate.message_id)

        if created:
            return TrendCandidateActionResult(
                True,
                f"Источник добавлен (disabled): #{existing.id}",
                source_id=existing.id,
            )
        return TrendCandidateActionResult(
            True,
            f"Источник уже существует: #{existing.id}",
            source_id=existing.id,
        )

    async def reject_source_candidate(self, *, candidate_id: int, user_id: int) -> TrendCandidateActionResult:
        reviewed_at = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            async with session.begin():
                candidate = await self._candidates_repo.get_source_candidate(session, candidate_id)
                if candidate is None:
                    return TrendCandidateActionResult(False, f"Кандидат источника #{candidate_id} не найден.")
                if candidate.status == TrendCandidateStatus.REJECTED:
                    return TrendCandidateActionResult(True, "Кандидат уже отклонён.")
                candidate.status = TrendCandidateStatus.REJECTED
                candidate.reviewed_by_user_id = user_id if user_id > 0 else None
                candidate.reviewed_at = reviewed_at
                await session.flush()
                await self._clear_candidate_keyboard(candidate.group_chat_id, candidate.message_id)
        return TrendCandidateActionResult(True, f"Кандидат источника #{candidate_id} отклонён.")

    async def publish_pending_candidates(self, *, topic_ids: list[int]) -> int:
        if not topic_ids or self._publisher is None:
            return 0

        settings = await self._load_bot_settings()
        if settings is None or settings.group_chat_id is None:
            return 0
        topic_id = settings.trend_candidates_topic_id or settings.inbox_topic_id
        if topic_id is None:
            return 0

        sent = 0
        async with self._session_factory() as session:
            async with session.begin():
                article_rows = list(
                    (
                        await session.execute(
                            select(TrendArticleCandidate)
                            .where(TrendArticleCandidate.topic_id.in_(topic_ids))
                            .where(TrendArticleCandidate.status == TrendCandidateStatus.PENDING)
                            .where(TrendArticleCandidate.message_id.is_(None))
                            .order_by(TrendArticleCandidate.score.desc(), TrendArticleCandidate.id.asc())
                        )
                    ).scalars().all()
                )
                source_rows = list(
                    (
                        await session.execute(
                            select(TrendSourceCandidate)
                            .where(TrendSourceCandidate.topic_id.in_(topic_ids))
                            .where(TrendSourceCandidate.status == TrendCandidateStatus.PENDING)
                            .where(TrendSourceCandidate.message_id.is_(None))
                            .order_by(TrendSourceCandidate.score.desc(), TrendSourceCandidate.id.asc())
                        )
                    ).scalars().all()
                )

        for row in article_rows:
            keyboard = keyboard_from_specs(
                [
                    [
                        ButtonSpec(text="Добавить во входящие", callback_data=f"trend:article:{row.id}:ingest"),
                        ButtonSpec(text="Отклонить", callback_data=f"trend:article:{row.id}:reject"),
                    ],
                    [ButtonSpec(text="Открыть статью", url=row.url)],
                ]
            )
            try:
                result = await self._publisher.send_text(
                    chat_id=settings.group_chat_id,
                    topic_id=topic_id,
                    text=self._render_article_card(row),
                    keyboard=keyboard,
                    parse_mode="HTML",
                )
            except Exception:
                self._log.exception("trend_discovery.publish_article_candidate_failed", candidate_id=row.id)
                continue
            sent += 1
            async with self._session_factory() as session:
                async with session.begin():
                    current = await self._candidates_repo.get_article_candidate(session, row.id)
                    if current is None:
                        continue
                    current.group_chat_id = result.chat_id
                    current.topic_id_telegram = topic_id
                    current.message_id = result.message_id
                    await session.flush()

        for row in source_rows:
            source_url = self._normalize_source_url(row.source_url or f"https://{row.domain}")
            keyboard = keyboard_from_specs(
                [
                    [
                        ButtonSpec(text="Добавить источник", callback_data=f"trend:source:{row.id}:add"),
                        ButtonSpec(text="Отклонить", callback_data=f"trend:source:{row.id}:reject"),
                    ],
                    [ButtonSpec(text="Открыть сайт", url=source_url)],
                ]
            )
            try:
                result = await self._publisher.send_text(
                    chat_id=settings.group_chat_id,
                    topic_id=topic_id,
                    text=self._render_source_card(row),
                    keyboard=keyboard,
                    parse_mode="HTML",
                )
            except Exception:
                self._log.exception("trend_discovery.publish_source_candidate_failed", candidate_id=row.id)
                continue
            sent += 1
            async with self._session_factory() as session:
                async with session.begin():
                    current = await self._candidates_repo.get_source_candidate(session, row.id)
                    if current is None:
                        continue
                    current.group_chat_id = result.chat_id
                    current.topic_id_telegram = topic_id
                    current.message_id = result.message_id
                    await session.flush()

        return sent

    async def _collect_network_items(self, *, since: datetime) -> list[NetworkTrendItem]:
        limit = self._settings.trend_discovery.item_limit_per_source
        rows: list[NetworkTrendItem] = []
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
            rows.extend(await self._collect_arxiv(http, since, limit))
            rows.extend(await self._collect_hn(http, since, limit))
            rows.extend(await self._collect_reddit(http, since, limit))
            rows.extend(await self._collect_x(http, since, limit))

        dedup: dict[str, NetworkTrendItem] = {}
        for item in rows:
            current = dedup.get(item.normalized_url)
            if current is None or item.observed_at > current.observed_at:
                dedup[item.normalized_url] = item
        return list(dedup.values())

    async def _collect_arxiv(
        self,
        http: httpx.AsyncClient,
        since: datetime,
        limit: int,
    ) -> list[NetworkTrendItem]:
        items: list[NetworkTrendItem] = []
        feeds = list(self._settings.trends.arxiv_feeds)
        per_feed = max(5, limit // max(len(feeds), 1))
        for feed_url in feeds:
            try:
                response = await http.get(feed_url)
                response.raise_for_status()
                parsed = feedparser.parse(response.text)
                for entry in parsed.entries[:per_feed]:
                    observed = self._extract_observed(entry, datetime.now(timezone.utc))
                    if observed < since:
                        continue
                    built = self._build_item(
                        source_name="ARXIV",
                        source_ref=feed_url,
                        title=str(entry.get("title") or ""),
                        url=str(entry.get("link") or ""),
                        summary=str(entry.get("summary") or ""),
                        observed=observed,
                    )
                    if built:
                        items.append(built)
            except Exception:
                self._log.exception("trend_discovery.arxiv_fetch_failed", feed_url=feed_url)
        return items

    async def _collect_hn(
        self,
        http: httpx.AsyncClient,
        since: datetime,
        limit: int,
    ) -> list[NetworkTrendItem]:
        items: list[NetworkTrendItem] = []
        try:
            response = await http.get("https://hacker-news.firebaseio.com/v0/topstories.json")
            response.raise_for_status()
            story_ids = list(response.json() or [])[:limit]
            for story_id in story_ids:
                try:
                    row = await http.get(f"https://hacker-news.firebaseio.com/v0/item/{int(story_id)}.json")
                    row.raise_for_status()
                    payload = row.json() or {}
                    observed = datetime.fromtimestamp(
                        int(payload.get("time") or int(datetime.now(timezone.utc).timestamp())),
                        tz=timezone.utc,
                    )
                    if observed < since:
                        continue
                    built = self._build_item(
                        source_name="HN",
                        source_ref="https://news.ycombinator.com/",
                        title=str(payload.get("title") or ""),
                        url=str(payload.get("url") or f"https://news.ycombinator.com/item?id={int(story_id)}"),
                        summary=str(payload.get("text") or ""),
                        observed=observed,
                    )
                    if built:
                        items.append(built)
                except Exception:
                    continue
        except Exception:
            self._log.exception("trend_discovery.hn_fetch_failed")
        return items

    async def _collect_reddit(
        self,
        http: httpx.AsyncClient,
        since: datetime,
        limit: int,
    ) -> list[NetworkTrendItem]:
        items: list[NetworkTrendItem] = []
        feeds = list(self._settings.trends.reddit_feeds)
        per_feed = max(5, limit // max(len(feeds), 1))
        headers = {"User-Agent": "tg-news-bot/1.0"}
        for feed_url in feeds:
            try:
                response = await http.get(feed_url, headers=headers)
                response.raise_for_status()
                payload = response.json() or {}
                children = payload.get("data", {}).get("children", [])
                if not isinstance(children, list):
                    continue
                for child in children[:per_feed]:
                    data = child.get("data", {}) if isinstance(child, dict) else {}
                    observed = datetime.fromtimestamp(
                        int(data.get("created_utc") or int(datetime.now(timezone.utc).timestamp())),
                        tz=timezone.utc,
                    )
                    if observed < since:
                        continue
                    built = self._build_item(
                        source_name="REDDIT",
                        source_ref=feed_url,
                        title=str(data.get("title") or ""),
                        url=str(data.get("url") or ""),
                        summary=str(data.get("selftext") or ""),
                        observed=observed,
                    )
                    if built:
                        items.append(built)
            except Exception:
                self._log.exception("trend_discovery.reddit_fetch_failed", feed_url=feed_url)
        return items

    async def _collect_x(
        self,
        http: httpx.AsyncClient,
        since: datetime,
        limit: int,
    ) -> list[NetworkTrendItem]:
        feeds = list(self._settings.trends.x_feeds)
        if not feeds:
            return []
        items: list[NetworkTrendItem] = []
        per_feed = max(5, limit // max(len(feeds), 1))
        for feed_url in feeds:
            try:
                response = await http.get(feed_url)
                response.raise_for_status()
                parsed = feedparser.parse(response.text)
                for entry in parsed.entries[:per_feed]:
                    observed = self._extract_observed(entry, datetime.now(timezone.utc))
                    if observed < since:
                        continue
                    built = self._build_item(
                        source_name="X",
                        source_ref=feed_url,
                        title=str(entry.get("title") or ""),
                        url=str(entry.get("link") or ""),
                        summary=str(entry.get("summary") or ""),
                        observed=observed,
                    )
                    if built:
                        items.append(built)
            except Exception:
                self._log.exception("trend_discovery.x_fetch_failed", feed_url=feed_url)
        return items

    async def _build_topic_candidates(
        self,
        *,
        items: list[NetworkTrendItem],
        profiles: list,
        trust_by_domain: dict[str, float],
        topic_limit: int,
    ) -> list[tuple[object, list[ProfileMatchedItem], str, float, float, dict]]:
        rows: list[tuple[object, list[ProfileMatchedItem], str, float, float, dict]] = []
        for profile in profiles:
            config = TrendDiscoveryProfileSettings(
                name=profile.name,
                seed_keywords=_normalize_keywords(profile.seed_keywords),
                exclude_keywords=_normalize_keywords(profile.exclude_keywords),
                trusted_domains=_normalize_keywords(profile.trusted_domains),
                min_article_score=float(profile.min_article_score or 0.0),
                enabled=bool(profile.enabled),
            )
            matched: list[ProfileMatchedItem] = []
            for item in items:
                scored = self._score_item_for_profile(config, item, trust_by_domain)
                if scored:
                    matched.append(scored)
            if not matched:
                continue
            matched.sort(key=lambda item: item.score, reverse=True)
            unique_domains = len({row.item.domain for row in matched if row.item.domain})
            unique_sources = len({row.item.source_name for row in matched})
            topic_score = self._topic_score(matched, unique_domains, unique_sources)
            if topic_score < self._settings.trend_discovery.min_topic_score:
                continue

            ai_title = await self._ai_topic_title(profile.name, matched)
            topic_name = ai_title or self._fallback_topic_name(profile.name, matched)
            confidence = max(0.05, min(1.0, topic_score / 8.0))
            reasons = {
                "items": len(matched),
                "unique_domains": unique_domains,
                "unique_sources": unique_sources,
                "top_keywords": self._top_keywords(matched),
            }
            if ai_title:
                reasons["ai_title"] = ai_title
            rows.append((profile, matched, topic_name, topic_score, confidence, reasons))

        rows.sort(key=lambda row: row[3], reverse=True)
        return rows[:topic_limit]

    async def _ai_topic_title(self, profile_name: str, matched: list[ProfileMatchedItem]) -> str | None:
        if self._llm_client is None:
            return None
        titles = [row.item.title for row in matched if row.item.title][:8]
        if not titles:
            return None
        prompt = "\n".join(f"- {title}" for title in titles)
        try:
            response = await self._llm_client.complete(
                system_prompt=(
                    "You are a trend analyst for science and technology media. "
                    "Return one short topic title in Russian, 4-9 words. Return plain text only."
                ),
                user_prompt=f"Profile: {profile_name}\nHeadlines:\n{prompt}",
            )
        except Exception:
            self._log.exception("trend_discovery.ai_topic_title_failed", profile=profile_name)
            return None
        compact = _compact(str(response))
        if not compact:
            return None
        return _trim(compact, 90)

    def _score_item_for_profile(
        self,
        profile: TrendDiscoveryProfileSettings,
        item: NetworkTrendItem,
        trust_by_domain: dict[str, float],
    ) -> ProfileMatchedItem | None:
        text = _compact(f"{item.title} {item.summary}").lower()
        if not text:
            return None

        seeds = [keyword for keyword in profile.seed_keywords if keyword in text]
        if not seeds:
            return None

        excludes = [keyword for keyword in profile.exclude_keywords if keyword in text]
        if len(excludes) >= len(seeds):
            return None

        score = len(seeds) * 1.25 - len(excludes) * 1.5
        score += _SOURCE_WEIGHTS.get(item.source_name, 0.7)

        trusted_domains = {value.lower() for value in profile.trusted_domains}
        if item.domain.lower() in trusted_domains:
            score += 0.7

        trust_raw = trust_by_domain.get(item.domain.lower())
        trust_boost = 0.0
        if trust_raw is not None:
            trust_boost = max(min(float(trust_raw) * 0.12, 1.0), -1.0)
            score += trust_boost

        if score <= 0:
            return None

        return ProfileMatchedItem(
            item=item,
            score=score,
            seed_hits=seeds,
            exclude_hits=excludes,
            trust_boost=trust_boost,
        )

    @staticmethod
    def _topic_score(matched: list[ProfileMatchedItem], unique_domains: int, unique_sources: int) -> float:
        top_rows = matched[:15]
        if not top_rows:
            return 0.0
        avg_score = sum(min(row.score, 5.0) for row in top_rows) / float(len(top_rows))
        return avg_score + min(unique_domains, 12) * 0.2 + min(unique_sources, 4) * 0.35

    @staticmethod
    def _top_keywords(matched: list[ProfileMatchedItem], limit: int = 5) -> list[str]:
        counts: Counter[str] = Counter()
        for row in matched:
            counts.update(item.lower() for item in row.seed_hits)
        return [value for value, _ in counts.most_common(limit)]

    def _fallback_topic_name(self, profile_name: str, matched: list[ProfileMatchedItem]) -> str:
        for keyword in self._top_keywords(matched, limit=3):
            if keyword in profile_name.lower():
                continue
            return f"{profile_name}: {keyword}"
        return profile_name

    def _build_source_candidates(
        self,
        *,
        matched_items: list[ProfileMatchedItem],
        known_domains: set[str],
        max_items: int,
    ) -> list[dict]:
        grouped: dict[str, list[ProfileMatchedItem]] = defaultdict(list)
        for row in matched_items:
            domain = row.item.domain.lower()
            if not domain or domain in known_domains:
                continue
            grouped[domain].append(row)

        rows: list[dict] = []
        for domain, group in grouped.items():
            avg_score = sum(item.score for item in group) / float(len(group))
            rows.append(
                {
                    "domain": domain,
                    "source_url": self._normalize_source_url(f"https://{domain}"),
                    "score": avg_score + min(len(group), 8) * 0.25,
                    "reasons": {
                        "mentions": len(group),
                        "avg_article_score": round(avg_score, 3),
                        "sources": sorted({item.item.source_name for item in group}),
                    },
                }
            )
        rows.sort(key=lambda item: float(item["score"]), reverse=True)
        return rows[:max_items]

    async def _load_bot_settings(self) -> BotSettings | None:
        async with self._session_factory() as session:
            async with session.begin():
                return await self._bot_settings_repo.get(session)

    async def _find_source_by_domain(self, session: AsyncSession, domain: str) -> Source | None:
        rows = await self._sources_repo.list_all(session)
        target = domain.lower()
        for row in rows:
            if extract_domain(row.url).lower() == target:
                return row
        return None

    def _build_trust_by_domain(self, sources: list[Source]) -> dict[str, float]:
        result: dict[str, float] = {}
        for source in sources:
            domain = extract_domain(source.url).lower()
            if not domain:
                continue
            result[domain] = float(source.trust_score or 0.0)
        return result

    def _build_item(
        self,
        *,
        source_name: str,
        source_ref: str | None,
        title: str,
        url: str,
        summary: str,
        observed: datetime,
    ) -> NetworkTrendItem | None:
        clean_title = _compact(title)
        clean_url = _compact(url)
        if not clean_title or not clean_url:
            return None
        parsed = urlparse(clean_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        normalized = normalize_url(clean_url)
        domain = extract_domain(normalized)
        if not domain:
            return None
        return NetworkTrendItem(
            title=clean_title,
            url=clean_url,
            normalized_url=normalized,
            domain=domain,
            summary=_compact(summary),
            source_name=source_name,
            source_ref=source_ref,
            observed_at=observed,
        )

    @staticmethod
    def _extract_observed(entry, fallback: datetime) -> datetime:  # noqa: ANN001
        for key in ("published_parsed", "updated_parsed"):
            value = entry.get(key)
            if value:
                try:
                    return datetime(
                        value.tm_year,
                        value.tm_mon,
                        value.tm_mday,
                        value.tm_hour,
                        value.tm_min,
                        value.tm_sec,
                        tzinfo=timezone.utc,
                    )
                except Exception:
                    continue
        return fallback

    def _clamp_hours(self, value: int | None) -> int:
        settings = self._settings.trend_discovery
        if value is None:
            return settings.default_window_hours
        return min(max(value, 1), settings.max_window_hours)

    def _clamp_topic_limit(self, value: int | None) -> int:
        settings = self._settings.trend_discovery
        if value is None:
            return settings.default_topic_limit
        return min(max(value, 1), settings.max_topic_limit)

    @staticmethod
    def _render_article_card(row: TrendArticleCandidate) -> str:
        lines = [
            f"<b>Кандидат трендовой статьи #{row.id}</b>",
            f"topic_id: {row.topic_id}",
            f"оценка: {float(row.score):.2f}",
            f"домен: {html.escape(row.domain or '-')}",
            f"заголовок: {html.escape(_trim(row.title or '-', 180))}",
        ]
        if row.snippet:
            lines.append(f"кратко: {html.escape(_trim(row.snippet, 260))}")
        lines.append(f"ссылка: {html.escape(row.url)}")
        return "\n".join(lines)

    @staticmethod
    def _render_source_card(row: TrendSourceCandidate) -> str:
        lines = [
            f"<b>Кандидат трендового источника #{row.id}</b>",
            f"topic_id: {row.topic_id}",
            f"оценка: {float(row.score):.2f}",
            f"домен: {html.escape(row.domain)}",
        ]
        if row.source_url:
            lines.append(f"ссылка: {html.escape(row.source_url)}")
        reasons = row.reasons if isinstance(row.reasons, dict) else {}
        mentions = reasons.get("mentions")
        if mentions is not None:
            lines.append(f"упоминаний: {mentions}")
        return "\n".join(lines)

    async def _clear_candidate_keyboard(self, chat_id: int | None, message_id: int | None) -> None:
        if self._publisher is None or chat_id is None or message_id is None:
            return
        try:
            await self._publisher.edit_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                keyboard=None,
            )
        except (PublisherNotFound, PublisherEditNotAllowed, PublisherNotModified):
            return
        except Exception:
            self._log.exception("trend_discovery.clear_keyboard_failed", chat_id=chat_id, message_id=message_id)

    @staticmethod
    def _normalize_source_url(value: str) -> str:
        compact = _compact(value)
        if not compact:
            return compact
        parsed = urlparse(compact)
        if parsed.scheme:
            return compact
        return f"https://{compact}"


def _normalize_keywords(value) -> list[str]:  # noqa: ANN001
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _compact(str(item)).lower()
        if text:
            result.append(text)
    return result


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _trim(value: str, limit: int) -> str:
    text = _compact(value)
    if len(text) <= limit:
        return text
    return f"{text[: max(limit - 1, 1)].rstrip()}…"


def _slug(value: str) -> str:
    text = re.sub(r"[^0-9a-zA-Zа-яА-Я]+", "-", _compact(value).lower()).strip("-")
    return text or "topic"
