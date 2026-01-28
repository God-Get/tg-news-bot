from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.bot.utils.callback_data import DraftCB, DraftAction
from app.core.constants import DraftState


def render_card_text(d) -> str:
    url = getattr(d, "source_url", None) or getattr(d, "url", None) or "-"
    state = getattr(d, "state", None) or "?"
    did = getattr(d, "id", None)

    scheduled = getattr(d, "scheduled_at", None)
    scheduled_s = ""
    if scheduled:
        scheduled_s = f"\n🕒 Scheduled: {scheduled}"

    return (
        f"🗂 Draft #{did}\n"
        f"State: {state}{scheduled_s}\n"
        f"🔗 {url}"
    )


def build_card_keyboard(d) -> InlineKeyboardMarkup:
    did = int(getattr(d, "id"))
    state = getattr(d, "state")

    rows: list[list[InlineKeyboardButton]] = []

    # общие кнопки
    rows.append([
        InlineKeyboardButton(text="Источник", callback_data=DraftCB(a=DraftAction.SOURCE, id=did).pack()),
        InlineKeyboardButton(text="Планировать", callback_data=DraftCB(a=DraftAction.SCHEDULE_MENU, id=did).pack()),
    ])

    # переходы
    if state == DraftState.INBOX:
        rows.append([
            InlineKeyboardButton(text="В редакцию", callback_data=DraftCB(a=DraftAction.TO_EDITING, id=did).pack()),
            InlineKeyboardButton(text="В готово", callback_data=DraftCB(a=DraftAction.TO_READY, id=did).pack()),
        ])
        rows.append([
            InlineKeyboardButton(text="Архив", callback_data=DraftCB(a=DraftAction.TO_ARCHIVE, id=did).pack()),
        ])

    elif state == DraftState.EDITING:
        rows.append([
            InlineKeyboardButton(text="В готово", callback_data=DraftCB(a=DraftAction.TO_READY, id=did).pack()),
            InlineKeyboardButton(text="Архив", callback_data=DraftCB(a=DraftAction.TO_ARCHIVE, id=did).pack()),
        ])

    elif state == DraftState.READY:
        rows.append([
            InlineKeyboardButton(text="В редакцию", callback_data=DraftCB(a=DraftAction.TO_EDITING, id=did).pack()),
            InlineKeyboardButton(text="Опубликовать", callback_data=DraftCB(a=DraftAction.PUBLISH_NOW, id=did).pack()),
        ])
        rows.append([
            InlineKeyboardButton(text="Архив", callback_data=DraftCB(a=DraftAction.TO_ARCHIVE, id=did).pack()),
        ])

    elif state == DraftState.SCHEDULED:
        rows.append([
            InlineKeyboardButton(text="Отменить план", callback_data=DraftCB(a=DraftAction.SCHEDULE_CANCEL, id=did).pack()),
            InlineKeyboardButton(text="Опубликовать сейчас", callback_data=DraftCB(a=DraftAction.PUBLISH_NOW, id=did).pack()),
        ])

    elif state == DraftState.PUBLISHED:
        rows.append([
            InlineKeyboardButton(text="Репост", callback_data=DraftCB(a=DraftAction.REPOST, id=did).pack()),
            InlineKeyboardButton(text="Архив", callback_data=DraftCB(a=DraftAction.TO_ARCHIVE, id=did).pack()),
        ])

    elif state == DraftState.ARCHIVE:
        rows.append([
            InlineKeyboardButton(text="В редакцию", callback_data=DraftCB(a=DraftAction.TO_EDITING, id=did).pack()),
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_schedule_shortcuts(draft_id: int) -> InlineKeyboardMarkup:
    did = int(draft_id)
    rows = [
        [
            InlineKeyboardButton(text="+1 час", callback_data=DraftCB(a=DraftAction.SCHEDULE_PLUS_1H, id=did).pack()),
            InlineKeyboardButton(text="Завтра 10:00", callback_data=DraftCB(a=DraftAction.SCHEDULE_TOMORROW_10, id=did).pack()),
        ],
        [
            InlineKeyboardButton(text="Назад", callback_data=DraftCB(a=DraftAction.TO_READY, id=did).pack()),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
