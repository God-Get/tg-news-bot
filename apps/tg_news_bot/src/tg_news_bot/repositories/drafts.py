"""Draft repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import Draft
from tg_news_bot.utils.url import normalize_title_key


class DraftRepository:
    async def get(self, session: AsyncSession, draft_id: int) -> Draft | None:
        result = await session.execute(select(Draft).where(Draft.id == draft_id))
        return result.scalar_one_or_none()

    async def get_by_normalized_url(self, session: AsyncSession, url: str) -> Draft | None:
        result = await session.execute(select(Draft).where(Draft.normalized_url == url))
        return result.scalar_one_or_none()

    async def exists_by_normalized_urls(
        self,
        session: AsyncSession,
        urls: list[str],
    ) -> bool:
        if not urls:
            return False
        result = await session.execute(
            select(Draft.id).where(Draft.normalized_url.in_(urls)).limit(1)
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
            select(Draft.title_en)
            .where(Draft.domain == domain)
            .where(Draft.created_at >= created_from)
            .where(Draft.title_en.is_not(None))
        )
        for title in result.scalars().all():
            if normalize_title_key(title) == normalized_title:
                return True
        return False

    async def get_for_update(self, session: AsyncSession, draft_id: int) -> Draft:
        result = await session.execute(
            select(Draft).where(Draft.id == draft_id).with_for_update()
        )
        draft = result.scalar_one_or_none()
        if draft is None:
            raise LookupError(f"Draft {draft_id} not found")
        return draft

    async def clear_expired_extracted_text(
        self, session: AsyncSession, *, now: datetime
    ) -> int:
        result = await session.execute(
            update(Draft)
            .where(Draft.extracted_text.is_not(None))
            .where(Draft.extracted_text_expires_at.is_not(None))
            .where(Draft.extracted_text_expires_at <= now)
            .values(
                extracted_text=None,
                extracted_text_expires_at=None,
            )
        )
        return int(result.rowcount or 0)
