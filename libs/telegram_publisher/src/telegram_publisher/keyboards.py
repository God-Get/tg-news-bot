"""Inline keyboard helpers."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


@dataclass(slots=True)
class ButtonSpec:
    text: str
    callback_data: str | None = None
    url: str | None = None

    def to_button(self) -> InlineKeyboardButton:
        if self.url and self.callback_data:
            raise ValueError("ButtonSpec supports either url or callback_data, not both")
        if self.url:
            return InlineKeyboardButton(text=self.text, url=self.url)
        if self.callback_data:
            return InlineKeyboardButton(text=self.text, callback_data=self.callback_data)
        raise ValueError("ButtonSpec requires url or callback_data")


def keyboard_from_rows(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    inline_rows: list[list[InlineKeyboardButton]] = []
    for row in rows:
        inline_rows.append(
            [InlineKeyboardButton(text=text, callback_data=callback) for text, callback in row]
        )
    return InlineKeyboardMarkup(inline_keyboard=inline_rows)


def keyboard_from_specs(rows: list[list[ButtonSpec]]) -> InlineKeyboardMarkup:
    inline_rows: list[list[InlineKeyboardButton]] = []
    for row in rows:
        inline_rows.append([button.to_button() for button in row])
    return InlineKeyboardMarkup(inline_keyboard=inline_rows)
