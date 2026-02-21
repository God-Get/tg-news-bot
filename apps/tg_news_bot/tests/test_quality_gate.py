from __future__ import annotations

from tg_news_bot.config import QualityGateSettings
from tg_news_bot.services.quality_gate import QualityGateService


def test_quality_gate_keeps_good_text_and_removes_noise() -> None:
    service = QualityGateService(QualityGateSettings(min_words=8, min_meaningful_chars=40))
    result = service.evaluate(
        current_text=(
            "Thank you for visiting nature.com. You are using a browser version with limited support for CSS.\n"
            "A team from MIT demonstrated a new battery chemistry with higher cycle life and lower cost."
        ),
        title="Battery breakthrough",
        source_text=None,
    )

    assert result.status == "ok"
    assert "nature.com" not in result.text.lower()
    assert "battery chemistry" in result.text.lower()


def test_quality_gate_uses_fallback_from_source_when_post_too_short() -> None:
    service = QualityGateService(QualityGateSettings(min_words=10, min_meaningful_chars=50))
    result = service.evaluate(
        current_text="Short note.",
        title="Quantum processor update",
        source_text=(
            "Researchers reported a new qubit control method that reduced errors and improved stability "
            "across long-running workloads in cryogenic systems."
        ),
    )

    assert result.status == "fallback"
    assert "quantum processor update" in result.text.lower()
    assert "qubit control method" in result.text.lower()


def test_quality_gate_rejects_when_both_current_and_source_are_empty() -> None:
    service = QualityGateService(QualityGateSettings(min_words=10, min_meaningful_chars=50))
    result = service.evaluate(
        current_text="Access options. Subscribe to this journal.",
        title="",
        source_text="References doi:10.1000/test",
    )

    assert result.status == "reject"
    assert result.should_archive is True
