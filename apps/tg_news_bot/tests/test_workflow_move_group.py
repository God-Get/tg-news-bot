from __future__ import annotations

from dataclasses import dataclass

import pytest

from telegram_publisher.types import SendResult
from tg_news_bot.db.models import BotSettings, Draft, DraftState
from tg_news_bot.services.workflow import DraftWorkflowService


class _Session:
    async def flush(self) -> None:
        return None


class _SessionFactory:
    def __call__(self):
        return self

    async def __aenter__(self):  # pragma: no cover - not used in this test
        return _Session()

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None


@dataclass
class _ScheduledRepoStub:
    async def get_by_draft(self, session, draft_id: int):  # noqa: ANN001, ARG002
        return None


@dataclass
class _PublisherCardFailSpy:
    deleted: list[tuple[int, int]]

    async def send_post(self, *, chat_id: int, topic_id: int | None, content, keyboard):  # noqa: ANN001
        return SendResult(chat_id=chat_id, message_id=501)

    async def send_text(self, *, chat_id: int, topic_id: int | None, text: str, keyboard, parse_mode):  # noqa: ANN001, ARG002
        raise RuntimeError("card send failed")

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))


@pytest.mark.asyncio
async def test_move_in_group_rolls_back_post_when_card_send_fails() -> None:
    publisher = _PublisherCardFailSpy(deleted=[])
    workflow = DraftWorkflowService(
        session_factory=_SessionFactory(),
        publisher=publisher,
        scheduled_repo=_ScheduledRepoStub(),
    )
    draft = Draft(
        id=1,
        state=DraftState.INBOX,
        normalized_url="https://example.com/item",
        domain="example.com",
        title_en="title",
        post_text_ru="text",
        post_message_id=None,
        card_message_id=None,
    )
    settings = BotSettings(group_chat_id=-1001, ready_topic_id=13)

    with pytest.raises(RuntimeError, match="card send failed"):
        await workflow._move_in_group(
            session=_Session(),
            draft=draft,
            settings=settings,
            target_state=DraftState.READY,
        )

    assert publisher.deleted == [(-1001, 501)]
    assert draft.post_message_id is None
    assert draft.card_message_id is None
