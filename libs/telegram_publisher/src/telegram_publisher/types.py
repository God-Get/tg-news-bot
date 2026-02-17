"""Types for Telegram publisher."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PostContent:
    text: str
    photo: str | None = None
    parse_mode: str | None = None


@dataclass(slots=True)
class SendResult:
    chat_id: int
    message_id: int
    photo_file_id: str | None = None
    photo_unique_id: str | None = None
