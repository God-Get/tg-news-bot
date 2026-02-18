from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from tg_news_bot.config import TrendsSettings
from tg_news_bot.services.trends import TrendCollector, _extract_keywords


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
    rows: list[tuple[str, float]]

    async def list_recent_keyword_scores(self, session, *, since, limit):  # noqa: ANN001, ARG002
        return self.rows[:limit]

    async def list_recent(self, session, *, since, limit):  # noqa: ANN001, ARG002
        return []

    async def add_many(self, session, *, items):  # noqa: ANN001, ARG002
        return len(items)

    async def delete_older_than(self, session, *, before):  # noqa: ANN001, ARG002
        return 0


def test_extract_keywords_filters_noise() -> None:
    settings = TrendsSettings(min_keyword_length=3, max_keyword_length=20)
    result = _extract_keywords(
        "The NASA launch and AI model update in 2026",
        settings,
    )

    assert "nasa" in result
    assert "launch" in result
    assert "model" in result
    assert "the" not in result
    assert "2026" not in result


@pytest.mark.asyncio
async def test_trend_collector_returns_boosts_from_recent_scores() -> None:
    collector = TrendCollector(
        settings=TrendsSettings(enabled=True, max_boost_per_keyword=2.0),
        session_factory=_SessionFactory(),
        repository=_RepoStub(rows=[("openai", 7.0), ("nasa", 4.0)]),
    )

    boosts = await collector.get_keyword_boosts(max_items=10)

    assert boosts["openai"] == pytest.approx(0.7)
    assert boosts["nasa"] == pytest.approx(0.4)
    assert collector._cached_at is not None  # noqa: SLF001
