from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from tg_news_bot.config import SemanticDedupSettings
from tg_news_bot.repositories.semantic_fingerprints import FingerprintCandidate
from tg_news_bot.services.semantic_dedup import SemanticDedupService


class _AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None


class _Session:
    def begin(self):
        return _AsyncContext()


class _SessionFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return _Session()

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None


@dataclass
class _RepoStub:
    candidates: list[FingerprintCandidate] = field(default_factory=list)

    async def list_recent_candidates(self, session, *, since, domain, limit):  # noqa: ANN001, ARG002
        if domain is None:
            return self.candidates[:limit]
        filtered = [item for item in self.candidates if item.domain == domain]
        return filtered[:limit]

    async def upsert(self, session, *, normalized_url, domain, vector, text_hash):  # noqa: ANN001, ARG002
        return None


@pytest.mark.asyncio
async def test_semantic_dedup_detects_near_duplicate() -> None:
    settings = SemanticDedupSettings(enabled=True, similarity_threshold=0.9, dimensions=64)
    repo = _RepoStub()
    service = SemanticDedupService(
        settings=settings,
        session_factory=_SessionFactory(),
        repository=repo,
    )
    vector, text_hash = service._make_embedding(  # noqa: SLF001
        title="OpenAI releases model",
        text="OpenAI releases model for science applications",
    )
    repo.candidates.append(
        FingerprintCandidate(
            normalized_url="https://example.com/prev",
            domain="example.com",
            vector=vector,
            text_hash=text_hash,
            created_at=datetime.now(timezone.utc),
        )
    )

    match = await service.find_near_duplicate(
        normalized_url="https://example.com/new",
        domain="example.com",
        title="OpenAI releases model",
        text="OpenAI releases model for science applications",
    )

    assert match is not None
    assert match.normalized_url == "https://example.com/prev"
    assert match.similarity >= 0.99


@pytest.mark.asyncio
async def test_semantic_dedup_returns_none_for_different_text() -> None:
    settings = SemanticDedupSettings(enabled=True, similarity_threshold=0.95, dimensions=64)
    repo = _RepoStub()
    service = SemanticDedupService(
        settings=settings,
        session_factory=_SessionFactory(),
        repository=repo,
    )
    vector, _ = service._make_embedding(  # noqa: SLF001
        title="Quantum material",
        text="Materials research in superconductors",
    )
    repo.candidates.append(
        FingerprintCandidate(
            normalized_url="https://example.com/old",
            domain="example.com",
            vector=vector,
            text_hash="other",
            created_at=datetime.now(timezone.utc),
        )
    )

    match = await service.find_near_duplicate(
        normalized_url="https://example.com/new",
        domain="example.com",
        title="Space launch",
        text="NASA mission launched to lunar orbit",
    )

    assert match is None
