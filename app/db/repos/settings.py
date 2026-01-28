from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.settings import BotSettings


class SettingsRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_singleton(self) -> BotSettings:
        q = await self.session.execute(select(BotSettings).limit(1))
        row = q.scalar_one_or_none()
        if row:
            return row
        s = BotSettings(fetch_limit=20)
        self.session.add(s)
        await self.session.commit()
        await self.session.refresh(s)
        return s

    async def update(self, **fields) -> BotSettings:
        s = await self.get_singleton()
        for k, v in fields.items():
            setattr(s, k, v)
        await self.session.commit()
        await self.session.refresh(s)
        return s
