from __future__ import annotations

from datetime import datetime, timezone

from tg_news_bot.config import PostFormattingSettings
from tg_news_bot.db.models import Draft, DraftState
from tg_news_bot.services.rendering import CAPTION_MAX_LEN, render_card_text, render_post_content


def _make_draft(*, state: DraftState, **kwargs) -> Draft:
    data = {
        "id": 10,
        "state": state,
        "normalized_url": "https://example.com/item",
        "domain": "example.com",
        "title_en": "title",
        "post_text_ru": "text",
    }
    data.update(kwargs)
    return Draft(**data)


def test_render_card_text_includes_schedule_for_scheduled_state() -> None:
    draft = _make_draft(state=DraftState.SCHEDULED)
    schedule_at = datetime(2026, 2, 17, 10, 0, tzinfo=timezone.utc)

    text = render_card_text(draft, schedule_at=schedule_at)

    assert "Schedule at: 2026-02-17 10:00 UTC" in text


def test_render_card_text_omits_schedule_for_non_scheduled_state() -> None:
    draft = _make_draft(state=DraftState.READY)

    text = render_card_text(draft, schedule_at=datetime.now(timezone.utc))

    assert "Schedule at:" not in text


def test_render_card_text_uses_state_override_for_transition() -> None:
    draft = _make_draft(state=DraftState.READY)
    schedule_at = datetime(2026, 2, 17, 10, 0, tzinfo=timezone.utc)

    text = render_card_text(
        draft,
        schedule_at=schedule_at,
        state=DraftState.SCHEDULED,
    )

    assert "State: SCHEDULED" in text
    assert "Schedule at: 2026-02-17 10:00 UTC" in text


def test_render_post_content_formats_title_text_hashtags_and_source() -> None:
    draft = _make_draft(
        state=DraftState.INBOX,
        post_text_ru="Заголовок\n\nТекст поста",
        score_reasons={"kw:AI": 1.0, "kw:Space Tech": 0.5},
    )

    content = render_post_content(draft)

    assert content.parse_mode == "HTML"
    assert content.photo is None
    assert "<b>Заголовок</b>" in content.text
    assert "Текст поста" in content.text
    assert "#ai" in content.text
    assert "#space_tech" in content.text
    assert "#example_com" in content.text
    assert '<a href="https://example.com/item">Источник</a>' not in content.text


def test_render_post_content_uses_auto_hashtags_from_reasons() -> None:
    draft = _make_draft(
        state=DraftState.INBOX,
        post_text_ru="Заголовок\n\nТекст поста",
        score_reasons={"auto_hashtags": ["#ai", "space_flight"]},
    )

    content = render_post_content(draft)

    assert "#ai" in content.text
    assert "#space_flight" in content.text


def test_render_post_content_uses_defaults_when_data_missing() -> None:
    draft = _make_draft(
        state=DraftState.INBOX,
        domain=None,
        title_en=None,
        post_text_ru=None,
        score_reasons=None,
    )

    content = render_post_content(draft)

    assert content.parse_mode == "HTML"
    assert "Без заголовка" in content.text
    assert "Текст будет добавлен после обработки." in content.text
    assert "#news" in content.text


def test_render_post_content_limits_caption_length_for_photo() -> None:
    draft = _make_draft(
        state=DraftState.INBOX,
        source_image_url="https://example.com/image.jpg",
        post_text_ru="Заголовок\n\n" + ("x" * 4000),
        score_reasons={"kw:ai": 1.0},
    )

    content = render_post_content(draft)

    assert content.photo == "https://example.com/image.jpg"
    assert len(content.text) <= CAPTION_MAX_LEN
    assert "…" in content.text


def test_render_post_content_truncation_keeps_valid_html_tags() -> None:
    draft = _make_draft(
        state=DraftState.INBOX,
        source_image_url="https://example.com/image.jpg",
        post_text_ru="Очень длинный заголовок\n\n" + ("текст " * 2000),
        score_reasons={"kw:ai": 1.0},
    )
    formatting = PostFormattingSettings(source_mode="text")

    content = render_post_content(draft, formatting=formatting)

    assert content.photo == "https://example.com/image.jpg"
    assert content.parse_mode == "HTML"
    assert len(content.text) <= CAPTION_MAX_LEN
    assert content.text.count("<b>") == content.text.count("</b>")
    assert content.text.count("<a href=") == content.text.count("</a>")
    assert '<a href="https://example.com/item">Источник</a>' in content.text


def test_render_post_content_respects_configured_order_and_hashtag_limit() -> None:
    draft = _make_draft(
        state=DraftState.INBOX,
        post_text_ru="Заголовок\n\nТекст поста",
        score_reasons={"kw:AI": 1.0, "kw:Space": 0.8},
    )
    formatting = PostFormattingSettings(
        sections_order="hashtags,title,source",
        hashtags_limit=1,
        source_label="Source",
        source_mode="text",
    )

    content = render_post_content(draft, formatting=formatting)
    lines = content.text.split("\n\n")

    assert content.parse_mode == "HTML"
    assert lines[0] == "#ai"
    assert lines[1] == "<b>Заголовок</b>"
    assert lines[2] == '<a href="https://example.com/item">Source</a>'


def test_render_post_content_keeps_source_when_mode_both() -> None:
    draft = _make_draft(
        state=DraftState.INBOX,
        post_text_ru="Заголовок\n\nТекст поста",
        score_reasons={"kw:AI": 1.0},
    )
    formatting = PostFormattingSettings(source_mode="both")

    content = render_post_content(draft, formatting=formatting)

    assert content.parse_mode == "HTML"
    assert '<a href="https://example.com/item">Источник</a>' in content.text


def test_render_post_content_normalizes_escaped_newlines_in_text() -> None:
    draft = _make_draft(
        state=DraftState.INBOX,
        post_text_ru=r"Заголовок\n\nТекст поста",
        score_reasons={"kw:AI": 1.0},
    )
    formatting = PostFormattingSettings(source_mode="text")

    content = render_post_content(draft, formatting=formatting)

    assert content.parse_mode == "HTML"
    assert "<b>Заголовок</b>" in content.text
    assert "Текст поста" in content.text
    assert r"\n\n" not in content.text


def test_render_post_content_normalizes_double_escaped_newlines_in_text() -> None:
    draft = _make_draft(
        state=DraftState.INBOX,
        post_text_ru="Заголовок\\\\n\\\\nТекст поста",
        score_reasons={"kw:AI": 1.0},
    )
    formatting = PostFormattingSettings(source_mode="text")

    content = render_post_content(draft, formatting=formatting)

    assert "<b>Заголовок</b>" in content.text
    assert "Текст поста" in content.text
    assert r"\\n\\n" not in content.text


def test_render_post_content_removes_trailing_source_line() -> None:
    draft = _make_draft(
        state=DraftState.INBOX,
        post_text_ru="Заголовок\n\nТекст поста\n\nИсточник: https://example.com/item",
        score_reasons={"kw:AI": 1.0},
    )
    formatting = PostFormattingSettings(source_mode="text")

    content = render_post_content(draft, formatting=formatting)

    assert content.text.count("Источник") == 1
    assert "Источник: https://example.com/item" not in content.text


def test_render_post_content_splits_title_from_first_line_when_no_blank_line() -> None:
    draft = _make_draft(
        state=DraftState.INBOX,
        post_text_ru="Короткий заголовок\nСтрока 1\nСтрока 2",
        score_reasons={"kw:AI": 1.0},
    )
    formatting = PostFormattingSettings(source_mode="text")

    content = render_post_content(draft, formatting=formatting)

    assert "<b>Короткий заголовок</b>" in content.text
    assert "Строка 1\nСтрока 2" in content.text


def test_post_formatting_section_separator_unescapes_newline() -> None:
    formatting = PostFormattingSettings(
        source_mode="text",
        section_separator=r"\n\n",
    )

    assert formatting.section_separator == "\n\n"
