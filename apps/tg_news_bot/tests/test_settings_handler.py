from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from tg_news_bot.telegram.handlers.settings import SettingsContext, create_settings_router


@dataclass
class _PublisherSpy:
    sent: list[dict] = field(default_factory=list)

    async def send_text(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        text: str,
        parse_mode=None,  # noqa: ANN001
        keyboard=None,  # noqa: ANN001
    ) -> None:
        self.sent.append(
            {
                "chat_id": chat_id,
                "topic_id": topic_id,
                "text": text,
            }
        )


@dataclass
class _WorkflowSpy:
    errors: dict[int, Exception] = field(default_factory=dict)
    calls: list[int] = field(default_factory=list)

    async def process_editing_text(self, *, draft_id: int) -> None:
        self.calls.append(draft_id)
        error = self.errors.get(draft_id)
        if error:
            raise error


@dataclass
class _Message:
    user_id: int = 10
    chat_id: int = -1001
    topic_id: int | None = 7

    @property
    def from_user(self):
        return SimpleNamespace(id=self.user_id)

    @property
    def chat(self):
        return SimpleNamespace(id=self.chat_id)

    @property
    def message_thread_id(self):
        return self.topic_id


def _process_range_handler(*, publisher: _PublisherSpy, workflow: _WorkflowSpy):
    context = SettingsContext(
        settings=SimpleNamespace(admin_user_id=10),
        session_factory=SimpleNamespace(),
        repository=SimpleNamespace(),
        source_repository=SimpleNamespace(),
        publisher=publisher,
        ingestion_runner=None,
        workflow=workflow,
    )
    router = create_settings_router(context)
    for handler in router.message.handlers:
        if handler.callback.__name__ == "process_range":
            return handler.callback
    raise AssertionError("process_range handler not found")


@pytest.mark.asyncio
async def test_process_range_reports_summary() -> None:
    publisher = _PublisherSpy()
    workflow = _WorkflowSpy(
        errors={
            2: ValueError("processing is available only for EDITING drafts"),
            3: LookupError("not found"),
            4: RuntimeError("boom"),
        }
    )
    handler = _process_range_handler(publisher=publisher, workflow=workflow)

    await handler(_Message(), SimpleNamespace(args="1 4"))

    assert workflow.calls == [1, 2, 3, 4]
    assert len(publisher.sent) == 2
    assert publisher.sent[0]["text"] == "Запускаю выжимку и перевод для Draft #1..#4"
    summary = publisher.sent[1]["text"]
    assert "Обработано: 1" in summary
    assert "Пропущено (нет Draft): 1" in summary
    assert "Пропущено (не в EDITING): 1" in summary
    assert "Ошибки: 1" in summary
    assert "Draft с ошибками: 4" in summary


@pytest.mark.asyncio
async def test_process_range_requires_valid_args() -> None:
    publisher = _PublisherSpy()
    workflow = _WorkflowSpy()
    handler = _process_range_handler(publisher=publisher, workflow=workflow)

    await handler(_Message(), SimpleNamespace(args="abc"))

    assert workflow.calls == []
    assert len(publisher.sent) == 1
    assert publisher.sent[0]["text"] == "Нужно указать два положительных числовых draft_id."


@pytest.mark.asyncio
async def test_process_range_ignores_non_admin() -> None:
    publisher = _PublisherSpy()
    workflow = _WorkflowSpy()
    handler = _process_range_handler(publisher=publisher, workflow=workflow)

    await handler(_Message(user_id=999), SimpleNamespace(args="1 3"))

    assert workflow.calls == []
    assert publisher.sent == []
