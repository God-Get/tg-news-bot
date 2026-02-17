"""Publish failure repository (DLQ)."""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import PublishFailure, PublishFailureContext


class PublishFailureRepository:
    async def create(
        self,
        session: AsyncSession,
        *,
        draft_id: int,
        context: PublishFailureContext,
        error_message: str,
        attempt_no: int,
        scheduled_post_id: int | None = None,
        details: dict | None = None,
    ) -> PublishFailure:
        failure = PublishFailure(
            draft_id=draft_id,
            scheduled_post_id=scheduled_post_id,
            context=context,
            error_message=error_message,
            attempt_no=attempt_no,
            details=details,
            resolved=False,
        )
        session.add(failure)
        await session.flush()
        return failure

    async def mark_resolved_for_draft(self, session: AsyncSession, *, draft_id: int) -> None:
        rows = await session.execute(
            update(PublishFailure)
            .where(PublishFailure.draft_id == draft_id)
            .where(PublishFailure.resolved.is_(False))
            .values(resolved=True)
        )
        if rows.rowcount:
            await session.flush()
