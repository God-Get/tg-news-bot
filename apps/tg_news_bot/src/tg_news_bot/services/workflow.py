"""Draft workflow and state machine."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from tg_news_bot.ports.publisher import (
    PublisherEditNotAllowed,
    PublisherNotFound,
    PublisherNotModified,
    PublisherPort,
)
from tg_news_bot.config import ContentSafetySettings, PostFormattingSettings
from tg_news_bot.db.models import (
    BotSettings,
    Draft,
    DraftState,
    ImageStatus,
    PublishFailureContext,
    ScheduledPostStatus,
)
from tg_news_bot.repositories.bot_settings import BotSettingsRepository
from tg_news_bot.repositories.articles import ArticleRepository
from tg_news_bot.repositories.drafts import DraftRepository
from tg_news_bot.repositories.publish_failures import PublishFailureRepository
from tg_news_bot.repositories.scheduled_posts import ScheduledPostRepository
from tg_news_bot.repositories.sources import SourceRepository
from tg_news_bot.services.keyboards import (
    build_schedule_keyboard,
    build_source_button_keyboard,
    build_state_keyboard,
)
from tg_news_bot.services.edit_sessions import EditSessionService
from tg_news_bot.services.rendering import render_card_text, render_post_content
from tg_news_bot.services.content_safety import ContentSafetyService
from tg_news_bot.services.metrics import metrics
from tg_news_bot.services.scheduling import ScheduleService
from tg_news_bot.services.source_text import sanitize_source_text
from tg_news_bot.services.text_generation import TextPipeline, compose_post_text
from tg_news_bot.services.workflow_types import DraftAction, TransitionRequest


class DraftWorkflowService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: PublisherPort,
        *,
        settings_repo: BotSettingsRepository | None = None,
        draft_repo: DraftRepository | None = None,
        scheduled_repo: ScheduledPostRepository | None = None,
        schedule_service: ScheduleService | None = None,
        edit_session_service: EditSessionService | None = None,
        post_formatting: PostFormattingSettings | None = None,
        publish_failure_repo: PublishFailureRepository | None = None,
        source_repo: SourceRepository | None = None,
        article_repo: ArticleRepository | None = None,
        text_pipeline: TextPipeline | None = None,
        content_safety: ContentSafetyService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._settings_repo = settings_repo or BotSettingsRepository()
        self._draft_repo = draft_repo or DraftRepository()
        self._scheduled_repo = scheduled_repo or ScheduledPostRepository()
        self._schedule_service = schedule_service or ScheduleService()
        self._edit_sessions = edit_session_service or EditSessionService(publisher)
        self._post_formatting = post_formatting
        self._publish_failure_repo = publish_failure_repo or PublishFailureRepository()
        self._source_repo = source_repo or SourceRepository()
        self._article_repo = article_repo or ArticleRepository()
        self._text_pipeline = text_pipeline
        self._content_safety = content_safety or ContentSafetyService(ContentSafetySettings())

    async def transition(self, request: TransitionRequest) -> Draft:
        async with self._session_factory() as session:
            async with session.begin():
                draft = await self._draft_repo.get_for_update(session, request.draft_id)
                settings = await self._settings_repo.get_or_create(session)
                source_state = draft.state
                move_handled = False

                target_state = self._resolve_target_state(
                    draft.state, request.action
                )
                if target_state is None:
                    return draft

                if request.action == DraftAction.SCHEDULE:
                    if request.schedule_at is None:
                        raise ValueError("schedule_at is required for schedule action")
                    await self._schedule_service.schedule(
                        session, draft_id=draft.id, schedule_at=request.schedule_at
                    )
                if request.action == DraftAction.CANCEL_SCHEDULE:
                    await self._schedule_service.cancel(session, draft_id=draft.id)
                if request.action == DraftAction.TO_READY:
                    self._ensure_ready_content_is_safe(draft)

                if request.action in {DraftAction.PUBLISH_NOW, DraftAction.REPOST}:
                    try:
                        should_publish = not (
                            request.action == DraftAction.PUBLISH_NOW
                            and self._has_published_channel_message(draft)
                        )
                        if should_publish:
                            await self._publish_now(session, draft, settings)
                        should_move = not (
                            request.action == DraftAction.SCHEDULE
                            and source_state == DraftState.SCHEDULED
                        )
                        if should_move:
                            await self._move_in_group(
                                session=session,
                                draft=draft,
                                settings=settings,
                                target_state=target_state,
                            )
                            move_handled = True
                        await self._publish_failure_repo.mark_resolved_for_draft(
                            session,
                            draft_id=draft.id,
                        )
                    except Exception:
                        metrics.inc_counter("publish_fail_total")
                        await self._publish_failure_repo.create(
                            session,
                            draft_id=draft.id,
                            context=PublishFailureContext.MANUAL,
                            error_message="publish_now_failed",
                            attempt_no=1,
                            details={"state": source_state.value},
                        )
                        raise
                    if draft.state == DraftState.SCHEDULED:
                        await self._schedule_service.mark_published(
                            session, draft_id=draft.id
                        )

                if not move_handled:
                    should_move = not (
                        request.action == DraftAction.SCHEDULE
                        and source_state == DraftState.SCHEDULED
                    )
                    if should_move:
                        await self._move_in_group(
                            session=session,
                            draft=draft,
                            settings=settings,
                            target_state=target_state,
                        )
                    elif target_state == DraftState.SCHEDULED:
                        await self._refresh_scheduled_messages(session=session, draft=draft)

                draft.state = target_state
                if source_state != target_state:
                    metrics.inc_counter(
                        "drafts_state_total", labels={"state": target_state.value}
                    )

                if target_state == DraftState.EDITING:
                    await self._edit_sessions.start(
                        session, draft_id=draft.id, user_id=request.user_id
                    )
                if request.action in {DraftAction.TO_ARCHIVE}:
                    await self._edit_sessions.cancel(session, draft_id=draft.id)

                return draft

    async def show_schedule_menu(
        self,
        *,
        draft_id: int,
        menu: str,
        now: datetime,
        timezone_name: str,
        selected_day: date | None = None,
    ) -> None:
        async with self._session_factory() as session:
            draft = await self._draft_repo.get(session, draft_id)
            if not draft:
                return
            if not draft.group_chat_id or not draft.post_message_id:
                return

        keyboard = build_schedule_keyboard(
            draft,
            menu=menu,
            now=now,
            timezone_name=timezone_name,
            selected_day=selected_day,
        )
        try:
            await self._publisher.edit_reply_markup(
                chat_id=draft.group_chat_id,
                message_id=draft.post_message_id,
                keyboard=keyboard,
            )
        except (PublisherNotFound, PublisherEditNotAllowed, PublisherNotModified):
            return

    async def restore_state_keyboard(self, *, draft_id: int) -> None:
        async with self._session_factory() as session:
            draft = await self._draft_repo.get(session, draft_id)
            if not draft:
                return
            if not draft.group_chat_id or not draft.post_message_id:
                return

        keyboard = build_state_keyboard(draft, draft.state)
        try:
            await self._publisher.edit_reply_markup(
                chat_id=draft.group_chat_id,
                message_id=draft.post_message_id,
                keyboard=keyboard,
            )
        except (PublisherNotFound, PublisherEditNotAllowed, PublisherNotModified):
            return

    async def process_editing_text(self, *, draft_id: int) -> None:
        if self._text_pipeline is None:
            raise RuntimeError("text pipeline is not configured")

        async with self._session_factory() as session:
            async with session.begin():
                draft = await self._draft_repo.get_for_update(session, draft_id)
                if draft.state != DraftState.EDITING:
                    raise ValueError("processing is available only for EDITING drafts")

                source_tags: dict | None = None
                if draft.source_id is not None:
                    source = await self._source_repo.get_by_id(session, draft.source_id)
                    if source:
                        source_tags = source.tags
                topic_hints = self._topic_hints_from_tags(source_tags)

                source_text = draft.extracted_text
                if not source_text and draft.article_id is not None:
                    article = await self._article_repo.get_by_id(session, draft.article_id)
                    if article and article.extracted_text:
                        source_text = article.extracted_text

                source_text = sanitize_source_text(source_text)
                if not source_text:
                    source_text = (draft.title_en or "").strip()
                if not source_text:
                    raise ValueError("draft has no source text to process")

                generated = await self._text_pipeline.generate_parts(
                    title_en=draft.title_en,
                    text_en=source_text,
                    topic_hints=topic_hints,
                )
                draft.post_text_ru = compose_post_text(
                    generated.title_ru,
                    generated.summary_ru,
                )
                await session.flush()

        await self._refresh_draft_messages(draft_id=draft_id)

    def _resolve_target_state(
        self, current: DraftState, action: DraftAction
    ) -> DraftState | None:
        transitions = {
            DraftState.INBOX: {
                DraftAction.TO_EDITING: DraftState.EDITING,
                DraftAction.TO_ARCHIVE: DraftState.ARCHIVE,
            },
            DraftState.EDITING: {
                DraftAction.TO_READY: DraftState.READY,
                DraftAction.TO_ARCHIVE: DraftState.ARCHIVE,
            },
            DraftState.READY: {
                DraftAction.TO_EDITING: DraftState.EDITING,
                DraftAction.TO_ARCHIVE: DraftState.ARCHIVE,
                DraftAction.SCHEDULE: DraftState.SCHEDULED,
                DraftAction.PUBLISH_NOW: DraftState.PUBLISHED,
            },
            DraftState.SCHEDULED: {
                DraftAction.SCHEDULE: DraftState.SCHEDULED,
                DraftAction.CANCEL_SCHEDULE: DraftState.READY,
                DraftAction.PUBLISH_NOW: DraftState.PUBLISHED,
                DraftAction.TO_ARCHIVE: DraftState.ARCHIVE,
            },
            DraftState.PUBLISHED: {
                DraftAction.REPOST: DraftState.PUBLISHED,
                DraftAction.TO_EDITING: DraftState.EDITING,
                DraftAction.TO_ARCHIVE: DraftState.ARCHIVE,
            },
            DraftState.ARCHIVE: {},
        }
        target = transitions.get(current, {}).get(action)
        return target

    async def _publish_now(
        self, session: AsyncSession, draft: Draft, settings: BotSettings
    ) -> None:
        if not settings.channel_id:
            raise RuntimeError("channel_id is not configured")
        content = render_post_content(draft, formatting=self._post_formatting)
        keyboard = build_source_button_keyboard(
            draft,
            formatting=self._post_formatting,
        )
        result = await self._publisher.send_post(
            chat_id=settings.channel_id,
            topic_id=None,
            content=content,
            keyboard=keyboard,
        )
        metrics.inc_counter("publish_success_total")
        draft.published_message_id = result.message_id
        draft.published_at = datetime.now(timezone.utc)
        await session.flush()

    async def _move_in_group(
        self,
        *,
        session: AsyncSession,
        draft: Draft,
        settings: BotSettings,
        target_state: DraftState,
    ) -> None:
        group_chat_id = settings.group_chat_id
        if not group_chat_id:
            raise RuntimeError("group_chat_id is not configured")
        topic_id = self._topic_id_for_state(settings, target_state)

        keyboard = build_state_keyboard(draft, target_state)
        post_content = render_post_content(draft, formatting=self._post_formatting)
        post = await self._publisher.send_post(
            chat_id=group_chat_id,
            topic_id=topic_id,
            content=post_content,
            keyboard=keyboard,
        )
        if post.photo_file_id:
            draft.tg_image_file_id = post.photo_file_id
            draft.tg_image_unique_id = post.photo_unique_id
            draft.has_image = True
            draft.image_status = ImageStatus.OK

        schedule_at = await self._active_schedule_at(session, draft.id)
        card_text = render_card_text(
            draft,
            schedule_at=schedule_at,
            state=target_state,
        )
        try:
            card = await self._publisher.send_text(
                chat_id=group_chat_id,
                topic_id=topic_id,
                text=card_text,
                keyboard=None,
                parse_mode=None,
            )
        except Exception:
            # Keep POST/CARD pair consistent: if CARD send fails, remove freshly sent POST.
            try:
                await self._safe_delete(group_chat_id, post.message_id)
            except Exception:
                pass
            raise

        old_post_id = draft.post_message_id
        old_card_id = draft.card_message_id
        draft.group_chat_id = group_chat_id
        draft.topic_id = topic_id
        draft.post_message_id = post.message_id
        draft.card_message_id = card.message_id
        await session.flush()

        if old_post_id:
            await self._safe_delete(group_chat_id, old_post_id)
        if old_card_id:
            await self._safe_delete(group_chat_id, old_card_id)

    async def _refresh_scheduled_messages(
        self,
        *,
        session: AsyncSession,
        draft: Draft,
    ) -> None:
        if not draft.group_chat_id:
            return
        if not draft.post_message_id:
            return
        keyboard = build_state_keyboard(draft, DraftState.SCHEDULED)
        try:
            await self._publisher.edit_reply_markup(
                chat_id=draft.group_chat_id,
                message_id=draft.post_message_id,
                keyboard=keyboard,
            )
        except (PublisherNotFound, PublisherEditNotAllowed, PublisherNotModified):
            return

        if not draft.card_message_id:
            return
        schedule_at = await self._active_schedule_at(session, draft.id)
        card_text = render_card_text(
            draft,
            schedule_at=schedule_at,
            state=DraftState.SCHEDULED,
        )
        try:
            await self._publisher.edit_text(
                chat_id=draft.group_chat_id,
                message_id=draft.card_message_id,
                text=card_text,
                keyboard=None,
                parse_mode=None,
                disable_web_page_preview=True,
            )
        except (PublisherNotFound, PublisherEditNotAllowed, PublisherNotModified):
            return

    async def _safe_delete(self, chat_id: int, message_id: int) -> None:
        try:
            await self._publisher.delete_message(chat_id=chat_id, message_id=message_id)
        except (PublisherNotFound, PublisherEditNotAllowed):
            return

    async def _active_schedule_at(
        self,
        session: AsyncSession,
        draft_id: int,
    ) -> datetime | None:
        scheduled = await self._scheduled_repo.get_by_draft(session, draft_id)
        if not scheduled:
            return None
        if scheduled.status != ScheduledPostStatus.SCHEDULED:
            return None
        return scheduled.schedule_at

    @staticmethod
    def _topic_id_for_state(settings: BotSettings, state: DraftState) -> int:
        mapping = {
            DraftState.INBOX: settings.inbox_topic_id,
            DraftState.EDITING: settings.editing_topic_id,
            DraftState.READY: settings.ready_topic_id,
            DraftState.SCHEDULED: settings.scheduled_topic_id,
            DraftState.PUBLISHED: settings.published_topic_id,
            DraftState.ARCHIVE: settings.archive_topic_id,
        }
        topic_id = mapping.get(state)
        if not topic_id:
            raise RuntimeError(f"topic_id for {state} is not configured")
        return int(topic_id)

    @staticmethod
    def _has_published_channel_message(draft: Draft) -> bool:
        return bool(draft.published_message_id)

    async def _refresh_draft_messages(self, *, draft_id: int) -> None:
        async with self._session_factory() as session:
            draft = await self._draft_repo.get(session, draft_id)
            if not draft:
                return
            if not draft.group_chat_id or not draft.post_message_id:
                return

        keyboard = build_state_keyboard(draft, draft.state)
        post_content = render_post_content(draft, formatting=self._post_formatting)
        try:
            await self._publisher.edit_post(
                chat_id=draft.group_chat_id,
                message_id=draft.post_message_id,
                content=post_content,
                keyboard=keyboard,
            )
        except PublisherNotModified:
            pass
        except (PublisherNotFound, PublisherEditNotAllowed):
            return

        if not draft.card_message_id:
            return
        try:
            await self._publisher.edit_text(
                chat_id=draft.group_chat_id,
                message_id=draft.card_message_id,
                text=render_card_text(draft),
                keyboard=None,
                parse_mode=None,
                disable_web_page_preview=True,
            )
        except (PublisherNotFound, PublisherEditNotAllowed, PublisherNotModified):
            return

    async def refresh_draft_messages(self, *, draft_id: int) -> None:
        await self._refresh_draft_messages(draft_id=draft_id)

    def _ensure_ready_content_is_safe(self, draft: Draft) -> None:
        check = self._content_safety.check(
            text=draft.post_text_ru,
            title=draft.title_en,
        )
        if check.allowed:
            return
        reason_preview = ",".join(check.reasons[:4]) if check.reasons else "unknown"
        raise ValueError(f"content_safety_failed:{reason_preview}")

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

