"""Edit session service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from telegram_publisher import ButtonSpec, keyboard_from_specs
from telegram_publisher.types import PostContent
from tg_news_bot.db.models import Draft, EditSession, EditSessionStatus, ImageStatus
from tg_news_bot.ports.publisher import (
    PublisherEditNotAllowed,
    PublisherNotFound,
    PublisherPort,
)
from tg_news_bot.repositories.bot_settings import BotSettingsRepository
from tg_news_bot.repositories.drafts import DraftRepository
from tg_news_bot.repositories.edit_sessions import EditSessionRepository
from tg_news_bot.services.keyboards import build_state_keyboard
from tg_news_bot.services.metrics import metrics
from tg_news_bot.services.rendering import render_card_text, render_post_content
from tg_news_bot.telegram.callbacks import build_callback


@dataclass(slots=True)
class EditPayload:
    chat_id: int
    topic_id: int
    user_id: int
    message_id: int
    text: str | None
    photo_file_id: str | None
    photo_unique_id: str | None


class EditSessionService:
    def __init__(
        self,
        publisher: PublisherPort,
        *,
        settings_repo: BotSettingsRepository | None = None,
        draft_repo: DraftRepository | None = None,
        edit_repo: EditSessionRepository | None = None,
    ) -> None:
        self._publisher = publisher
        self._settings_repo = settings_repo or BotSettingsRepository()
        self._draft_repo = draft_repo or DraftRepository()
        self._edit_repo = edit_repo or EditSessionRepository()

    async def start(self, session: AsyncSession, *, draft_id: int, user_id: int) -> None:
        settings = await self._settings_repo.get_or_create(session)
        if not settings.group_chat_id or not settings.editing_topic_id:
            raise RuntimeError("EDITING topic or group is not configured")

        other = await self._edit_repo.get_active_for_topic(
            session,
            group_chat_id=settings.group_chat_id,
            topic_id=settings.editing_topic_id,
        )
        if other and other.draft_id != draft_id:
            await self._finalize(session, other, EditSessionStatus.CANCELLED)

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=10)
        edit_session = await self._edit_repo.upsert_active(
            session,
            draft_id=draft_id,
            group_chat_id=settings.group_chat_id,
            topic_id=settings.editing_topic_id,
            user_id=user_id,
            started_at=now,
            expires_at=expires_at,
        )

        instruction = self._instruction_text(draft_id)
        keyboard = keyboard_from_specs(
            [
                [
                    ButtonSpec(
                        text="Cancel edit",
                        callback_data=build_callback(draft_id, "cancel_edit"),
                    )
                ]
            ]
        )

        instruction_id = edit_session.instruction_message_id
        if instruction_id:
            try:
                await self._publisher.edit_text(
                    chat_id=settings.group_chat_id,
                    message_id=instruction_id,
                    text=instruction,
                    keyboard=keyboard,
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
            except (PublisherNotFound, PublisherEditNotAllowed):
                instruction_id = None

        if not instruction_id:
            sent = await self._publisher.send_text(
                chat_id=settings.group_chat_id,
                topic_id=settings.editing_topic_id,
                text=instruction,
                keyboard=keyboard,
                parse_mode=None,
            )
            instruction_id = sent.message_id

        edit_session.instruction_message_id = instruction_id
        await session.flush()
        metrics.set_gauge("edit_sessions_active", 1)

    async def cancel(self, session: AsyncSession, *, draft_id: int) -> None:
        active = await self._edit_repo.get_active_by_draft(session, draft_id)
        if not active:
            return
        await self._finalize(session, active, EditSessionStatus.CANCELLED)

    async def cancel_active_for_topic(
        self, session: AsyncSession, *, group_chat_id: int, topic_id: int
    ) -> bool:
        active = await self._edit_repo.get_active_for_topic(
            session, group_chat_id=group_chat_id, topic_id=topic_id
        )
        if not active:
            return False
        await self._finalize(session, active, EditSessionStatus.CANCELLED)
        return True

    async def apply_edit(self, session: AsyncSession, payload: EditPayload) -> Draft | None:
        active = await self._edit_repo.get_active_for_topic(
            session, group_chat_id=payload.chat_id, topic_id=payload.topic_id
        )
        if not active:
            return None
        now = datetime.now(timezone.utc)
        if active.expires_at <= now:
            await self._finalize(session, active, EditSessionStatus.EXPIRED)
            return None

        if not payload.text and not payload.photo_file_id:
            return None

        draft = await self._draft_repo.get_for_update(session, active.draft_id)
        previous_post_message_id = draft.post_message_id
        previous_card_message_id = draft.card_message_id

        text_updated = False
        if payload.text is not None:
            draft.post_text_ru = payload.text
            text_updated = True

        if payload.photo_file_id:
            draft.tg_image_file_id = payload.photo_file_id
            draft.tg_image_unique_id = payload.photo_unique_id
            draft.has_image = True
            draft.image_status = ImageStatus.OK

        await self._update_post_message(
            session=session,
            draft=draft,
            text_updated=text_updated,
            new_photo=payload.photo_file_id,
        )
        try:
            await self._update_card_message(draft)
        except Exception:
            # Keep POST/CARD invariant when both messages are created from scratch.
            if (
                previous_post_message_id is None
                and previous_card_message_id is None
                and draft.post_message_id is not None
                and draft.group_chat_id is not None
            ):
                await self._safe_delete(draft.group_chat_id, draft.post_message_id)
                draft.post_message_id = None
            raise

        await self._safe_delete(payload.chat_id, payload.message_id)
        await self._finalize(session, active, EditSessionStatus.COMPLETED)
        return draft

    async def _update_post_message(
        self,
        *,
        session: AsyncSession,
        draft: Draft,
        text_updated: bool,
        new_photo: str | None,
    ) -> None:
        if not draft.group_chat_id or not draft.topic_id:
            raise RuntimeError("Draft has no group/topic for editing")

        keyboard = build_state_keyboard(draft, draft.state)
        post_content = render_post_content(draft)
        current_post_id = draft.post_message_id

        if not current_post_id:
            sent = await self._publisher.send_post(
                chat_id=draft.group_chat_id,
                topic_id=draft.topic_id,
                content=post_content,
                keyboard=keyboard,
            )
            draft.post_message_id = sent.message_id
            if sent.photo_file_id:
                draft.tg_image_file_id = sent.photo_file_id
                draft.tg_image_unique_id = sent.photo_unique_id
            await session.flush()
            return

        try:
            if new_photo:
                result = await self._publisher.edit_post(
                    chat_id=draft.group_chat_id,
                    message_id=current_post_id,
                    content=PostContent(
                        text=post_content.text,
                        photo=new_photo,
                        parse_mode=post_content.parse_mode,
                    ),
                    keyboard=keyboard,
                )
                if result.photo_file_id:
                    draft.tg_image_file_id = result.photo_file_id
                    draft.tg_image_unique_id = result.photo_unique_id
                return

            if draft.tg_image_file_id or draft.source_image_url:
                if text_updated:
                    await self._publisher.edit_caption(
                        chat_id=draft.group_chat_id,
                        message_id=current_post_id,
                        caption=post_content.text,
                        keyboard=keyboard,
                        parse_mode=post_content.parse_mode,
                    )
                return

            await self._publisher.edit_post(
                chat_id=draft.group_chat_id,
                message_id=current_post_id,
                content=PostContent(
                    text=post_content.text,
                    photo=None,
                    parse_mode=post_content.parse_mode,
                ),
                keyboard=keyboard,
            )
        except (PublisherEditNotAllowed, PublisherNotFound):
            sent = await self._publisher.send_post(
                chat_id=draft.group_chat_id,
                topic_id=draft.topic_id,
                content=post_content,
                keyboard=keyboard,
            )
            old_post_id = draft.post_message_id
            draft.post_message_id = sent.message_id
            if sent.photo_file_id:
                draft.tg_image_file_id = sent.photo_file_id
                draft.tg_image_unique_id = sent.photo_unique_id
            await session.flush()
            if old_post_id:
                await self._safe_delete(draft.group_chat_id, old_post_id)

    async def _update_card_message(self, draft: Draft) -> None:
        if not draft.group_chat_id or not draft.topic_id:
            raise RuntimeError("Draft has no group/topic for editing")
        text = render_card_text(draft)
        if not draft.card_message_id:
            sent = await self._publisher.send_text(
                chat_id=draft.group_chat_id,
                topic_id=draft.topic_id,
                text=text,
                keyboard=None,
                parse_mode=None,
            )
            draft.card_message_id = sent.message_id
            return
        try:
            await self._publisher.edit_text(
                chat_id=draft.group_chat_id,
                message_id=draft.card_message_id,
                text=text,
                keyboard=None,
                parse_mode=None,
                disable_web_page_preview=True,
            )
        except (PublisherEditNotAllowed, PublisherNotFound):
            sent = await self._publisher.send_text(
                chat_id=draft.group_chat_id,
                topic_id=draft.topic_id,
                text=text,
                keyboard=None,
                parse_mode=None,
            )
            old_card_id = draft.card_message_id
            draft.card_message_id = sent.message_id
            if old_card_id:
                await self._safe_delete(draft.group_chat_id, old_card_id)

    async def _finalize(
        self,
        session: AsyncSession,
        edit_session: EditSession,
        status: EditSessionStatus,
    ) -> None:
        edit_session.status = status
        instruction_id = edit_session.instruction_message_id
        edit_session.instruction_message_id = None
        await session.flush()
        metrics.set_gauge("edit_sessions_active", 0)

        if instruction_id:
            await self._safe_delete(edit_session.group_chat_id, instruction_id)

    async def _safe_delete(self, chat_id: int, message_id: int) -> None:
        try:
            await self._publisher.delete_message(chat_id=chat_id, message_id=message_id)
        except (PublisherNotFound, PublisherEditNotAllowed):
            return

    @staticmethod
    def _instruction_text(draft_id: int) -> str:
        return (
            f"Draft #{draft_id}\n"
            "\u041f\u0440\u0438\u0448\u043b\u0438\u0442\u0435 \u043d\u043e\u0432\u044b\u0439 \u0442\u0435\u043a\u0441\u0442 \u0438/\u0438\u043b\u0438 \u0444\u043e\u0442\u043e "
            "\u0441 \u043f\u043e\u0434\u043f\u0438\u0441\u044c\u044e. /cancel - \u043e\u0442\u043c\u0435\u043d\u0430."
        )
