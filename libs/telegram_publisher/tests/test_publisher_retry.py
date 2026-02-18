from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import DeleteMessage, SendMessage

from telegram_publisher.exceptions import PublisherEditNotAllowed, PublisherNotFound
from telegram_publisher.publisher import TelegramPublisher


def _retry_after_exception() -> TelegramRetryAfter:
    method = SendMessage(chat_id=1, text="x")
    return TelegramRetryAfter(method=method, message="retry", retry_after=1)


def _bad_request_exception(message: str) -> TelegramBadRequest:
    method = DeleteMessage(chat_id=1, message_id=1)
    return TelegramBadRequest(method=method, message=message)


@pytest.mark.asyncio
async def test_send_text_retries_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    async def fake_sleep(_: float) -> None:
        return None

    async def send_message(**kwargs):  # noqa: ANN003
        calls["count"] += 1
        if calls["count"] == 1:
            raise _retry_after_exception()
        return SimpleNamespace(chat=SimpleNamespace(id=kwargs["chat_id"]), message_id=42)

    bot = SimpleNamespace(send_message=send_message)
    publisher = TelegramPublisher(bot, max_retry_after_attempts=3)
    monkeypatch.setattr("telegram_publisher.publisher.asyncio.sleep", fake_sleep)

    result = await publisher.send_text(
        chat_id=123,
        topic_id=7,
        text="hello",
        keyboard=None,
        parse_mode=None,
    )

    assert calls["count"] == 2
    assert result.chat_id == 123
    assert result.message_id == 42


@pytest.mark.asyncio
async def test_send_text_retries_and_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    async def fake_sleep(_: float) -> None:
        return None

    async def send_message(**kwargs):  # noqa: ANN003, ARG001
        calls["count"] += 1
        raise _retry_after_exception()

    bot = SimpleNamespace(send_message=send_message)
    publisher = TelegramPublisher(bot, max_retry_after_attempts=2)
    monkeypatch.setattr("telegram_publisher.publisher.asyncio.sleep", fake_sleep)

    with pytest.raises(TelegramRetryAfter):
        await publisher.send_text(
            chat_id=123,
            topic_id=7,
            text="hello",
            keyboard=None,
            parse_mode=None,
        )
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_delete_message_maps_cant_be_deleted_to_edit_not_allowed() -> None:
    async def delete_message(**kwargs):  # noqa: ANN003, ARG001
        raise _bad_request_exception("Bad Request: message can't be deleted")

    bot = SimpleNamespace(delete_message=delete_message)
    publisher = TelegramPublisher(bot)

    with pytest.raises(PublisherEditNotAllowed):
        await publisher.delete_message(chat_id=123, message_id=77)


@pytest.mark.asyncio
async def test_edit_text_maps_message_id_invalid_to_not_found() -> None:
    async def edit_message_text(**kwargs):  # noqa: ANN003, ARG001
        raise _bad_request_exception("Bad Request: MESSAGE_ID_INVALID")

    bot = SimpleNamespace(edit_message_text=edit_message_text)
    publisher = TelegramPublisher(bot)

    with pytest.raises(PublisherNotFound):
        await publisher.edit_text(
            chat_id=123,
            message_id=77,
            text="x",
            keyboard=None,
            parse_mode=None,
        )
