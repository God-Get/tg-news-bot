from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from tg_news_bot.services.ingestion import IngestionRunner


class _AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


class _Session:
    def begin(self) -> _AsyncContext:
        return _AsyncContext()


class _SessionFactory:
    def __call__(self) -> _SessionFactory:
        return self

    async def __aenter__(self) -> _Session:
        return _Session()

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


@dataclass
class _DraftRepo:
    draft_id: int = 0

    async def get_by_normalized_url(self, session, url: str):  # noqa: ANN001, ARG002
        if self.draft_id <= 0:
            return None
        return SimpleNamespace(id=self.draft_id)


class _MissingSourceRepo:
    async def get_by_id(self, session, source_id: int):  # noqa: ANN001, ARG002
        return None


@pytest.mark.asyncio
async def test_ingest_url_returns_invalid_url_reason() -> None:
    runner = object.__new__(IngestionRunner)
    runner._normalized_url_candidates = lambda url, entry_id=None: []  # noqa: ARG005

    result = await runner.ingest_url(url="bad")

    assert result.created is False
    assert result.reason == "invalid_url"


@pytest.mark.asyncio
async def test_ingest_url_returns_created_result() -> None:
    runner = object.__new__(IngestionRunner)
    runner._normalized_url_candidates = (
        lambda url, entry_id=None: ["https://example.com/news/1"]  # noqa: ARG005
    )
    runner._session_factory = _SessionFactory()
    runner._draft_repo = _DraftRepo(draft_id=55)

    async def _process_entry(source_id, entry, topic_hints, http, stats):  # noqa: ANN001, ARG001
        return True

    runner._process_entry = _process_entry

    result = await runner.ingest_url(url="https://example.com/news/1")

    assert result.created is True
    assert result.draft_id == 55
    assert result.normalized_url == "https://example.com/news/1"


@pytest.mark.asyncio
async def test_ingest_url_maps_duplicate_reason() -> None:
    runner = object.__new__(IngestionRunner)
    runner._normalized_url_candidates = (
        lambda url, entry_id=None: ["https://example.com/news/1"]  # noqa: ARG005
    )

    async def _process_entry(source_id, entry, topic_hints, http, stats):  # noqa: ANN001, ARG001
        stats.duplicates = 1
        return False

    runner._process_entry = _process_entry

    result = await runner.ingest_url(url="https://example.com/news/1")

    assert result.created is False
    assert result.reason == "duplicate"


@pytest.mark.asyncio
async def test_ingest_url_returns_source_not_found_reason() -> None:
    runner = object.__new__(IngestionRunner)
    runner._normalized_url_candidates = (
        lambda url, entry_id=None: ["https://example.com/news/1"]  # noqa: ARG005
    )
    runner._session_factory = _SessionFactory()
    runner._sources_repo = _MissingSourceRepo()

    result = await runner.ingest_url(url="https://example.com/news/1", source_id=10)

    assert result.created is False
    assert result.reason == "source_not_found"
