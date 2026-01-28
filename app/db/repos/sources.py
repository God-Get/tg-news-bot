from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.source import Source


class SourcesRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, url: str, type_: str = "RSS", topics: str | None = None) -> Source:
        src = Source(url=url, type=type_, topics=topics, enabled=True)
        self.session.add(src)
        await self.session.commit()
        await self.session.refresh(src)
        return src

    async def list_all(self) -> list[Source]:
        q = await self.session.execute(select(Source).order_by(Source.id.asc()))
        return list(q.scalars().all())

    async def list_enabled(self) -> list[Source]:
        q = await self.session.execute(select(Source).where(Source.enabled.is_(True)).order_by(Source.id.asc()))
        return list(q.scalars().all())

    async def set_enabled(self, source_id: int, enabled: bool) -> None:
        await self.session.execute(update(Source).where(Source.id == source_id).values(enabled=enabled))
        await self.session.commit()
