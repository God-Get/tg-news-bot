"""Source repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import Article, Draft, Source


class SourceRepository:
    async def list_all(self, session: AsyncSession) -> list[Source]:
        result = await session.execute(select(Source).order_by(Source.id.asc()))
        return list(result.scalars().all())

    async def list_enabled(self, session: AsyncSession) -> list[Source]:
        result = await session.execute(select(Source).where(Source.enabled.is_(True)))
        return list(result.scalars().all())

    async def get_by_id(self, session: AsyncSession, source_id: int) -> Source | None:
        result = await session.execute(select(Source).where(Source.id == source_id))
        return result.scalar_one_or_none()

    async def get_by_url(self, session: AsyncSession, url: str) -> Source | None:
        result = await session.execute(select(Source).where(Source.url == url))
        return result.scalar_one_or_none()

    async def create(
        self,
        session: AsyncSession,
        *,
        name: str,
        url: str,
        enabled: bool = True,
        tags: dict | None = None,
    ) -> Source:
        source = Source(
            name=name,
            url=url,
            enabled=enabled,
            tags=tags,
        )
        session.add(source)
        await session.flush()
        return source

    async def has_linked_data(self, session: AsyncSession, *, source_id: int) -> bool:
        draft_row = await session.execute(
            select(Draft.id).where(Draft.source_id == source_id).limit(1)
        )
        if draft_row.scalar_one_or_none() is not None:
            return True
        article_row = await session.execute(
            select(Article.id).where(Article.source_id == source_id).limit(1)
        )
        return article_row.scalar_one_or_none() is not None

    async def delete(self, session: AsyncSession, source: Source) -> None:
        await session.delete(source)
        await session.flush()
