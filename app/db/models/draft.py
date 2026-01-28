from __future__ import annotations

import enum
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DraftState(str, enum.Enum):
    INBOX = "INBOX"
    EDITING = "EDITING"
    READY = "READY"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    ARCHIVE = "ARCHIVE"


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    state: Mapped[DraftState] = mapped_column(Enum(DraftState, name="draft_state"), nullable=False, index=True)

    group_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    topic_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # ID "карточки" Draft #... (отдельное сообщение)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # ID "поста" (сообщение с текстом/фото, под которым клавиатура)
    post_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    title_en: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    title_ru: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    body_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    tg_image_file_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
