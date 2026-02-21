"""RSS ingestion pipeline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import feedparser
import httpx
from aiogram.exceptions import TelegramRetryAfter
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_publisher.types import PostContent
from tg_news_bot.config import Settings
from tg_news_bot.db.models import Article, Draft, DraftState, ImageStatus
from tg_news_bot.logging import get_logger
from tg_news_bot.monitoring import add_sentry_breadcrumb, capture_sentry_exception
from tg_news_bot.ports.publisher import PublisherPort
from tg_news_bot.repositories.articles import ArticleRepository
from tg_news_bot.repositories.bot_settings import BotSettingsRepository
from tg_news_bot.repositories.drafts import DraftRepository
from tg_news_bot.repositories.llm_cache import LLMCacheRepository
from tg_news_bot.repositories.sources import SourceRepository
from tg_news_bot.services.extraction import ArticleExtractor
from tg_news_bot.services.images import ImageSelector
from tg_news_bot.services.keyboards import build_state_keyboard
from tg_news_bot.services.metrics import metrics
from tg_news_bot.services.rendering import render_card_text, render_post_content
from tg_news_bot.services.rubricator import RubricatorService
from tg_news_bot.services.scoring import ScoringService
from tg_news_bot.services.content_safety import ContentSafetyService
from tg_news_bot.services.semantic_dedup import SemanticDedupService
from tg_news_bot.services.source_quality import SourceQualityService
from tg_news_bot.services.source_text import sanitize_source_text
from tg_news_bot.services.text_generation import (
    LLMCircuitOpenError,
    StubSummarizer,
    StubTranslator,
    compose_post_text,
    TextPipeline,
    build_text_pipeline,
)
from tg_news_bot.services.trends import TrendCollector
from tg_news_bot.utils.url import extract_domain, normalize_title_key, normalize_url


MAX_TELEGRAM_SEND_RETRIES = 3
MAX_TELEGRAM_RETRY_DELAY_SECONDS = 60


@dataclass(slots=True)
class IngestionConfig:
    poll_interval_seconds: int
    max_items_per_source: int


@dataclass(slots=True)
class IngestionStats:
    sources_total: int = 0
    entries_total: int = 0
    created: int = 0
    duplicates: int = 0
    skipped_low_score: int = 0
    skipped_invalid_entry: int = 0
    skipped_no_html: int = 0
    skipped_unsafe: int = 0
    skipped_blocked: int = 0
    skipped_rate_limited: int = 0
    rss_fetch_errors: int = 0

    def has_activity(self) -> bool:
        return any(
            (
                self.created,
                self.duplicates,
                self.skipped_low_score,
                self.skipped_invalid_entry,
                self.skipped_no_html,
                self.skipped_unsafe,
                self.skipped_blocked,
                self.skipped_rate_limited,
                self.rss_fetch_errors,
            )
        )


@dataclass(slots=True)
class ManualIngestResult:
    created: bool
    normalized_url: str | None = None
    draft_id: int | None = None
    reason: str | None = None


class IngestionRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: PublisherPort,
        config: IngestionConfig,
        trend_collector: TrendCollector | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._publisher = publisher
        self._config = config
        self._poll_lock = asyncio.Lock()
        self._source_last_poll: dict[int, datetime] = {}
        self._sources_repo = SourceRepository()
        self._draft_repo = DraftRepository()
        self._article_repo = ArticleRepository()
        self._llm_cache_repo = LLMCacheRepository()
        self._settings_repo = BotSettingsRepository()
        self._extractor = ArticleExtractor()
        self._scoring = ScoringService(settings.scoring)
        self._trend_collector = trend_collector
        self._rubricator = RubricatorService()
        self._content_safety = ContentSafetyService(settings.content_safety)
        self._source_quality = SourceQualityService(settings.source_quality)
        self._semantic_dedup = SemanticDedupService(
            settings=settings.semantic_dedup,
            session_factory=session_factory,
        )
        self._text_pipeline: TextPipeline = build_text_pipeline(
            settings.text_generation,
            settings.llm,
        )
        self._fallback_text_pipeline = TextPipeline(
            StubSummarizer(max_chars=settings.text_generation.summary_max_chars),
            StubTranslator(keep_lang_prefix=settings.text_generation.keep_lang_prefix),
        )
        self._log = get_logger(__name__)

    async def run(self) -> None:
        async with AsyncClient(follow_redirects=True, timeout=20) as http:
            while True:
                try:
                    stats = await self._poll_with_lock(http)
                    if stats.has_activity():
                        self._log.info(
                            "ingestion.poll_summary",
                            sources=stats.sources_total,
                            entries=stats.entries_total,
                            created=stats.created,
                            duplicates=stats.duplicates,
                            skipped_low_score=stats.skipped_low_score,
                            skipped_invalid_entry=stats.skipped_invalid_entry,
                            skipped_no_html=stats.skipped_no_html,
                            skipped_unsafe=stats.skipped_unsafe,
                            skipped_blocked=stats.skipped_blocked,
                            skipped_rate_limited=stats.skipped_rate_limited,
                            rss_fetch_errors=stats.rss_fetch_errors,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._log.exception("ingestion.loop_error")
                    add_sentry_breadcrumb(
                        category="ingestion",
                        message="ingestion loop error",
                        level="error",
                    )
                await asyncio.sleep(self._config.poll_interval_seconds)

    async def run_once(self, *, source_ids: set[int] | None = None) -> IngestionStats:
        async with AsyncClient(follow_redirects=True, timeout=20) as http:
            return await self._poll_with_lock(http, source_ids=source_ids)

    async def ingest_url(
        self,
        *,
        url: str,
        source_id: int | None = None,
        topic_hints: list[str] | None = None,
    ) -> ManualIngestResult:
        normalized_candidates = self._normalized_url_candidates(url, entry_id=url)
        if not normalized_candidates:
            return ManualIngestResult(created=False, reason="invalid_url")
        normalized_url = normalized_candidates[0]
        resolved_topic_hints = topic_hints or []
        source_trust_score = 0.0
        if source_id is not None:
            async with self._session_factory() as session:
                async with session.begin():
                    source = await self._sources_repo.get_by_id(session, source_id)
                    if not source:
                        return ManualIngestResult(
                            created=False,
                            normalized_url=normalized_url,
                            reason="source_not_found",
                        )
                    source_tags = source.tags if isinstance(source.tags, dict) else None
                    source_trust_score = float(source.trust_score or 0.0)
                    if not resolved_topic_hints:
                        resolved_topic_hints = self._topic_hints_from_tags(source_tags)

        stats = IngestionStats()
        entry = {"id": url, "link": url}
        trend_boosts = await self._get_trend_boosts()
        async with AsyncClient(follow_redirects=True, timeout=20) as http:
            created = await self._process_entry(
                source_id,
                entry,
                resolved_topic_hints,
                source_trust_score,
                trend_boosts,
                http,
                stats,
            )

        if not created:
            if stats.duplicates:
                reason = "duplicate"
            elif stats.skipped_blocked:
                reason = "blocked"
            elif stats.skipped_low_score:
                reason = "low_score"
            elif stats.skipped_no_html:
                reason = "no_html"
            elif stats.skipped_unsafe:
                reason = "unsafe"
            elif stats.skipped_invalid_entry:
                reason = "invalid_entry"
            else:
                reason = "not_created"
            return ManualIngestResult(
                created=False,
                normalized_url=normalized_url,
                reason=reason,
            )

        async with self._session_factory() as session:
            async with session.begin():
                draft = await self._draft_repo.get_by_normalized_url(session, normalized_url)
        return ManualIngestResult(
            created=True,
            normalized_url=normalized_url,
            draft_id=draft.id if draft else None,
        )

    async def _poll_with_lock(
        self,
        http: AsyncClient,
        *,
        source_ids: set[int] | None = None,
    ) -> IngestionStats:
        async with self._poll_lock:
            return await self._poll(http, source_ids=source_ids)

    async def _poll(
        self,
        http: AsyncClient,
        *,
        source_ids: set[int] | None = None,
    ) -> IngestionStats:
        stats = IngestionStats()
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            async with session.begin():
                drafts_cleared = await self._draft_repo.clear_expired_extracted_text(
                    session, now=now
                )
                articles_cleared = await self._article_repo.clear_expired_extracted_text(
                    session, now=now
                )
                if drafts_cleared:
                    metrics.inc_counter(
                        "extracted_text_cleanup_total",
                        value=float(drafts_cleared),
                        labels={"entity": "drafts"},
                    )
                if articles_cleared:
                    metrics.inc_counter(
                        "extracted_text_cleanup_total",
                        value=float(articles_cleared),
                        labels={"entity": "articles"},
                    )
                if drafts_cleared or articles_cleared:
                    self._log.info(
                        "ingestion.extracted_text_cleanup",
                        drafts=drafts_cleared,
                        articles=articles_cleared,
                    )
                sources = await self._sources_repo.list_enabled(session)
        if source_ids is not None:
            sources = [item for item in sources if item.id in source_ids]

        stats.sources_total = len(sources)
        if not sources:
            return stats

        for source in sources:
            now = datetime.now(timezone.utc)
            if self._is_rate_limited_source(source.id, now=now):
                stats.skipped_rate_limited += 1
                metrics.inc_counter("ingestion_sources_skipped_total", labels={"reason": "rate_limited"})
                continue
            await self._process_source(
                source.id,
                source.url,
                source.tags,
                float(source.trust_score or 0.0),
                http,
                stats,
            )
            self._source_last_poll[source.id] = now
        return stats

    async def _process_source(
        self,
        source_id: int,
        source_url: str,
        source_tags: dict | None,
        source_trust_score: float,
        http: AsyncClient,
        stats: IngestionStats,
    ) -> None:
        topic_hints = self._topic_hints_from_tags(source_tags)
        trend_boosts = await self._get_trend_boosts()
        response = await self._fetch_url_with_ssl_policy(
            source_url,
            http=http,
            source_id=source_id,
            feed_tags=source_tags,
            is_rss=True,
        )
        if response is None:
            stats.rss_fetch_errors += 1
            await self._record_source_quality_event(source_id=source_id, event="rss_http_error")
            return
        if response.status_code >= 400:
            stats.rss_fetch_errors += 1
            event_name = "rss_http_403" if response.status_code == 403 else "rss_http_error"
            await self._record_source_quality_event(
                source_id=source_id,
                event=event_name,
                details={"status": response.status_code},
            )
            self._log.warning(
                "ingestion.fetch_rss_failed",
                source_id=source_id,
                status=response.status_code,
            )
            add_sentry_breadcrumb(
                category="ingestion",
                message="rss fetch bad status",
                level="warning",
                data={"source_id": source_id, "status": response.status_code},
            )
            return

        feed = feedparser.parse(response.text)
        entries = feed.entries[: self._config.max_items_per_source]
        if not entries:
            await self._record_source_quality_event(
                source_id=source_id,
                event="rss_empty",
            )
            return

        duplicates_before = stats.duplicates
        created_local = 0
        for entry in entries:
            stats.entries_total += 1
            if await self._process_entry(
                source_id,
                entry,
                topic_hints,
                source_trust_score,
                trend_boosts,
                http,
                stats,
            ):
                stats.created += 1
                created_local += 1

        duplicates_local = max(stats.duplicates - duplicates_before, 0)
        total_local = len(entries)
        if total_local >= 8:
            duplicate_rate = duplicates_local / float(total_local)
            if duplicate_rate >= 0.85 and created_local == 0:
                await self._record_source_quality_event(
                    source_id=source_id,
                    event="high_duplicate_rate",
                    details={
                        "duplicate_rate": round(duplicate_rate, 3),
                        "entries": total_local,
                        "duplicates": duplicates_local,
                    },
                )

    async def _process_entry(
        self,
        source_id: int | None,
        entry,
        topic_hints: list[str],
        source_trust_score: float,
        trend_boosts: dict[str, float],
        http: AsyncClient,
        stats: IngestionStats,
    ) -> bool:
        link = entry.get("link") or entry.get("id")
        if not link:
            stats.skipped_invalid_entry += 1
            await self._record_source_quality_event(source_id=source_id, event="invalid_entry")
            return False

        normalized_candidates = self._normalized_url_candidates(
            link,
            entry_id=entry.get("id"),
        )
        if not normalized_candidates:
            stats.skipped_invalid_entry += 1
            await self._record_source_quality_event(source_id=source_id, event="invalid_entry")
            return False
        normalized = normalized_candidates[0]
        domain = extract_domain(normalized)
        if not domain:
            stats.skipped_invalid_entry += 1
            await self._record_source_quality_event(source_id=source_id, event="invalid_entry")
            return False
        title_en = entry.get("title")
        blocked_reason = self._blocked_reason(
            domain=domain,
            normalized_url=normalized,
            title=title_en,
        )
        if blocked_reason is not None:
            stats.skipped_blocked += 1
            metrics.inc_counter(
                "ingestion_entries_skipped_total",
                labels={"reason": "blocked"},
            )
            await self._record_source_quality_event(
                source_id=source_id,
                event="blocked",
                details={"reason": blocked_reason},
            )
            self._log.info(
                "ingestion.skipped_blocked",
                normalized_url=normalized,
                reason=blocked_reason,
            )
            return False

        title_key = normalize_title_key(title_en)
        dedup_from = datetime.now(timezone.utc) - timedelta(
            hours=self._settings.rss.dedup_title_window_hours
        )
        async with self._session_factory() as session:
            async with session.begin():
                has_duplicate_url = await self._draft_repo.exists_by_normalized_urls(
                    session,
                    normalized_candidates,
                ) or await self._article_repo.exists_by_normalized_urls(
                    session,
                    normalized_candidates,
                )
                has_duplicate_title = await self._draft_repo.exists_recent_by_domain_title(
                    session,
                    domain=domain,
                    normalized_title=title_key,
                    created_from=dedup_from,
                ) or await self._article_repo.exists_recent_by_domain_title(
                    session,
                    domain=domain,
                    normalized_title=title_key,
                    created_from=dedup_from,
                )
                if has_duplicate_url or has_duplicate_title:
                    stats.duplicates += 1
                    metrics.inc_counter(
                        "ingestion_duplicates_total",
                        labels={"reason": "url" if has_duplicate_url else "title"},
                    )
                    await self._record_source_quality_event(
                        source_id=source_id,
                        event="duplicate",
                        details={"type": "url" if has_duplicate_url else "title"},
                    )
                    return False

        html = await self._fetch_html(link, http)
        if not html:
            stats.skipped_no_html += 1
            await self._record_source_quality_event(source_id=source_id, event="no_html")
            return False

        extracted = self._extractor.extract(html)
        title_en = entry.get("title") or extracted.title
        text_en = sanitize_source_text(extracted.text)

        near_duplicate = await self._semantic_dedup.find_near_duplicate(
            normalized_url=normalized,
            domain=domain,
            title=title_en,
            text=text_en,
        )
        if near_duplicate is not None:
            stats.duplicates += 1
            metrics.inc_counter(
                "ingestion_duplicates_total",
                labels={"reason": "semantic"},
            )
            await self._record_source_quality_event(
                source_id=source_id,
                event="near_duplicate",
                details={
                    "matched_url": near_duplicate.normalized_url,
                    "similarity": round(near_duplicate.similarity, 4),
                },
            )
            return False

        safety = self._content_safety.check(text=text_en, title=title_en)
        if not safety.allowed:
            stats.skipped_unsafe += 1
            metrics.inc_counter(
                "ingestion_entries_skipped_total",
                labels={"reason": "unsafe"},
            )
            await self._record_source_quality_event(
                source_id=source_id,
                event="unsafe",
                details={"reasons": safety.reasons},
            )
            self._log.info(
                "ingestion.skipped_unsafe",
                normalized_url=normalized,
                reasons=safety.reasons,
            )
            return False

        trend_keywords = list(trend_boosts.keys())[:6]
        rubrication = self._rubricator.classify(
            title=title_en,
            text=text_en,
            trend_keywords=trend_keywords,
            hashtag_mode=self._settings.post_formatting.hashtag_mode,
        )
        effective_topic_hints = topic_hints
        if not effective_topic_hints and rubrication.topics:
            effective_topic_hints = list(rubrication.topics)

        published_at = self._parse_published(entry)
        score = self._scoring.score(
            text=text_en,
            title=title_en,
            domain=domain,
            published_at=published_at,
            trend_boosts=trend_boosts,
            source_trust_score=source_trust_score,
        )
        if score.score < self._settings.scoring.min_score:
            stats.skipped_low_score += 1
            await self._record_source_quality_event(source_id=source_id, event="low_score")
            self._log.info(
                "ingestion.skipped_low_score",
                normalized_url=normalized,
                score=score.score,
                min_score=self._settings.scoring.min_score,
            )
            return False

        score_reasons = dict(score.reasons)
        score_reasons["safety_quality"] = safety.quality_score
        if safety.reasons:
            score_reasons["safety_flags"] = safety.reasons
        if rubrication.topics:
            score_reasons["auto_topics"] = rubrication.topics
        if rubrication.hashtags:
            score_reasons["auto_hashtags"] = rubrication.hashtags

        image_selector = ImageSelector(self._settings.images, http)
        image = await image_selector.select(html, link)

        generated_title_ru: str | None = None
        generated_summary_ru: str | None = None
        if self._settings.text_generation.defer_to_editing:
            # In defer mode INBOX keeps source text; summarization/translation is triggered in EDITING.
            post_text_ru = compose_post_text(title_en, text_en or "")
            metrics.inc_counter("llm_requests_total", labels={"result": "deferred"})
        else:
            cached_title_ru: str | None = None
            cached_summary_ru: str | None = None
            async with self._session_factory() as session:
                async with session.begin():
                    cached = await self._llm_cache_repo.get_by_normalized_url(
                        session,
                        normalized,
                    )
                    if cached:
                        cached_title_ru = cached.title_ru
                        cached_summary_ru = cached.summary_ru

            if cached_summary_ru is not None:
                post_text_ru = compose_post_text(cached_title_ru, cached_summary_ru)
                metrics.inc_counter("llm_requests_total", labels={"result": "cache_hit"})
            else:
                try:
                    generated = await self._text_pipeline.generate_parts(
                        title_en=title_en,
                        text_en=text_en,
                        topic_hints=effective_topic_hints,
                    )
                    generated_title_ru = generated.title_ru
                    generated_summary_ru = generated.summary_ru
                    post_text_ru = compose_post_text(generated_title_ru, generated_summary_ru)
                    metrics.inc_counter("llm_requests_total", labels={"result": "success"})
                except LLMCircuitOpenError:
                    self._log.warning(
                        "ingestion.text_generation_circuit_open",
                        normalized_url=normalized,
                    )
                    add_sentry_breadcrumb(
                        category="ingestion",
                        message="llm circuit open",
                        level="warning",
                        data={"normalized_url": normalized},
                    )
                    metrics.inc_counter("llm_requests_total", labels={"result": "circuit_open"})
                    post_text_ru = await self._fallback_text_pipeline.generate_post(
                        title_en=title_en,
                        text_en=text_en,
                        topic_hints=effective_topic_hints,
                    )
                except Exception:
                    self._log.exception(
                        "ingestion.text_generation_failed",
                        normalized_url=normalized,
                    )
                    add_sentry_breadcrumb(
                        category="ingestion",
                        message="text generation failed",
                        level="error",
                        data={"normalized_url": normalized},
                    )
                    metrics.inc_counter("llm_requests_total", labels={"result": "failed"})
                    post_text_ru = await self._fallback_text_pipeline.generate_post(
                        title_en=title_en,
                        text_en=text_en,
                        topic_hints=effective_topic_hints,
                    )

        article = Article(
            source_id=source_id,
            url=link,
            normalized_url=normalized,
            domain=domain,
            title_en=title_en,
            published_at=published_at,
            fetched_at=datetime.now(timezone.utc),
            content_html=html,
            extracted_text=text_en,
            extracted_text_expires_at=datetime.now(timezone.utc)
            + timedelta(days=self._settings.extracted_text_ttl_days),
        )

        draft = Draft(
            state=DraftState.INBOX,
            normalized_url=normalized,
            domain=domain,
            title_en=title_en,
            source_id=source_id,
            extracted_text=text_en,
            extracted_text_expires_at=datetime.now(timezone.utc)
            + timedelta(days=self._settings.extracted_text_ttl_days),
            score=score.score,
            score_reasons=score_reasons,
            post_text_ru=post_text_ru,
            source_image_url=image.url,
            has_image=image.status == ImageStatus.OK,
            image_status=image.status,
        )

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    stored_article = await self._article_repo.get_by_normalized_url(
                        session, normalized
                    )
                    if not stored_article:
                        stored_article = await self._article_repo.create(session, article)
                    if generated_summary_ru is not None:
                        await self._llm_cache_repo.upsert(
                            session,
                            article_id=stored_article.id,
                            normalized_url=normalized,
                            provider=(
                                self._settings.llm.provider
                                if self._settings.llm.enabled
                                else "stub"
                            ),
                            model=(
                                self._settings.llm.model
                                if self._settings.llm.enabled
                                else "stub"
                            ),
                            topic_hints=effective_topic_hints,
                            title_ru=generated_title_ru,
                            summary_ru=generated_summary_ru,
                        )
                        metrics.inc_counter("llm_cache_upserts_total")
                    draft.article_id = stored_article.id
                    session.add(draft)
                    await session.flush()
                    draft_id = draft.id
        except IntegrityError:
            stats.duplicates += 1
            await self._record_source_quality_event(source_id=source_id, event="duplicate")
            return False

        self._log.info(
            "ingestion.draft_created",
            draft_id=draft_id,
            source_id=source_id,
            score=score.score,
            image_status=image.status,
        )
        await self._record_source_quality_event(source_id=source_id, event="created")
        await self._semantic_dedup.store(
            normalized_url=normalized,
            domain=domain,
            title=title_en,
            text=text_en,
        )
        metrics.inc_counter("drafts_created_total")
        metrics.inc_counter("drafts_state_total", labels={"state": DraftState.INBOX.value})
        await self._post_to_inbox(draft_id)
        return True

    async def _post_to_inbox(self, draft_id: int) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                draft = await self._draft_repo.get(session, draft_id)
                settings = await self._settings_repo.get_or_create(session)
                if not draft:
                    return
                if not settings.group_chat_id or not settings.inbox_topic_id:
                    self._log.warning("inbox_not_configured", draft_id=draft_id)
                    return

                content = render_post_content(
                    draft,
                    formatting=self._settings.post_formatting,
                )
                keyboard = build_state_keyboard(draft, DraftState.INBOX)
                card_text = render_card_text(draft)
                chat_id = settings.group_chat_id
                topic_id = settings.inbox_topic_id

        try:
            post = await self._send_post_with_retry(
                draft_id=draft_id,
                chat_id=chat_id,
                topic_id=topic_id,
                content=content,
                keyboard=keyboard,
            )
        except Exception:
            self._log.exception("ingestion.inbox_post_failed", draft_id=draft_id)
            add_sentry_breadcrumb(
                category="ingestion",
                message="inbox post failed",
                level="error",
                data={"draft_id": draft_id},
            )
            return

        try:
            card = await self._send_text_with_retry(
                draft_id=draft_id,
                chat_id=chat_id,
                topic_id=topic_id,
                text=card_text,
                keyboard=None,
                parse_mode=None,
            )
        except Exception:
            self._log.exception("ingestion.inbox_card_failed", draft_id=draft_id)
            add_sentry_breadcrumb(
                category="ingestion",
                message="inbox card failed",
                level="error",
                data={"draft_id": draft_id},
            )
            try:
                await self._publisher.delete_message(
                    chat_id=chat_id,
                    message_id=post.message_id,
                )
            except Exception as cleanup_exc:
                self._log.exception(
                    "ingestion.inbox_cleanup_failed",
                    draft_id=draft_id,
                    message_id=post.message_id,
                )
                capture_sentry_exception(
                    cleanup_exc,
                    context={"draft_id": draft_id, "message_id": post.message_id},
                )
            return

        async with self._session_factory() as session:
            async with session.begin():
                draft = await self._draft_repo.get_for_update(session, draft_id)
                if not draft:
                    return
                draft.group_chat_id = chat_id
                draft.topic_id = topic_id
                draft.post_message_id = post.message_id
                draft.card_message_id = card.message_id
                if post.photo_file_id:
                    draft.tg_image_file_id = post.photo_file_id
                    draft.tg_image_unique_id = post.photo_unique_id
                await session.flush()

    async def _send_post_with_retry(
        self,
        *,
        draft_id: int,
        chat_id: int,
        topic_id: int,
        content: PostContent,
        keyboard,
    ):
        last_error: Exception | None = None
        for attempt in range(1, MAX_TELEGRAM_SEND_RETRIES + 1):
            try:
                return await self._publisher.send_post(
                    chat_id=chat_id,
                    topic_id=topic_id,
                    content=content,
                    keyboard=keyboard,
                )
            except TelegramRetryAfter as exc:
                last_error = exc
                retry_after = float(getattr(exc, "retry_after", 1))
                wait_seconds = min(max(retry_after, 1.0), MAX_TELEGRAM_RETRY_DELAY_SECONDS)
                self._log.warning(
                    "ingestion.telegram_retry_after",
                    draft_id=draft_id,
                    method="send_post",
                    attempt=attempt,
                    wait_seconds=wait_seconds,
                )
                if attempt >= MAX_TELEGRAM_SEND_RETRIES:
                    break
                await asyncio.sleep(wait_seconds)
        if last_error:
            raise last_error
        raise RuntimeError("send_post retry loop failed unexpectedly")

    async def _send_text_with_retry(
        self,
        *,
        draft_id: int,
        chat_id: int,
        topic_id: int,
        text: str,
        keyboard,
        parse_mode: str | None,
    ):
        last_error: Exception | None = None
        for attempt in range(1, MAX_TELEGRAM_SEND_RETRIES + 1):
            try:
                return await self._publisher.send_text(
                    chat_id=chat_id,
                    topic_id=topic_id,
                    text=text,
                    keyboard=keyboard,
                    parse_mode=parse_mode,
                )
            except TelegramRetryAfter as exc:
                last_error = exc
                retry_after = float(getattr(exc, "retry_after", 1))
                wait_seconds = min(max(retry_after, 1.0), MAX_TELEGRAM_RETRY_DELAY_SECONDS)
                self._log.warning(
                    "ingestion.telegram_retry_after",
                    draft_id=draft_id,
                    method="send_text",
                    attempt=attempt,
                    wait_seconds=wait_seconds,
                )
                if attempt >= MAX_TELEGRAM_SEND_RETRIES:
                    break
                await asyncio.sleep(wait_seconds)
        if last_error:
            raise last_error
        raise RuntimeError("send_text retry loop failed unexpectedly")

    async def _get_trend_boosts(self) -> dict[str, float]:
        collector = getattr(self, "_trend_collector", None)
        if collector is None:
            return {}
        try:
            return await collector.get_keyword_boosts(max_items=80)
        except Exception:
            self._log.exception("ingestion.trend_boosts_failed")
            return {}

    async def _record_source_quality_event(
        self,
        *,
        source_id: int | None,
        event: str,
        details: dict | None = None,
    ) -> None:
        if source_id is None:
            return
        source_quality = getattr(self, "_source_quality", None)
        if source_quality is None:
            return
        quality_result = None
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    quality_result = await source_quality.apply_event(
                        session,
                        source_id=source_id,
                        event=event,
                        details=details,
                    )
            if quality_result and quality_result.auto_disabled:
                await self._notify_source_auto_disabled(
                    source_id=quality_result.source_id,
                    source_name=quality_result.source_name,
                    trust_score=quality_result.trust_score,
                    events_total=quality_result.events_total,
                    consecutive_failures=quality_result.consecutive_failures,
                    trigger_event=event,
                )
        except Exception:
            self._log.exception(
                "ingestion.source_quality_update_failed",
                source_id=source_id,
                event=event,
            )

    async def _notify_source_auto_disabled(
        self,
        *,
        source_id: int,
        source_name: str,
        trust_score: float,
        events_total: int,
        consecutive_failures: int,
        trigger_event: str,
    ) -> None:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    bot_settings = await self._settings_repo.get_or_create(session)
                    chat_id = bot_settings.group_chat_id
                    topic_id = bot_settings.editing_topic_id or bot_settings.inbox_topic_id
            if not chat_id or not topic_id:
                return
            await self._publisher.send_text(
                chat_id=chat_id,
                topic_id=topic_id,
                text=(
                    "Source health alert:\n"
                    f"Источник #{source_id} ({source_name}) автоматически отключен.\n"
                    f"trust_score={trust_score:.2f}, events={events_total}, "
                    f"consecutive_failures={consecutive_failures}, trigger={trigger_event}.\n"
                    f"Проверка/включение: /source_quality {source_id} и /enable_source {source_id}"
                ),
            )
        except Exception:
            self._log.exception(
                "ingestion.source_quality_notify_failed",
                source_id=source_id,
            )

    @staticmethod
    def _topic_hints_from_tags(source_tags: dict | None) -> list[str]:
        if not source_tags:
            return []
        topics_value = source_tags.get("topics")
        if isinstance(topics_value, list):
            return [
                str(item).strip().lower()
                for item in topics_value
                if str(item).strip()
            ]
        if isinstance(topics_value, str):
            text = topics_value.strip().lower()
            return [text] if text else []
        topic_value = source_tags.get("topic")
        if isinstance(topic_value, str):
            text = topic_value.strip().lower()
            return [text] if text else []
        return []

    def _is_rate_limited_source(self, source_id: int, *, now: datetime) -> bool:
        min_interval = self._settings.rss.per_source_min_interval_seconds
        if min_interval <= 0:
            return False
        last_poll = self._source_last_poll.get(source_id)
        if not last_poll:
            return False
        return (now - last_poll).total_seconds() < min_interval

    def _blocked_reason(
        self,
        *,
        domain: str,
        normalized_url: str,
        title: str | None = None,
    ) -> str | None:
        domain_lc = domain.lower().strip()
        blocked_domains = {
            item.lower().strip()
            for item in self._settings.rss.blocked_domains
            if item and item.strip()
        }
        for blocked in blocked_domains:
            if domain_lc == blocked or domain_lc.endswith(f".{blocked}"):
                return f"domain:{blocked}"

        url_lc = normalized_url.lower()
        for keyword in self._settings.rss.blocked_url_keywords:
            candidate = keyword.lower().strip()
            if candidate and candidate in url_lc:
                return f"url_keyword:{candidate}"

        title_lc = (title or "").lower()
        for keyword in self._settings.rss.blocked_title_keywords:
            candidate = keyword.lower().strip()
            if candidate and candidate in title_lc:
                return f"title_keyword:{candidate}"
        return None

    @staticmethod
    def _normalized_url_candidates(link: str, *, entry_id: str | None) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        raw_values = [link]
        if entry_id:
            raw_values.append(entry_id)

        for raw in raw_values:
            if not raw:
                continue
            try:
                normalized = normalize_url(raw)
            except ValueError:
                continue
            for value in (normalized, normalize_url(normalized.split("?", maxsplit=1)[0])):
                if not extract_domain(value):
                    continue
                if value and value not in seen:
                    seen.add(value)
                    candidates.append(value)
        return candidates

    async def _fetch_url_with_ssl_policy(
        self,
        url: str,
        *,
        http: AsyncClient,
        source_id: int | None,
        feed_tags: dict | None,
        is_rss: bool,
    ):
        await self._request_delay()
        try:
            return await http.get(url)
        except Exception as exc:
            if (
                self._should_try_insecure_ssl(url=url, feed_tags=feed_tags)
                and self._looks_like_ssl_error(exc)
            ):
                try:
                    await self._request_delay()
                    async with AsyncClient(
                        follow_redirects=True,
                        timeout=20,
                        verify=False,
                    ) as insecure_http:
                        response = await insecure_http.get(url)
                    self._log.warning(
                        "ingestion.ssl_insecure_fallback_used",
                        source_id=source_id,
                        url=url,
                        is_rss=is_rss,
                    )
                    metrics.inc_counter("ingestion_ssl_fallback_total")
                    return response
                except Exception:
                    self._log.exception(
                        "ingestion.ssl_insecure_fallback_failed",
                        source_id=source_id,
                        url=url,
                    )
                    return None

            event_name = "ingestion.fetch_rss_failed" if is_rss else "ingestion.fetch_html_failed"
            log_kwargs = {"url": url}
            if source_id is not None:
                log_kwargs["source_id"] = source_id
            self._log.exception(event_name, **log_kwargs)
            add_sentry_breadcrumb(
                category="ingestion",
                message="fetch failed",
                level="warning",
                data={"url": url, "source_id": source_id, "is_rss": is_rss},
            )
            return None

    async def _request_delay(self) -> None:
        delay = self._settings.rss.request_delay_seconds
        if delay > 0:
            await asyncio.sleep(delay)

    def _should_try_insecure_ssl(self, *, url: str, feed_tags: dict | None) -> bool:
        if not self._settings.rss.allow_insecure_ssl_fallback:
            return False

        domain = extract_domain(url)
        allowed_domains = {item.lower().strip() for item in self._settings.rss.insecure_ssl_domains if item}
        if domain and (domain in allowed_domains or "*" in allowed_domains):
            return True

        if isinstance(feed_tags, dict):
            raw = feed_tags.get("allow_insecure_ssl")
            if isinstance(raw, bool):
                return raw
        return False

    @staticmethod
    def _looks_like_ssl_error(exc: Exception) -> bool:
        if isinstance(exc, httpx.ConnectError):
            message = str(exc).lower()
            return "certificate verify failed" in message or "ssl" in message
        return False

    @staticmethod
    def _parse_published(entry) -> datetime | None:
        for key in ("published_parsed", "updated_parsed"):
            value = entry.get(key)
            if value:
                return datetime(*value[:6], tzinfo=timezone.utc)
        return None

    async def _fetch_html(self, url: str, http: AsyncClient) -> str | None:
        response = await self._fetch_url_with_ssl_policy(
            url,
            http=http,
            source_id=None,
            feed_tags=None,
            is_rss=False,
        )
        if response is None:
            return None
        if response.status_code >= 400:
            return None
        return response.text
