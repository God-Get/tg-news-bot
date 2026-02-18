"""Article repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import Article
from tg_news_bot.utils.url import normalize_title_key


class ArticleRepository:
    async def get_by_id(self, session: AsyncSession, article_id: int) -> Article | None:
        result = await session.execute(select(Article).where(Article.id == article_id))
        return result.scalar_one_or_none()

    async def get_by_normalized_url(self, session: AsyncSession, url: str) -> Article | None:
        result = await session.execute(select(Article).where(Article.normalized_url == url))
        return result.scalar_one_or_none()

    async def exists_by_normalized_urls(
        self,
        session: AsyncSession,
        urls: list[str],
    ) -> bool:
        if not urls:
            return False
        result = await session.execute(
            select(Article.id).where(Article.normalized_url.in_(urls)).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def exists_recent_by_domain_title(
        self,
        session: AsyncSession,
        *,
        domain: str,
        normalized_title: str,
        created_from: datetime,
    ) -> bool:
        if not normalized_title:
            return False
        result = await session.execute(
            select(Article.title_en)
            .where(Article.domain == domain)
            .where(Article.created_at >= created_from)
            .where(Article.title_en.is_not(None))
        )
        for title in result.scalars().all():
            if normalize_title_key(title) == normalized_title:
                return True
        return False

    async def create(self, session: AsyncSession, article: Article) -> Article:
        session.add(article)
        await session.flush()
        return article

    async def clear_expired_extracted_text(
        self, session: AsyncSession, *, now: datetime
    ) -> int:
        result = await session.execute(
            update(Article)
            .where(Article.extracted_text.is_not(None))
            .where(Article.extracted_text_expires_at.is_not(None))
            .where(Article.extracted_text_expires_at <= now)
            .values(
                extracted_text=None,
                extracted_text_expires_at=None,
            )
        )
        return int(result.rowcount or 0)
