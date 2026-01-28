from __future__ import annotations

from enum import Enum
from aiogram.filters.callback_data import CallbackData


class DraftAction(str, Enum):
    SOURCE = "source"
    PREVIEW = "preview"
    EDIT_MODE = "edit_mode"

    TO_EDITING = "to_editing"
    TO_READY = "to_ready"
    TO_ARCHIVE = "to_archive"
    BACK_TO_EDITING = "back_to_editing"

    PUBLISH_NOW = "publish_now"

    SCHEDULE_MENU = "schedule_menu"
    SCHEDULE_PLUS_1H = "schedule_plus_1h"
    SCHEDULE_TOMORROW_10 = "schedule_tomorrow_10"
    SCHEDULE_MANUAL = "schedule_manual"
    SCHEDULE_CHANGE_TIME = "schedule_change_time"
    SCHEDULE_CANCEL = "schedule_cancel"

    REPOST = "repost"


class DraftCB(CallbackData, prefix="draft"):
    a: str
    id: int
    arg: str | None = None
