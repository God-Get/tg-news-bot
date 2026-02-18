from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from telegram_publisher.types import PostContent, SendResult
from tg_news_bot.adapters.publisher import PublisherAdapter


@dataclass
class _PublisherSpy:
    calls: list[tuple[str, dict]] = field(default_factory=list)

    async def send_post(self, **kwargs):  # noqa: ANN003
        self.calls.append(("send_post", kwargs))
        return SendResult(chat_id=kwargs["chat_id"], message_id=101)

    async def send_text(self, **kwargs):  # noqa: ANN003
        self.calls.append(("send_text", kwargs))
        return SendResult(chat_id=kwargs["chat_id"], message_id=102)

    async def edit_post(self, **kwargs):  # noqa: ANN003
        self.calls.append(("edit_post", kwargs))
        return SendResult(chat_id=kwargs["chat_id"], message_id=kwargs["message_id"])

    async def edit_text(self, **kwargs):  # noqa: ANN003
        self.calls.append(("edit_text", kwargs))

    async def edit_caption(self, **kwargs):  # noqa: ANN003
        self.calls.append(("edit_caption", kwargs))

    async def edit_reply_markup(self, **kwargs):  # noqa: ANN003
        self.calls.append(("edit_reply_markup", kwargs))

    async def delete_message(self, **kwargs):  # noqa: ANN003
        self.calls.append(("delete_message", kwargs))


@pytest.mark.asyncio
async def test_publisher_adapter_delegates_send_and_edit_calls() -> None:
    spy = _PublisherSpy()
    adapter = PublisherAdapter(spy)  # type: ignore[arg-type]

    content = PostContent(text="hello", photo=None, parse_mode="HTML")

    post_result = await adapter.send_post(
        chat_id=-1001,
        topic_id=12,
        content=content,
        keyboard=None,
    )
    text_result = await adapter.send_text(
        chat_id=-1001,
        topic_id=12,
        text="text",
        keyboard=None,
        parse_mode=None,
    )
    await adapter.edit_post(
        chat_id=-1001,
        message_id=77,
        content=content,
        keyboard=None,
    )
    await adapter.edit_text(
        chat_id=-1001,
        message_id=77,
        text="updated",
        keyboard=None,
        parse_mode=None,
        disable_web_page_preview=True,
    )
    await adapter.edit_caption(
        chat_id=-1001,
        message_id=77,
        caption="cap",
        keyboard=None,
        parse_mode="HTML",
    )
    await adapter.edit_reply_markup(
        chat_id=-1001,
        message_id=77,
        keyboard=None,
    )
    await adapter.delete_message(chat_id=-1001, message_id=77)

    assert post_result.message_id == 101
    assert text_result.message_id == 102
    assert [name for name, _ in spy.calls] == [
        "send_post",
        "send_text",
        "edit_post",
        "edit_text",
        "edit_caption",
        "edit_reply_markup",
        "delete_message",
    ]
