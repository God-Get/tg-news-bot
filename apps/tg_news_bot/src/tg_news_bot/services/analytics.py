"""Operational analytics snapshot service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import statistics

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_news_bot.db.models import Draft, DraftState, PublishFailure, ScheduledPost, ScheduledPostStatus


@dataclass(slots=True)
class AnalyticsSnapshot:
    window_hours: int
    drafts_created: int
    drafts_published: int
    ingestion_rate_per_hour: float
    current_states: dict[str, int]
    conversion_to_published: float
    median_minutes_to_publish: float | None
    failures_recent: int
    failures_unresolved: int
    scheduled_failed_now: int


class AnalyticsService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def snapshot(self, *, window_hours: int) -> AnalyticsSnapshot:
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=window_hours)

        async with self._session_factory() as session:
            async with session.begin():
                drafts_created = await _count_where(
                    session,
                    select(func.count()).select_from(Draft).where(Draft.created_at >= since),
                )
                drafts_published = await _count_where(
                    session,
                    select(func.count())
                    .select_from(Draft)
                    .where(Draft.published_at.is_not(None))
                    .where(Draft.published_at >= since),
                )
                failures_recent = await _count_where(
                    session,
                    select(func.count())
                    .select_from(PublishFailure)
                    .where(PublishFailure.created_at >= since),
                )
                failures_unresolved = await _count_where(
                    session,
                    select(func.count())
                    .select_from(PublishFailure)
                    .where(PublishFailure.resolved.is_(False)),
                )
                scheduled_failed_now = await _count_where(
                    session,
                    select(func.count())
                    .select_from(ScheduledPost)
                    .where(ScheduledPost.status == ScheduledPostStatus.FAILED),
                )

                state_rows = await session.execute(
                    select(Draft.state, func.count())
                    .group_by(Draft.state)
                    .order_by(Draft.state.asc())
                )
                current_states = {
                    (state.value if isinstance(state, DraftState) else str(state)): int(count)
                    for state, count in state_rows.all()
                }

                published_rows = await session.execute(
                    select(Draft.created_at, Draft.published_at)
                    .where(Draft.published_at.is_not(None))
                    .where(Draft.published_at >= since)
                )

        durations: list[float] = []
        for created_at, published_at in published_rows.all():
            if created_at and published_at and published_at >= created_at:
                durations.append((published_at - created_at).total_seconds() / 60.0)

        median_minutes: float | None = None
        if durations:
            median_minutes = float(statistics.median(durations))

        ingestion_rate = drafts_created / float(window_hours or 1)
        conversion = 0.0
        if drafts_created > 0:
            conversion = drafts_published / float(drafts_created)

        return AnalyticsSnapshot(
            window_hours=window_hours,
            drafts_created=drafts_created,
            drafts_published=drafts_published,
            ingestion_rate_per_hour=ingestion_rate,
            current_states=current_states,
            conversion_to_published=conversion,
            median_minutes_to_publish=median_minutes,
            failures_recent=failures_recent,
            failures_unresolved=failures_unresolved,
            scheduled_failed_now=scheduled_failed_now,
        )

    @staticmethod
    def render(snapshot: AnalyticsSnapshot) -> str:
        lines = [
            f"Аналитика за {snapshot.window_hours}ч",
            f"Ingestion rate: {snapshot.ingestion_rate_per_hour:.2f} draft/ч",
            f"Создано draft: {snapshot.drafts_created}",
            f"Опубликовано: {snapshot.drafts_published}",
            f"Conversion created->published: {snapshot.conversion_to_published * 100:.1f}%",
        ]
        if snapshot.median_minutes_to_publish is not None:
            lines.append(f"Median time to publish: {snapshot.median_minutes_to_publish:.1f} мин")
        else:
            lines.append("Median time to publish: n/a")

        if snapshot.current_states:
            state_view = ", ".join(
                f"{state}:{count}" for state, count in sorted(snapshot.current_states.items())
            )
            lines.append(f"Состояния: {state_view}")

        lines.append(f"Ошибки publish (окно): {snapshot.failures_recent}")
        lines.append(f"Ошибки publish unresolved: {snapshot.failures_unresolved}")
        lines.append(f"Scheduled FAILED сейчас: {snapshot.scheduled_failed_now}")
        return "\n".join(lines)


async def _count_where(session: AsyncSession, query) -> int:
    result = await session.execute(query)
    return int(result.scalar_one() or 0)
