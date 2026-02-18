"""Trend signal repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import TrendSignal, TrendSignalSource


@dataclass(slots=True)
class TrendSignalInput:
    source: TrendSignalSource
    keyword: str
    weight: float
    observed_at: datetime
    meta: dict | None = None


class TrendSignalRepository:
    async def add_many(
        self,
        session: AsyncSession,
        *,
        items: list[TrendSignalInput],
    ) -> int:
        inserted = 0
        for item in items:
            signal = TrendSignal(
                source=item.source,
                keyword=item.keyword,
                weight=item.weight,
                observed_at=item.observed_at,
                meta=item.meta,
            )
            session.add(signal)
            inserted += 1
        if inserted:
            await session.flush()
        return inserted

    async def list_recent(
        self,
        session: AsyncSession,
        *,
        since: datetime,
        limit: int,
    ) -> list[TrendSignal]:
        result = await session.execute(
            select(TrendSignal)
            .where(TrendSignal.observed_at >= since)
            .order_by(TrendSignal.observed_at.desc(), TrendSignal.weight.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_recent_keyword_scores(
        self,
        session: AsyncSession,
        *,
        since: datetime,
        limit: int,
    ) -> list[tuple[str, float]]:
        result = await session.execute(
            select(
                TrendSignal.keyword,
                func.sum(TrendSignal.weight).label("weight_sum"),
            )
            .where(TrendSignal.observed_at >= since)
            .group_by(TrendSignal.keyword)
            .order_by(func.sum(TrendSignal.weight).desc())
            .limit(limit)
        )
        return [(str(row[0]), float(row[1] or 0.0)) for row in result.all()]

    async def delete_older_than(self, session: AsyncSession, *, before: datetime) -> int:
        result = await session.execute(
            delete(TrendSignal).where(TrendSignal.observed_at < before)
        )
        return int(result.rowcount or 0)
