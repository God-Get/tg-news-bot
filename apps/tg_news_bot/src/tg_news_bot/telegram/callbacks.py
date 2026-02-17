"""Callback helpers."""

from __future__ import annotations

from dataclasses import dataclass
import enum


CALLBACK_PREFIX = "draft"


@dataclass(slots=True)
class DraftCallback:
    draft_id: int
    action: str


def build_callback(draft_id: int, action: str | enum.Enum) -> str:
    action_value = action.value if isinstance(action, enum.Enum) else action
    return f"{CALLBACK_PREFIX}:{draft_id}:{action_value}"


def parse_callback(data: str) -> DraftCallback | None:
    if not data:
        return None
    parts = data.split(":")
    if len(parts) != 3:
        return None
    prefix, draft_id, action = parts
    if prefix != CALLBACK_PREFIX:
        return None
    if not draft_id.isdigit():
        return None
    return DraftCallback(draft_id=int(draft_id), action=action)
