from datetime import datetime, timedelta, timezone

import pytest

from tg_news_bot.config import ScoringSettings
from tg_news_bot.services.scoring import ScoringService


def test_scoring_with_boosts_and_freshness() -> None:
    settings = ScoringSettings(
        min_length_chars=200,
        max_length_chars=500,
        freshness_hours=24,
        min_score=0.0,
        keyword_boosts={"ai": 2.0},
        domain_boosts={"example.com": 1.0},
    )
    service = ScoringService(settings)
    published_at = datetime.now(timezone.utc) - timedelta(hours=1)

    result = service.score(
        text="AI is here",
        title="News",
        domain="news.example.com",
        published_at=published_at,
    )

    assert result.score == pytest.approx(3.0)
    assert result.reasons["kw:ai"] == 2.0
    assert result.reasons["domain:example.com"] == 1.0


def test_scoring_without_text_is_rejected() -> None:
    settings = ScoringSettings()
    service = ScoringService(settings)

    result = service.score(text=None, title=None, domain=None, published_at=None)

    assert result.score == -2.0
    assert result.reasons["no_text"] == -2.0


def test_scoring_applies_title_keyword_multiplier() -> None:
    settings = ScoringSettings(
        min_length_chars=200,
        max_length_chars=5000,
        freshness_hours=24,
        min_score=0.0,
        keyword_boosts={"nasa": 1.0},
        title_keyword_multiplier=2.0,
    )
    service = ScoringService(settings)
    published_at = datetime.now(timezone.utc) - timedelta(hours=2)

    result = service.score(
        text="Regular body text without boost words.",
        title="NASA announced a new mission",
        domain="example.com",
        published_at=published_at,
    )

    assert result.reasons["kw:nasa"] == pytest.approx(2.0)
    assert result.reasons["kw_title:nasa"] == pytest.approx(2.0)


def test_scoring_short_text_uses_soft_penalty() -> None:
    settings = ScoringSettings(
        min_length_chars=1000,
        max_length_chars=5000,
        freshness_hours=24,
        min_score=0.0,
    )
    service = ScoringService(settings)
    published_at = datetime.now(timezone.utc) - timedelta(hours=1)

    result = service.score(
        text="x" * 800,
        title="Some title",
        domain="example.com",
        published_at=published_at,
    )

    assert result.reasons["length_penalty"] == pytest.approx(-0.2)


def test_scoring_applies_trend_and_trust_boosts() -> None:
    settings = ScoringSettings(
        min_length_chars=200,
        max_length_chars=5000,
        freshness_hours=24,
        min_score=0.0,
    )
    service = ScoringService(settings)

    result = service.score(
        text=(
            "NVIDIA and OpenAI announce new AI inference stack. "
            + ("Detailed benchmark data and deployment notes. " * 10)
        ),
        title="AI news",
        domain="example.com",
        published_at=None,
        trend_boosts={"nvidia": 0.4, "openai": 0.5},
        source_trust_score=2.0,
    )

    assert result.reasons["trend:nvidia"] == pytest.approx(0.4)
    assert result.reasons["trend:openai"] == pytest.approx(0.5)
    assert result.reasons["source_trust"] == pytest.approx(0.3)
