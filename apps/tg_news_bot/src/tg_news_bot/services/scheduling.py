"""Scheduling service."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import ScheduledPostStatus
from tg_news_bot.repositories.scheduled_posts import ScheduledPostRepository


class ScheduleService:
    def __init__(self, scheduled_repo: ScheduledPostRepository | None = None) -> None:
        self._scheduled_repo = scheduled_repo or ScheduledPostRepository()

    async def schedule(
        self,
        session: AsyncSession,
        *,
        draft_id: int,
        schedule_at: datetime,
    ) -> None:
        await self._scheduled_repo.upsert(
            session, draft_id=draft_id, schedule_at=schedule_at
        )

    async def cancel(self, session: AsyncSession, *, draft_id: int) -> None:
        scheduled = await self._scheduled_repo.get_by_draft(session, draft_id)
        if scheduled is None:
            return
        scheduled.status = ScheduledPostStatus.CANCELLED
        await session.flush()

    async def mark_published(self, session: AsyncSession, *, draft_id: int) -> None:
        scheduled = await self._scheduled_repo.get_by_draft(session, draft_id)
        if scheduled is None:
            return
        if scheduled.status == ScheduledPostStatus.SCHEDULED:
            scheduled.status = ScheduledPostStatus.PUBLISHED
            await session.flush()
