# app/services/moderation/state_machine.py
from __future__ import annotations

from dataclasses import dataclass

from app.bot.utils.callback_data import DraftAction
from app.core.constants import DraftState


@dataclass(frozen=True)
class Transition:
    action: DraftAction
    from_states: tuple[DraftState, ...]
    to_state: DraftState | None
    label: str


TRANSITIONS: tuple[Transition, ...] = (
    # INBOX
    Transition(DraftAction.TO_EDITING, (DraftState.INBOX,), DraftState.EDITING, "✅ В редакцию"),
    Transition(DraftAction.TO_ARCHIVE, (DraftState.INBOX,), DraftState.ARCHIVE, "❌ В архив"),
    Transition(DraftAction.SOURCE, (DraftState.INBOX,), None, "🔗 Источник"),

    # EDITING
    Transition(DraftAction.EDIT_MODE, (DraftState.EDITING,), None, "✏️ Edit"),
    Transition(DraftAction.PREVIEW, (DraftState.EDITING,), None, "👁 Preview"),
    Transition(DraftAction.TO_READY, (DraftState.EDITING,), DraftState.READY, "✅ В публикацию"),
    Transition(DraftAction.TO_ARCHIVE, (DraftState.EDITING,), DraftState.ARCHIVE, "❌ В архив"),
    Transition(DraftAction.SOURCE, (DraftState.EDITING,), None, "🔗 Источник"),

    # READY
    Transition(DraftAction.PUBLISH_NOW, (DraftState.READY,), DraftState.PUBLISHED, "✅ Publish сейчас"),
    Transition(DraftAction.SCHEDULE_MENU, (DraftState.READY,), None, "🕒 Schedule"),

    # ✅ ВАЖНО: разрешаем shortcuts из READY -> SCHEDULED
    Transition(DraftAction.SCHEDULE_PLUS_1H, (DraftState.READY,), DraftState.SCHEDULED, "⏱ +1 час"),
    Transition(DraftAction.SCHEDULE_TOMORROW_10, (DraftState.READY,), DraftState.SCHEDULED, "🗓 Завтра 10:00"),
    Transition(DraftAction.SCHEDULE_MANUAL, (DraftState.READY,), DraftState.SCHEDULED, "✍️ Ввести вручную"),

    # "Edit" на READY (у тебя action называется BACK_TO_EDITING)
    Transition(DraftAction.BACK_TO_EDITING, (DraftState.READY,), DraftState.EDITING, "✏️ Edit"),
    Transition(DraftAction.TO_ARCHIVE, (DraftState.READY,), DraftState.ARCHIVE, "❌ В архив"),
    Transition(DraftAction.SOURCE, (DraftState.READY,), None, "🔗 Источник"),

    # SCHEDULED
    Transition(DraftAction.SCHEDULE_CHANGE_TIME, (DraftState.SCHEDULED,), None, "⏱ Изменить время"),
    Transition(DraftAction.SCHEDULE_CANCEL, (DraftState.SCHEDULED,), DraftState.READY, "⛔ Отменить"),
    Transition(DraftAction.PUBLISH_NOW, (DraftState.SCHEDULED,), DraftState.PUBLISHED, "✅ Опубликовать сейчас"),
    Transition(DraftAction.TO_ARCHIVE, (DraftState.SCHEDULED,), DraftState.ARCHIVE, "❌ В архив"),
    Transition(DraftAction.SOURCE, (DraftState.SCHEDULED,), None, "🔗 Источник"),

    # PUBLISHED
    Transition(DraftAction.REPOST, (DraftState.PUBLISHED,), None, "🔁 Repost"),
    Transition(DraftAction.BACK_TO_EDITING, (DraftState.PUBLISHED,), DraftState.EDITING, "✏️ В редакцию"),
    Transition(DraftAction.TO_ARCHIVE, (DraftState.PUBLISHED,), DraftState.ARCHIVE, "🗑 В архив"),
    Transition(DraftAction.SOURCE, (DraftState.PUBLISHED,), None, "🔗 Источник"),
)


def is_action_allowed(state: DraftState, action: DraftAction) -> bool:
    return any(t.action == action and state in t.from_states for t in TRANSITIONS)


def next_state(state: DraftState, action: DraftAction) -> DraftState | None:
    for t in TRANSITIONS:
        if t.action == action and state in t.from_states:
            return t.to_state
    raise ValueError(f"Action {action} is not allowed from state {state}")
