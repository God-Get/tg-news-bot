from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from tg_news_bot.telegram.handlers.schedule_input import (
    ScheduleInputContext,
    create_schedule_input_router,
)


@dataclass
class _ScheduleInputSpy:
    result: object | None = None
    calls: list[dict] = field(default_factory=list)

    async def process_message(self, *, chat_id: int, topic_id: int, user_id: int, text: str):
        self.calls.append(
            {
                "chat_id": chat_id,
                "topic_id": topic_id,
                "user_id": user_id,
                "text": text,
            }
        )
        return self.result


@dataclass
class _PublisherSpy:
    deleted: list[tuple[int, int]] = field(default_factory=list)
    sent: list[dict] = field(default_factory=list)

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))

    async def send_text(self, *, chat_id: int, topic_id: int | None, text: str, parse_mode=None, keyboard=None):  # noqa: ANN001
        self.sent.append({"chat_id": chat_id, "topic_id": topic_id, "text": text})


@dataclass
class _Result:
    accepted: bool
    message: str


@dataclass
class _Message:
    text: str | None
    user_id: int = 10
    chat_id: int = -1001
    topic_id: int | None = 22
    message_id: int = 77

    @property
    def from_user(self):
        return SimpleNamespace(id=self.user_id)

    @property
    def chat(self):
        return SimpleNamespace(id=self.chat_id)

    @property
    def message_thread_id(self):
        return self.topic_id


def _handler(schedule_input: _ScheduleInputSpy, publisher: _PublisherSpy):
    context = ScheduleInputContext(
        settings=SimpleNamespace(admin_user_id=10),
        schedule_input=schedule_input,
        publisher=publisher,
    )
    router = create_schedule_input_router(context)
    return router.message.handlers[0].callback


@pytest.mark.asyncio
async def test_schedule_input_accepted_deletes_message() -> None:
    schedule_input = _ScheduleInputSpy(result=_Result(accepted=True, message="ok"))
    publisher = _PublisherSpy()
    handler = _handler(schedule_input, publisher)

    await handler(_Message(text="17.02.2026 10:00"))

    assert len(schedule_input.calls) == 1
    assert publisher.deleted == [(-1001, 77)]
    assert publisher.sent == []


@pytest.mark.asyncio
async def test_schedule_input_error_sends_feedback() -> None:
    schedule_input = _ScheduleInputSpy(result=_Result(accepted=False, message="Формат даты"))
    publisher = _PublisherSpy()
    handler = _handler(schedule_input, publisher)

    await handler(_Message(text="bad"))

    assert publisher.deleted == []
    assert publisher.sent == [{"chat_id": -1001, "topic_id": 22, "text": "Формат даты"}]


@pytest.mark.asyncio
async def test_schedule_input_ignores_non_admin_or_no_session() -> None:
    schedule_input = _ScheduleInputSpy(result=None)
    publisher = _PublisherSpy()
    handler = _handler(schedule_input, publisher)

    await handler(_Message(text="17.02.2026 10:00", user_id=999))
    await handler(_Message(text=None, user_id=10))
    await handler(_Message(text="17.02.2026 10:00", topic_id=None))
    await handler(_Message(text="17.02.2026 10:00", user_id=10))

    assert len(schedule_input.calls) == 1
    assert publisher.deleted == []
    assert publisher.sent == []
