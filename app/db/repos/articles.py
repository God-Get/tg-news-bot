from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.article import Article


class ArticlesRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def exists_by_normalized_url(self, normalized_url: str) -> bool:
        q = await self.session.execute(select(Article.id).where(Article.normalized_url == normalized_url).limit(1))
        return q.scalar_one_or_none() is not None

    async def insert(self, **fields) -> Article:
        a = Article(**fields)
        self.session.add(a)
        await self.session.commit()
        await self.session.refresh(a)
        return a
