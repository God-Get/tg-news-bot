from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from telegram_publisher.types import PostContent, SendResult
from tg_news_bot.db.models import (
    BotSettings,
    Draft,
    DraftState,
    EditSession,
    EditSessionStatus,
)
from tg_news_bot.ports.publisher import PublisherNotModified
from tg_news_bot.services.edit_sessions import EditPayload, EditSessionService


class _Session:
    async def flush(self) -> None:
        return None


@dataclass
class _SettingsRepo:
    settings: BotSettings

    async def get_or_create(self, session: _Session) -> BotSettings:  # noqa: ARG002
        return self.settings


@dataclass
class _DraftRepo:
    draft: Draft

    async def get_for_update(self, session: _Session, draft_id: int) -> Draft:  # noqa: ARG002
        if draft_id != self.draft.id:
            raise LookupError
        return self.draft


@dataclass
class _EditRepo:
    sessions: list[EditSession] = field(default_factory=list)
    next_id: int = 1

    async def get_active_by_draft(
        self, session: _Session, draft_id: int  # noqa: ARG002
    ) -> EditSession | None:
        for item in reversed(self.sessions):
            if item.draft_id == draft_id and item.status == EditSessionStatus.ACTIVE:
                return item
        return None

    async def get_active_for_topic(
        self,
        session: _Session,  # noqa: ARG002
        *,
        group_chat_id: int,
        topic_id: int,
    ) -> EditSession | None:
        for item in reversed(self.sessions):
            if (
                item.group_chat_id == group_chat_id
                and item.topic_id == topic_id
                and item.status == EditSessionStatus.ACTIVE
            ):
                return item
        return None

    async def upsert_active(
        self,
        session: _Session,  # noqa: ARG002
        *,
        draft_id: int,
        group_chat_id: int,
        topic_id: int,
        user_id: int,
        started_at: datetime,
        expires_at: datetime,
    ) -> EditSession:
        existing = await self.get_active_by_draft(session, draft_id)
        if existing:
            existing.group_chat_id = group_chat_id
            existing.topic_id = topic_id
            existing.user_id = user_id
            existing.started_at = started_at
            existing.expires_at = expires_at
            existing.status = EditSessionStatus.ACTIVE
            return existing

        created = EditSession(
            id=self.next_id,
            draft_id=draft_id,
            group_chat_id=group_chat_id,
            topic_id=topic_id,
            user_id=user_id,
            instruction_message_id=None,
            status=EditSessionStatus.ACTIVE,
            started_at=started_at,
            expires_at=expires_at,
        )
        self.next_id += 1
        self.sessions.append(created)
        return created


@dataclass
class _Publisher:
    next_message_id: int = 1000
    instruction_message_ids: list[int] = field(default_factory=list)
    deleted_message_ids: list[int] = field(default_factory=list)
    post_edit_calls: int = 0
    card_edit_calls: int = 0
    fail_card_send: bool = False

    async def send_text(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        text: str,
        keyboard=None,  # noqa: ANN001
        parse_mode: str | None = None,  # noqa: ARG002
    ) -> SendResult:
        if self.fail_card_send and keyboard is None:
            raise RuntimeError("card send failed")
        message_id = self.next_message_id
        self.next_message_id += 1
        if keyboard and text.startswith("Draft #"):
            self.instruction_message_ids.append(message_id)
        return SendResult(chat_id=chat_id, message_id=message_id)

    async def edit_text(
        self,
        *,
        chat_id: int,  # noqa: ARG002
        message_id: int,  # noqa: ARG002
        text: str,  # noqa: ARG002
        keyboard=None,  # noqa: ANN001
        parse_mode: str | None = None,  # noqa: ARG002
        disable_web_page_preview: bool = False,  # noqa: ARG002
    ) -> None:
        if keyboard is None:
            self.card_edit_calls += 1

    async def send_post(
        self,
        *,
        chat_id: int,
        topic_id: int | None,  # noqa: ARG002
        content: PostContent,
        keyboard=None,  # noqa: ANN001, ARG002
    ) -> SendResult:
        message_id = self.next_message_id
        self.next_message_id += 1
        return SendResult(
            chat_id=chat_id,
            message_id=message_id,
            photo_file_id=content.photo,
            photo_unique_id=None,
        )

    async def edit_post(
        self,
        *,
        chat_id: int,  # noqa: ARG002
        message_id: int,
        content: PostContent,
        keyboard=None,  # noqa: ANN001, ARG002
    ) -> SendResult:
        self.post_edit_calls += 1
        return SendResult(
            chat_id=chat_id,
            message_id=message_id,
            photo_file_id=content.photo,
            photo_unique_id=None,
        )

    async def edit_caption(
        self,
        *,
        chat_id: int,  # noqa: ARG002
        message_id: int,  # noqa: ARG002
        caption: str,  # noqa: ARG002
        keyboard=None,  # noqa: ANN001, ARG002
        parse_mode: str | None = None,  # noqa: ARG002
    ) -> None:
        self.post_edit_calls += 1

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:  # noqa: ARG002
        self.deleted_message_ids.append(message_id)


@dataclass
class _InstructionNotModifiedPublisher(_Publisher):
    edit_instruction_calls: int = 0

    async def edit_text(
        self,
        *,
        chat_id: int,  # noqa: ARG002
        message_id: int,  # noqa: ARG002
        text: str,  # noqa: ARG002
        keyboard=None,  # noqa: ANN001
        parse_mode: str | None = None,  # noqa: ARG002
        disable_web_page_preview: bool = False,  # noqa: ARG002
    ) -> None:
        if keyboard is not None:
            self.edit_instruction_calls += 1
            raise PublisherNotModified("message is not modified")
        await super().edit_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            keyboard=keyboard,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )


@pytest.mark.asyncio
async def test_ten_edit_cycles_do_not_accumulate_tail_messages() -> None:
    draft = Draft(
        id=1,
        state=DraftState.EDITING,
        normalized_url="https://example.com/article",
        domain="example.com",
        title_en="Title",
        post_text_ru="init",
        group_chat_id=-1001,
        topic_id=12,
        post_message_id=501,
        card_message_id=502,
    )
    settings_repo = _SettingsRepo(
        BotSettings(group_chat_id=-1001, editing_topic_id=12),
    )
    draft_repo = _DraftRepo(draft)
    edit_repo = _EditRepo()
    publisher = _Publisher()
    service = EditSessionService(
        publisher,
        settings_repo=settings_repo,
        draft_repo=draft_repo,
        edit_repo=edit_repo,
    )
    session = _Session()

    for idx in range(10):
        await service.start(session, draft_id=1, user_id=10)
        await service.apply_edit(
            session,
            EditPayload(
                chat_id=-1001,
                topic_id=12,
                user_id=10,
                message_id=700 + idx,
                text=f"Новый заголовок {idx}\n\nНовый текст {idx}",
                photo_file_id=None,
                photo_unique_id=None,
            ),
        )

    assert draft.post_text_ru == "Новый заголовок 9\n\nНовый текст 9"
    assert draft.post_message_id == 501
    assert draft.card_message_id == 502
    assert publisher.post_edit_calls == 10
    assert publisher.card_edit_calls == 10
    assert len(publisher.instruction_message_ids) == 10
    assert all(
        message_id in publisher.deleted_message_ids
        for message_id in publisher.instruction_message_ids
    )
    admin_message_ids = {700 + idx for idx in range(10)}
    assert admin_message_ids.issubset(set(publisher.deleted_message_ids))
    assert all(item.status == EditSessionStatus.COMPLETED for item in edit_repo.sessions)
    assert len(edit_repo.sessions) == 10


@pytest.mark.asyncio
async def test_apply_edit_rolls_back_new_post_if_card_create_fails() -> None:
    draft = Draft(
        id=2,
        state=DraftState.EDITING,
        normalized_url="https://example.com/article2",
        domain="example.com",
        title_en="Title",
        post_text_ru="init",
        group_chat_id=-1001,
        topic_id=12,
        post_message_id=None,
        card_message_id=None,
    )
    settings_repo = _SettingsRepo(
        BotSettings(group_chat_id=-1001, editing_topic_id=12),
    )
    draft_repo = _DraftRepo(draft)
    active_session = EditSession(
        id=1,
        draft_id=2,
        group_chat_id=-1001,
        topic_id=12,
        user_id=10,
        instruction_message_id=999,
        status=EditSessionStatus.ACTIVE,
        started_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    edit_repo = _EditRepo(sessions=[active_session], next_id=2)
    publisher = _Publisher(fail_card_send=True)
    service = EditSessionService(
        publisher,
        settings_repo=settings_repo,
        draft_repo=draft_repo,
        edit_repo=edit_repo,
    )
    session = _Session()

    with pytest.raises(RuntimeError, match="card send failed"):
        await service.apply_edit(
            session,
            EditPayload(
                chat_id=-1001,
                topic_id=12,
                user_id=10,
                message_id=800,
                text="Новый текст",
                photo_file_id=None,
                photo_unique_id=None,
            ),
        )

    assert draft.post_message_id is None
    assert draft.card_message_id is None
    assert 1000 in publisher.deleted_message_ids
    assert active_session.status == EditSessionStatus.ACTIVE


@pytest.mark.asyncio
async def test_start_ignores_not_modified_for_existing_instruction() -> None:
    draft = Draft(
        id=3,
        state=DraftState.EDITING,
        normalized_url="https://example.com/article3",
        domain="example.com",
        title_en="Title",
        post_text_ru="init",
        group_chat_id=-1001,
        topic_id=12,
    )
    settings_repo = _SettingsRepo(BotSettings(group_chat_id=-1001, editing_topic_id=12))
    draft_repo = _DraftRepo(draft)
    edit_repo = _EditRepo(
        sessions=[
            EditSession(
                id=1,
                draft_id=3,
                group_chat_id=-1001,
                topic_id=12,
                user_id=10,
                instruction_message_id=777,
                status=EditSessionStatus.ACTIVE,
                started_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
        ],
        next_id=2,
    )
    publisher = _InstructionNotModifiedPublisher(next_message_id=2000)
    service = EditSessionService(
        publisher,
        settings_repo=settings_repo,
        draft_repo=draft_repo,
        edit_repo=edit_repo,
    )
    session = _Session()

    await service.start(session, draft_id=3, user_id=10)

    active = await edit_repo.get_active_by_draft(session, 3)
    assert active is not None
    assert active.instruction_message_id == 777
    assert publisher.edit_instruction_calls == 1
    assert publisher.instruction_message_ids == []
