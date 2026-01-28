from __future__ import annotations

from sqlalchemy import BigInteger, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BotSettings(Base):
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    group_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    inbox_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    service_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ready_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    scheduled_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    published_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    archive_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    channel_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    fetch_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
