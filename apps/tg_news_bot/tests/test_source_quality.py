from __future__ import annotations

from dataclasses import dataclass

import pytest

from tg_news_bot.config import SourceQualitySettings
from tg_news_bot.services.source_quality import SourceQualityService


class _AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None


class _Session:
    async def flush(self) -> None:
        return None


@dataclass
class _Source:
    id: int
    enabled: bool = True
    trust_score: float = 0.0
    tags: dict | None = None
    name: str = "source"


@dataclass
class _SourceRepoStub:
    source: _Source | None

    async def get_by_id(self, session, source_id: int):  # noqa: ANN001, ARG002
        if self.source and self.source.id == source_id:
            return self.source
        return None


@pytest.mark.asyncio
async def test_source_quality_updates_trust_score() -> None:
    source = _Source(id=1, trust_score=0.0, tags={})
    service = SourceQualityService(
        SourceQualitySettings(enabled=True, auto_disable_enabled=False),
        source_repo=_SourceRepoStub(source),
    )

    result = await service.apply_event(_Session(), source_id=1, event="created")

    assert result is not None
    assert result.source_name == "source"
    assert source.trust_score > 0
    assert isinstance(source.tags, dict)
    assert source.tags["quality"]["events_total"] == 1


@pytest.mark.asyncio
async def test_source_quality_can_auto_disable_low_trust_source() -> None:
    source = _Source(
        id=2,
        enabled=True,
        trust_score=-3.9,
        tags={"quality": {"events_total": 12, "events": {}}},
    )
    service = SourceQualityService(
        SourceQualitySettings(
            enabled=True,
            auto_disable_enabled=True,
            auto_disable_threshold=-4.0,
            min_events_for_auto_disable=10,
            blocked_delta=-0.8,
        ),
        source_repo=_SourceRepoStub(source),
    )

    result = await service.apply_event(_Session(), source_id=2, event="blocked")

    assert result is not None
    assert result.auto_disabled is True
    assert source.enabled is False


@pytest.mark.asyncio
async def test_source_quality_auto_disables_on_consecutive_failures() -> None:
    source = _Source(
        id=3,
        enabled=True,
        trust_score=0.2,
        tags={"quality": {"events_total": 1, "events": {}, "health": {"consecutive_failures": 2}}},
    )
    service = SourceQualityService(
        SourceQualitySettings(
            enabled=True,
            auto_disable_enabled=True,
            consecutive_failures_disable_threshold=3,
            min_events_for_auto_disable=100,
            rss_http_error_delta=-0.4,
        ),
        source_repo=_SourceRepoStub(source),
    )

    result = await service.apply_event(_Session(), source_id=3, event="rss_http_error")

    assert result is not None
    assert result.auto_disabled is True
    assert result.consecutive_failures >= 3
    assert source.enabled is False
