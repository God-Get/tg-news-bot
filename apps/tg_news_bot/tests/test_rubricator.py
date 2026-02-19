from __future__ import annotations

from tg_news_bot.services.rubricator import RubricatorService


def test_rubricator_adds_russian_topic_aliases() -> None:
    service = RubricatorService()

    result = service.classify(
        title="AI research for space missions",
        text="Scientists discuss machine learning in NASA programs.",
        trend_keywords=None,
        limit=8,
    )

    assert "#ai" in result.hashtags
    assert "#ии" in result.hashtags
    assert "#space" in result.hashtags
    assert "#космос" in result.hashtags


def test_rubricator_supports_cyrillic_trend_keywords() -> None:
    service = RubricatorService()

    result = service.classify(
        title="",
        text="",
        trend_keywords=["Квантовая энергия"],
        limit=6,
    )

    assert "#квантовая_энергия" in result.hashtags


def test_rubricator_supports_ru_mode() -> None:
    service = RubricatorService()

    result = service.classify(
        title="AI and machine learning",
        text="",
        trend_keywords=["technology"],
        hashtag_mode="ru",
        limit=6,
    )

    assert "#ии" in result.hashtags
    assert "#ai" not in result.hashtags


def test_rubricator_supports_en_mode() -> None:
    service = RubricatorService()

    result = service.classify(
        title="AI and machine learning",
        text="",
        trend_keywords=["technology"],
        hashtag_mode="en",
        limit=6,
    )

    assert "#ai" in result.hashtags
    assert "#ии" not in result.hashtags
