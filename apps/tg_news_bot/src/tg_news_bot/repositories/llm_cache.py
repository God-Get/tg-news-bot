"""LLM cache repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import LLMCache


class LLMCacheRepository:
    async def get_by_normalized_url(
        self,
        session: AsyncSession,
        normalized_url: str,
    ) -> LLMCache | None:
        result = await session.execute(
            select(LLMCache).where(LLMCache.normalized_url == normalized_url)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        session: AsyncSession,
        *,
        article_id: int | None,
        normalized_url: str,
        provider: str,
        model: str,
        topic_hints: list[str],
        title_ru: str | None,
        summary_ru: str,
    ) -> LLMCache:
        cached = await self.get_by_normalized_url(session, normalized_url)
        payload = {
            "topics": topic_hints,
        }
        if cached is None:
            cached = LLMCache(
                article_id=article_id,
                normalized_url=normalized_url,
                provider=provider,
                model=model,
                topic_hints=payload,
                title_ru=title_ru,
                summary_ru=summary_ru,
            )
            session.add(cached)
            await session.flush()
            return cached

        cached.article_id = article_id
        cached.provider = provider
        cached.model = model
        cached.topic_hints = payload
        cached.title_ru = title_ru
        cached.summary_ru = summary_ru
        await session.flush()
        return cached
