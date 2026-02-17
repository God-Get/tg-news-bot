from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from telegram_publisher.types import SendResult
from tg_news_bot.config import PostFormattingSettings
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
class _PublisherSpy:
    sent_keyboard: object | None = None

    async def send_post(self, *, chat_id: int, topic_id: int | None, content, keyboard):  # noqa: ANN001
        self.sent_keyboard = keyboard
        return SendResult(chat_id=chat_id, message_id=777)


@pytest.mark.asyncio
async def test_publish_now_adds_source_button_when_source_mode_button() -> None:
    publisher = _PublisherSpy()
    workflow = DraftWorkflowService(
        session_factory=_SessionFactory(),
        publisher=publisher,
        post_formatting=PostFormattingSettings(source_mode="button"),
    )
    draft = Draft(
        id=1,
        state=DraftState.READY,
        normalized_url="https://example.com/item",
        domain="example.com",
        title_en="title",
        post_text_ru="text",
    )
    settings = BotSettings(channel_id=-100777)

    await workflow._publish_now(_Session(), draft, settings)

    assert publisher.sent_keyboard is not None
    button = publisher.sent_keyboard.inline_keyboard[0][0]
    assert button.url == "https://example.com/item"
    assert draft.published_message_id == 777
    assert isinstance(draft.published_at, datetime)
    assert draft.published_at.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_publish_now_adds_discussion_button_when_configured() -> None:
    publisher = _PublisherSpy()
    workflow = DraftWorkflowService(
        session_factory=_SessionFactory(),
        publisher=publisher,
        post_formatting=PostFormattingSettings(
            source_mode="button",
            discussion_label="Обсудить",
            discussion_url="https://t.me/my_discussion_group",
        ),
    )
    draft = Draft(
        id=2,
        state=DraftState.READY,
        normalized_url="https://example.com/item2",
        domain="example.com",
        title_en="title",
        post_text_ru="text",
    )
    settings = BotSettings(channel_id=-100777)

    await workflow._publish_now(_Session(), draft, settings)

    assert publisher.sent_keyboard is not None
    row = publisher.sent_keyboard.inline_keyboard[0]
    assert len(row) == 2
    assert row[0].url == "https://example.com/item2"
    assert row[1].text == "Обсудить"
    assert row[1].url == "https://t.me/my_discussion_group"
