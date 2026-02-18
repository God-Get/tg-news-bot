from __future__ import annotations

from tg_news_bot.config import ContentSafetySettings
from tg_news_bot.services.content_safety import ContentSafetyService


def test_content_safety_blocks_advertising_patterns() -> None:
    service = ContentSafetyService(ContentSafetySettings(min_ready_chars=20))

    result = service.check(
        title="AI update",
        text="Limited offer sponsored post. Buy now and subscribe now.",
    )

    assert result.allowed is False
    assert any(item.startswith("ad:") for item in result.reasons)


def test_content_safety_allows_normal_science_text() -> None:
    service = ContentSafetyService(ContentSafetySettings(min_ready_chars=20))

    result = service.check(
        title="Space research",
        text=(
            "Researchers published a detailed report on orbital debris mitigation "
            "with reproducible methods and transparent assumptions."
        ),
    )

    assert result.allowed is True
