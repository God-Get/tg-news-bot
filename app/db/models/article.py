from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    title_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
