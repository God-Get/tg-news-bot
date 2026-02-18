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
from tg_news_bot.config import PostFormattingSettings
from tg_news_bot.db.models import (
    BotSettings,
    Draft,
    DraftState,
    ImageStatus,
    PublishFailureContext,
    ScheduledPostStatus,
)
from tg_news_bot.repositories.bot_settings import BotSettingsRepository
from tg_news_bot.repositories.drafts import DraftRepository
from tg_news_bot.repositories.publish_failures import PublishFailureRepository
from tg_news_bot.repositories.scheduled_posts import ScheduledPostRepository
from tg_news_bot.services.keyboards import (
    build_schedule_keyboard,
    build_source_button_keyboard,
    build_state_keyboard,
)
from tg_news_bot.services.edit_sessions import EditSessionService
from tg_news_bot.services.rendering import render_card_text, render_post_content
from tg_news_bot.services.metrics import metrics
from tg_news_bot.services.scheduling import ScheduleService
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

    async def transition(self, request: TransitionRequest) -> Draft:
        async with self._session_factory() as session:
            async with session.begin():
                draft = await self._draft_repo.get_for_update(session, request.draft_id)
                settings = await self._settings_repo.get_or_create(session)
                source_state = draft.state

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

                if request.action in {DraftAction.PUBLISH_NOW, DraftAction.REPOST}:
                    try:
                        await self._publish_now(session, draft, settings)
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
