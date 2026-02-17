"""Schedule input session repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import ScheduleInputSession, ScheduleInputStatus


class ScheduleInputSessionRepository:
    async def get_active_by_draft(
        self,
        session: AsyncSession,
        *,
        draft_id: int,
    ) -> ScheduleInputSession | None:
        result = await session.execute(
            select(ScheduleInputSession)
            .where(ScheduleInputSession.draft_id == draft_id)
            .where(ScheduleInputSession.status == ScheduleInputStatus.ACTIVE)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_for_topic_user(
        self,
        session: AsyncSession,
        *,
        group_chat_id: int,
        topic_id: int,
        user_id: int,
        now: datetime,
    ) -> ScheduleInputSession | None:
        await self.expire_overdue(session, now=now)
        result = await session.execute(
            select(ScheduleInputSession)
            .where(ScheduleInputSession.group_chat_id == group_chat_id)
            .where(ScheduleInputSession.topic_id == topic_id)
            .where(ScheduleInputSession.user_id == user_id)
            .where(ScheduleInputSession.status == ScheduleInputStatus.ACTIVE)
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
        expires_at: datetime,
    ) -> ScheduleInputSession:
        active = await self.get_active_by_draft(session, draft_id=draft_id)
        if not active:
            active = ScheduleInputSession(
                draft_id=draft_id,
                group_chat_id=group_chat_id,
                topic_id=topic_id,
                user_id=user_id,
                status=ScheduleInputStatus.ACTIVE,
                expires_at=expires_at,
            )
            session.add(active)
            await session.flush()
            return active

        active.group_chat_id = group_chat_id
        active.topic_id = topic_id
        active.user_id = user_id
        active.expires_at = expires_at
        await session.flush()
        return active

    async def complete(self, session: AsyncSession, *, session_id: int) -> None:
        await self._set_status(
            session,
            session_id=session_id,
            status=ScheduleInputStatus.COMPLETED,
        )

    async def cancel_by_draft(self, session: AsyncSession, *, draft_id: int) -> None:
        await session.execute(
            update(ScheduleInputSession)
            .where(ScheduleInputSession.draft_id == draft_id)
            .where(ScheduleInputSession.status == ScheduleInputStatus.ACTIVE)
            .values(status=ScheduleInputStatus.CANCELLED)
        )

    async def expire_overdue(self, session: AsyncSession, *, now: datetime) -> None:
        await session.execute(
            update(ScheduleInputSession)
            .where(ScheduleInputSession.status == ScheduleInputStatus.ACTIVE)
            .where(ScheduleInputSession.expires_at <= now)
            .values(status=ScheduleInputStatus.EXPIRED)
        )

    async def _set_status(
        self,
        session: AsyncSession,
        *,
        session_id: int,
        status: ScheduleInputStatus,
    ) -> None:
        await session.execute(
            update(ScheduleInputSession)
            .where(ScheduleInputSession.id == session_id)
            .where(ScheduleInputSession.status == ScheduleInputStatus.ACTIVE)
            .values(status=status)
        )
