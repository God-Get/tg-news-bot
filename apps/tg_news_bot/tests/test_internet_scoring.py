from __future__ import annotations

import pytest

from tg_news_bot.config import InternetScoringSettings, TrendsSettings
from tg_news_bot.services.internet_scoring import InternetScoringService


@pytest.mark.asyncio
async def test_build_context_merges_db_google_and_wordstat_boosts() -> None:
    service = InternetScoringService(
        settings=InternetScoringSettings(
            max_signal_boost_per_keyword=1.0,
            max_signal_keywords=10,
            wordstat_keyword_boosts={"ai": 0.8, "quantum": 0.5},
            google_trends_enabled=True,
        ),
        trends_settings=TrendsSettings(),
        session_factory=object(),  # not used in this test
    )

    async def _fake_db() -> dict[str, float]:
        return {"ai": 0.7, "nvidia": 0.6}

    async def _fake_google() -> dict[str, float]:
        return {"ai": 0.9, "mars": 0.7}

    service._load_db_signal_boosts = _fake_db  # type: ignore[method-assign]
    service._load_google_trends_boosts = _fake_google  # type: ignore[method-assign]

    context = await service.build_context()

    assert context.signal_boosts["ai"] == pytest.approx(1.0)
    assert "mars" in context.signal_boosts
    assert "nvidia" in context.signal_boosts
    assert "quantum" in context.signal_boosts
    assert context.provider_stats["trend_db"] == 2
    assert context.provider_stats["google_trends"] == 2
    assert context.provider_stats["wordstat"] == 2


def test_score_item_applies_signal_trust_and_profile_components() -> None:
    service = InternetScoringService(
        settings=InternetScoringSettings(
            seed_hit_weight=1.0,
            exclude_hit_penalty=2.0,
            signal_keyword_multiplier=0.5,
            max_signal_boost_per_keyword=1.0,
            max_total_signal_boost=2.0,
            source_trust_multiplier=0.1,
            source_trust_boost_cap=1.0,
            source_weights={"ARXIV": 1.2},
            default_source_weight=0.0,
            google_trends_enabled=False,
        ),
        trends_settings=TrendsSettings(),
        session_factory=object(),
    )

    result = service.score_item(
        text="AI breakthrough from arxiv with nvidia benchmarks.",
        source_name="ARXIV",
        seed_hits=["ai", "breakthrough"],
        exclude_hits=["casino"],
        trusted_domain_match=True,
        source_trust_score=5.0,
        signal_boosts={"nvidia": 0.8, "openai": 0.6},
    )

    expected_total = 2.0 - 2.0 + 1.2 + 0.7 + 0.5 + 0.4
    assert result.total == pytest.approx(expected_total)
    assert result.components["profile_seed_hits"] == pytest.approx(2.0)
    assert result.components["profile_exclude_penalty"] == pytest.approx(-2.0)
    assert result.components["network_source_weight"] == pytest.approx(1.2)
    assert result.components["trusted_domain_bonus"] == pytest.approx(0.7)
    assert result.components["source_trust"] == pytest.approx(0.5)
    assert result.components["internet_signal_boost"] == pytest.approx(0.4)
    assert result.signal_hits == ["nvidia"]


def test_score_item_limits_signal_boost_by_total_cap() -> None:
    service = InternetScoringService(
        settings=InternetScoringSettings(
            source_weights={},
            default_source_weight=0.0,
            signal_keyword_multiplier=1.0,
            max_signal_boost_per_keyword=1.0,
            max_total_signal_boost=0.4,
            max_signal_matches_per_item=5,
            google_trends_enabled=False,
        ),
        trends_settings=TrendsSettings(),
        session_factory=object(),
    )

    result = service.score_item(
        text="openai ai openai",
        source_name="HN",
        seed_hits=[],
        exclude_hits=[],
        trusted_domain_match=False,
        source_trust_score=None,
        signal_boosts={"openai": 0.7, "ai": 0.6},
    )

    assert result.components["internet_signal_boost"] == pytest.approx(0.4)
    assert result.total == pytest.approx(0.4)
    assert result.signal_hits[0] == "openai"
