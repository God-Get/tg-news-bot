"""Workflow types."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime


class DraftAction(enum.StrEnum):
    TO_EDITING = "to_editing"
    TO_READY = "to_ready"
    TO_ARCHIVE = "to_archive"
    PUBLISH_NOW = "publish_now"
    SCHEDULE = "schedule"
    CANCEL_SCHEDULE = "cancel_schedule"
    REPOST = "repost"


@dataclass(slots=True)
class TransitionRequest:
    draft_id: int
    action: DraftAction
    user_id: int
    schedule_at: datetime | None = None
