from __future__ import annotations

from datetime import date, datetime, timezone

from tg_news_bot.config import PostFormattingSettings
from tg_news_bot.db.models import Draft, DraftState
from tg_news_bot.services.keyboards import (
    build_schedule_keyboard,
    build_source_button_keyboard,
    build_state_keyboard,
)


def _draft() -> Draft:
    return Draft(
        id=1,
        state=DraftState.READY,
        normalized_url="https://example.com/item",
        domain="example.com",
        title_en="title",
        post_text_ru="text",
    )


def test_build_source_button_keyboard_for_button_mode() -> None:
    keyboard = build_source_button_keyboard(
        _draft(),
        formatting=PostFormattingSettings(source_mode="button", source_label="Source"),
    )

    assert keyboard is not None
    button = keyboard.inline_keyboard[0][0]
    assert button.text == "Source"
    assert button.url == "https://example.com/item"


def test_build_source_button_keyboard_returns_none_for_text_mode() -> None:
    keyboard = build_source_button_keyboard(
        _draft(),
        formatting=PostFormattingSettings(source_mode="text"),
    )

    assert keyboard is None


def test_build_source_button_keyboard_adds_discussion_button() -> None:
    keyboard = build_source_button_keyboard(
        _draft(),
        formatting=PostFormattingSettings(
            source_mode="button",
            source_label="Источник",
            discussion_label="Обсудить",
            discussion_url="https://t.me/my_discussion_group",
        ),
    )

    assert keyboard is not None
    row = keyboard.inline_keyboard[0]
    assert len(row) == 2
    assert row[0].text == "Источник"
    assert row[0].url == "https://example.com/item"
    assert row[1].text == "Обсудить"
    assert row[1].url == "https://t.me/my_discussion_group"


def test_build_source_button_keyboard_keeps_discussion_when_source_text_mode() -> None:
    keyboard = build_source_button_keyboard(
        _draft(),
        formatting=PostFormattingSettings(
            source_mode="text",
            discussion_label="Обсудить",
            discussion_url="https://t.me/my_discussion_group",
        ),
    )

    assert keyboard is not None
    row = keyboard.inline_keyboard[0]
    assert len(row) == 1
    assert row[0].text == "Обсудить"
    assert row[0].url == "https://t.me/my_discussion_group"


def test_build_schedule_times_menu_filters_past_slots_for_today() -> None:
    keyboard = build_schedule_keyboard(
        _draft(),
        menu="times",
        now=datetime(2026, 2, 17, 21, 10, tzinfo=timezone.utc),
        timezone_name="UTC",
        selected_day=date(2026, 2, 17),
    )

    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "22:00" in labels
    assert "20:00" not in labels
    assert "08:00" not in labels


def test_build_schedule_times_menu_shows_no_slots_hint_for_late_today() -> None:
    keyboard = build_schedule_keyboard(
        _draft(),
        menu="times",
        now=datetime(2026, 2, 17, 23, 58, tzinfo=timezone.utc),
        timezone_name="UTC",
        selected_day=date(2026, 2, 17),
    )

    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "На сегодня слотов нет" in labels


def test_build_state_keyboard_editing_has_process_button() -> None:
    draft = _draft()
    draft.state = DraftState.EDITING
    keyboard = build_state_keyboard(draft, DraftState.EDITING)

    first_button = keyboard.inline_keyboard[0][0]
    assert first_button.text == "Сделать выжимку"
    assert first_button.callback_data == "draft:1:process_now"
