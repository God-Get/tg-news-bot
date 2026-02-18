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
class _IngestionRunnerSpy:
    result: object | None = None
    calls: list[dict] = field(default_factory=list)

    async def ingest_url(self, *, url: str, source_id=None, topic_hints=None):  # noqa: ANN001
        self.calls.append(
            {
                "url": url,
                "source_id": source_id,
                "topic_hints": topic_hints,
            }
        )
        return self.result


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


def _router_and_handler_by_name(
    name: str,
    *,
    publisher: _PublisherSpy,
    ingestion: _IngestionRunnerSpy,
):
    context = SettingsContext(
        settings=SimpleNamespace(admin_user_id=10),
        session_factory=SimpleNamespace(),
        repository=SimpleNamespace(),
        source_repository=SimpleNamespace(),
        publisher=publisher,
        ingestion_runner=ingestion,
        workflow=SimpleNamespace(),
    )
    router = create_settings_router(context)
    for handler in router.message.handlers:
        if handler.callback.__name__ == name:
            return router, handler.callback
    raise AssertionError(f"handler not found: {name}")


@pytest.mark.asyncio
async def test_commands_help_contains_syntax_lines() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    _, handler = _router_and_handler_by_name(
        "commands_help",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message())

    assert len(publisher.sent) == 1
    text = publisher.sent[0]["text"]
    assert "/commands" in text
    assert "/ingest_url <article_url> [source_id]" in text
    assert "/process_range <from_id> <to_id>" in text
    assert "/cancel" in text


@pytest.mark.asyncio
async def test_commands_help_lists_all_router_commands() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    router, handler = _router_and_handler_by_name(
        "commands_help",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message())

    text = publisher.sent[0]["text"]
    command_names: set[str] = set()
    for handler_obj in router.message.handlers:
        for filter_obj in handler_obj.filters:
            command_filter = getattr(filter_obj, "callback", None)
            names = getattr(command_filter, "commands", None)
            if not names:
                continue
            for name in names:
                command_names.add(str(name).strip().lstrip("/").lower())

    for command_name in command_names:
        assert f"/{command_name}" in text


@pytest.mark.asyncio
async def test_ingest_url_creates_draft_message() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy(
        result=SimpleNamespace(
            created=True,
            draft_id=321,
            normalized_url="https://example.com/news/1",
            reason=None,
        )
    )
    _, handler = _router_and_handler_by_name(
        "ingest_url",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message(), SimpleNamespace(args="https://example.com/news/1"))

    assert ingestion.calls == [
        {
            "url": "https://example.com/news/1",
            "source_id": None,
            "topic_hints": None,
        }
    ]
    assert len(publisher.sent) == 2
    assert "ссылке" in publisher.sent[0]["text"].lower()
    assert "Draft #321" in publisher.sent[1]["text"]


@pytest.mark.asyncio
async def test_ingest_url_reports_duplicate() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy(
        result=SimpleNamespace(
            created=False,
            draft_id=None,
            normalized_url="https://example.com/news/1",
            reason="duplicate",
        )
    )
    _, handler = _router_and_handler_by_name(
        "ingest_url",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message(), SimpleNamespace(args="https://example.com/news/1"))

    assert len(publisher.sent) == 2
    assert "дубликат" in publisher.sent[1]["text"].lower()


@pytest.mark.asyncio
async def test_ingest_url_rejects_invalid_url() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    _, handler = _router_and_handler_by_name(
        "ingest_url",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message(), SimpleNamespace(args="not-a-url"))

    assert ingestion.calls == []
    assert len(publisher.sent) == 1
    assert "url" in publisher.sent[0]["text"].lower()


@pytest.mark.asyncio
async def test_ingest_url_accepts_optional_source_id() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy(
        result=SimpleNamespace(
            created=True,
            draft_id=222,
            normalized_url="https://example.com/news/2",
            reason=None,
        )
    )
    _, handler = _router_and_handler_by_name(
        "ingest_url",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message(), SimpleNamespace(args="https://example.com/news/2 3"))

    assert ingestion.calls == [
        {
            "url": "https://example.com/news/2",
            "source_id": 3,
            "topic_hints": None,
        }
    ]
    assert "source #3" in publisher.sent[0]["text"]
