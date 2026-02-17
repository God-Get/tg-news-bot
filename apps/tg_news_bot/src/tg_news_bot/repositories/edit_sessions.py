"""Edit session repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import EditSession, EditSessionStatus


class EditSessionRepository:
    async def get_active_by_draft(
        self, session: AsyncSession, draft_id: int
    ) -> EditSession | None:
        result = await session.execute(
            select(EditSession).where(
                EditSession.draft_id == draft_id,
                EditSession.status == EditSessionStatus.ACTIVE,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_for_topic(
        self, session: AsyncSession, *, group_chat_id: int, topic_id: int
    ) -> EditSession | None:
        result = await session.execute(
            select(EditSession)
            .where(
                EditSession.group_chat_id == group_chat_id,
                EditSession.topic_id == topic_id,
                EditSession.status == EditSessionStatus.ACTIVE,
            )
            .order_by(EditSession.started_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def upsert_active(
        self,
        session: AsyncSession,
        *,
        draft_id: int,
        group_chat_id: int,
        topic_id: int,
        user_id: int,
        started_at: datetime,
        expires_at: datetime,
    ) -> EditSession:
        existing = await self.get_active_by_draft(session, draft_id)
        if existing:
            existing.group_chat_id = group_chat_id
            existing.topic_id = topic_id
            existing.user_id = user_id
            existing.started_at = started_at
            existing.expires_at = expires_at
            existing.status = EditSessionStatus.ACTIVE
            await session.flush()
            return existing

        session_obj = EditSession(
            draft_id=draft_id,
            group_chat_id=group_chat_id,
            topic_id=topic_id,
            user_id=user_id,
            started_at=started_at,
            expires_at=expires_at,
            status=EditSessionStatus.ACTIVE,
        )
        session.add(session_obj)
        await session.flush()
        return session_obj
