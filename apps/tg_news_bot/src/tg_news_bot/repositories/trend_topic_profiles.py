"""Trend topic profiles repository."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import TrendTopicProfile


@dataclass(slots=True)
class TrendTopicProfileInput:
    name: str
    seed_keywords: list[str]
    exclude_keywords: list[str] | None = None
    trusted_domains: list[str] | None = None
    min_article_score: float = 0.0
    enabled: bool = True
    tags: dict | None = None


class TrendTopicProfileRepository:
    async def list_all(self, session: AsyncSession) -> list[TrendTopicProfile]:
        result = await session.execute(
            select(TrendTopicProfile).order_by(TrendTopicProfile.name.asc())
        )
        return list(result.scalars().all())

    async def list_enabled(self, session: AsyncSession) -> list[TrendTopicProfile]:
        result = await session.execute(
            select(TrendTopicProfile)
            .where(TrendTopicProfile.enabled.is_(True))
            .order_by(TrendTopicProfile.name.asc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, session: AsyncSession, profile_id: int) -> TrendTopicProfile | None:
        result = await session.execute(
            select(TrendTopicProfile).where(TrendTopicProfile.id == profile_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, session: AsyncSession, name: str) -> TrendTopicProfile | None:
        result = await session.execute(
            select(TrendTopicProfile).where(TrendTopicProfile.name == name)
        )
        return result.scalar_one_or_none()

    async def create(self, session: AsyncSession, *, payload: TrendTopicProfileInput) -> TrendTopicProfile:
        profile = TrendTopicProfile(
            name=payload.name,
            enabled=payload.enabled,
            seed_keywords=payload.seed_keywords,
            exclude_keywords=payload.exclude_keywords,
            trusted_domains=payload.trusted_domains,
            min_article_score=payload.min_article_score,
            tags=payload.tags,
        )
        session.add(profile)
        await session.flush()
        return profile

    async def upsert_by_name(
        self,
        session: AsyncSession,
        *,
        payload: TrendTopicProfileInput,
    ) -> TrendTopicProfile:
        existing = await self.get_by_name(session, payload.name)
        if existing is None:
            return await self.create(session, payload=payload)
        existing.enabled = payload.enabled
        existing.seed_keywords = payload.seed_keywords
        existing.exclude_keywords = payload.exclude_keywords
        existing.trusted_domains = payload.trusted_domains
        existing.min_article_score = payload.min_article_score
        existing.tags = payload.tags
        await session.flush()
        return existing

    async def set_enabled(
        self,
        session: AsyncSession,
        *,
        profile_id: int,
        enabled: bool,
    ) -> TrendTopicProfile | None:
        existing = await self.get_by_id(session, profile_id)
        if existing is None:
            return None
        existing.enabled = enabled
        await session.flush()
        return existing
