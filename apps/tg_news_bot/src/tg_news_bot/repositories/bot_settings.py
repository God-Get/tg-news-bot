"""Bot settings repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import BotSettings


class BotSettingsRepository:
    async def get(self, session: AsyncSession) -> BotSettings | None:
        result = await session.execute(select(BotSettings).limit(1))
        return result.scalar_one_or_none()

    async def get_or_create(self, session: AsyncSession) -> BotSettings:
        settings = await self.get(session)
        if settings:
            return settings
        settings = BotSettings()
        session.add(settings)
        await session.flush()
        return settings
