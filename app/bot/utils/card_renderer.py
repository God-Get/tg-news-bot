from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.utils.callback_data import DraftActionCb, DraftAction
from app.db.models.settings import BotSettings
from app.db.models.draft import Draft


def render_card_text(d: Draft) -> str:
    created = d.created_at.astimezone(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []
    lines.append(f"🗂 Draft #{d.id}")
    lines.append(f"State: {d.state}")
    lines.append(f"Created: {created}")

    if getattr(d, "title_en", None):
        lines.append(f"EN: {d.title_en}")

    if getattr(d, "title_ru", None):
        lines.append(f"RU: {d.title_ru}")

    if getattr(d, "source_url", None):
        lines.append(f"URL: {d.source_url}")

    if getattr(d, "scheduled_at", None):
        sch = d.scheduled_at.astimezone(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d %H:%M")
        lines.append(f"⏰ Scheduled: {sch}")

    return "\n".join(lines)


def render_schedule_shortcuts(draft_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    kb.button(text="➕ +1 час", callback_data=DraftActionCb(action=DraftAction.SCHED_PLUS_1H, draft_id=draft_id).pack())
    kb.button(text="➕ +3 часа", callback_data=DraftActionCb(action=DraftAction.SCHED_PLUS_3H, draft_id=draft_id).pack())
    kb.button(text="➕ +24 часа", callback_data=DraftActionCb(action=DraftAction.SCHED_PLUS_24H, draft_id=draft_id).pack())
    kb.button(text="❌ Сбросить", callback_data=DraftActionCb(action=DraftAction.SCHED_CLEAR, draft_id=draft_id).pack())

    kb.adjust(2, 2)
    return kb.as_markup()


def render_card_keyboard(d: Draft, settings: BotSettings) -> InlineKeyboardMarkup:
    """
    Кнопки ДОЛЖНЫ висеть под САМИМ постом (post_message_id),
    но клавиатуру мы строим здесь единым образом.
    """
    kb = InlineKeyboardBuilder()
    did = int(d.id)

    # Базовые действия
    kb.button(text="📝 В редакцию", callback_data=DraftActionCb(action=DraftAction.TO_EDITING, draft_id=did).pack())
    kb.button(text="✅ В готово", callback_data=DraftActionCb(action=DraftAction.TO_READY, draft_id=did).pack())

    kb.button(text="⏰ В план", callback_data=DraftActionCb(action=DraftAction.TO_SCHEDULED, draft_id=did).pack())
    kb.button(text="📣 Опубликовать", callback_data=DraftActionCb(action=DraftAction.PUBLISH, draft_id=did).pack())

    kb.button(text="🗄 В архив", callback_data=DraftActionCb(action=DraftAction.TO_ARCHIVE, draft_id=did).pack())

    # Ветка планирования (шорткаты)
    kb.button(text="⏱ Быстро: +1/+3/+24", callback_data=DraftActionCb(action=DraftAction.OPEN_SCHEDULE, draft_id=did).pack())

    kb.adjust(2, 2, 1, 1)
    return kb.as_markup()


# Совместимость с твоими текущими импортами:
build_card_keyboard = render_card_keyboard
