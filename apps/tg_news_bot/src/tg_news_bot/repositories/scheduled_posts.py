"""Scheduled post repository."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import ScheduledPost, ScheduledPostStatus


class ScheduledPostRepository:
    async def get_by_draft(self, session: AsyncSession, draft_id: int) -> ScheduledPost | None:
        result = await session.execute(
            select(ScheduledPost).where(ScheduledPost.draft_id == draft_id)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        session: AsyncSession,
        *,
        draft_id: int,
        schedule_at: datetime,
    ) -> ScheduledPost:
        scheduled = await self.get_by_draft(session, draft_id)
        if scheduled is None:
            scheduled = ScheduledPost(draft_id=draft_id, schedule_at=schedule_at)
            session.add(scheduled)
            await session.flush()
            return scheduled
        scheduled.schedule_at = schedule_at
        scheduled.status = ScheduledPostStatus.SCHEDULED
        scheduled.attempts = 0
        scheduled.last_error = None
        scheduled.next_retry_at = None
        await session.flush()
        return scheduled

    async def list_due_for_update(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        limit: int = 20,
    ) -> list[ScheduledPost]:
        result = await session.execute(
            select(ScheduledPost)
            .where(
                or_(
                    (
                        (ScheduledPost.status == ScheduledPostStatus.SCHEDULED)
                        & (ScheduledPost.schedule_at <= now)
                    ),
                    (
                        (ScheduledPost.status == ScheduledPostStatus.FAILED)
                        & (ScheduledPost.next_retry_at.is_not(None))
                        & (ScheduledPost.next_retry_at <= now)
                    ),
                )
            )
            .order_by(
                func.coalesce(ScheduledPost.next_retry_at, ScheduledPost.schedule_at).asc()
            )
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_failed_without_retry_for_update(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        limit: int = 20,
    ) -> list[ScheduledPost]:
        result = await session.execute(
            select(ScheduledPost)
            .where(
                ScheduledPost.status == ScheduledPostStatus.FAILED,
                ScheduledPost.next_retry_at.is_(None),
                ScheduledPost.updated_at <= now,
            )
            .order_by(ScheduledPost.updated_at.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_pending(self, session: AsyncSession) -> int:
        result = await session.execute(
            select(func.count())
            .select_from(ScheduledPost)
            .where(
                or_(
                    ScheduledPost.status == ScheduledPostStatus.SCHEDULED,
                    (
                        (ScheduledPost.status == ScheduledPostStatus.FAILED)
                        & (ScheduledPost.next_retry_at.is_not(None))
                    ),
                )
            )
        )
        return int(result.scalar_one())

    async def list_failed(
        self,
        session: AsyncSession,
        *,
        limit: int = 20,
    ) -> list[ScheduledPost]:
        result = await session.execute(
            select(ScheduledPost)
            .where(ScheduledPost.status == ScheduledPostStatus.FAILED)
            .order_by(ScheduledPost.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_upcoming(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        until: datetime | None = None,
        limit: int = 50,
    ) -> list[ScheduledPost]:
        query = (
            select(ScheduledPost)
            .where(
                ScheduledPost.status == ScheduledPostStatus.SCHEDULED,
                ScheduledPost.schedule_at >= now,
            )
            .order_by(ScheduledPost.schedule_at.asc())
            .limit(limit)
        )
        if until is not None:
            query = query.where(ScheduledPost.schedule_at <= until)
        result = await session.execute(query)
        return list(result.scalars().all())

    async def retry_now_by_draft(self, session: AsyncSession, *, draft_id: int) -> bool:
        scheduled = await self.get_by_draft(session, draft_id)
        if scheduled is None:
            return False
        scheduled.status = ScheduledPostStatus.FAILED
        scheduled.next_retry_at = datetime.now(timezone.utc)
        await session.flush()
        return True

    async def cancel_by_draft(self, session: AsyncSession, *, draft_id: int) -> bool:
        scheduled = await self.get_by_draft(session, draft_id)
        if scheduled is None:
            return False
        scheduled.status = ScheduledPostStatus.CANCELLED
        scheduled.next_retry_at = None
        await session.flush()
        return True
