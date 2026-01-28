from __future__ import annotations

from enum import Enum


class DraftState(str, Enum):
    INBOX = "INBOX"
    EDITING = "EDITING"
    READY = "READY"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    ARCHIVE = "ARCHIVE"


class ImageStatus(str, Enum):
    NONE = "NONE"
    FOUND = "FOUND"
    DOWNLOADED = "DOWNLOADED"
    UPLOADED_TO_TG = "UPLOADED_TO_TG"
    FAILED = "FAILED"


class SourceType(str, Enum):
    RSS = "RSS"
    HTML = "HTML"


TOPIC_KEYS = [
    "inbox",
    "service",
    "ready",
    "scheduled",
    "published",
    "archive",
]
