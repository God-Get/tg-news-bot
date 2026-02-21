"""Trend discovery candidates repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import (
    TrendArticleCandidate,
    TrendCandidateStatus,
    TrendSourceCandidate,
    TrendTopic,
)


@dataclass(slots=True)
class TrendTopicInput:
    profile_id: int | None
    topic_name: str
    topic_slug: str
    trend_score: float
    confidence: float
    reasons: dict | None
    discovered_at: datetime


@dataclass(slots=True)
class TrendArticleCandidateInput:
    topic_id: int
    title: str | None
    url: str
    normalized_url: str
    domain: str | None
    snippet: str | None
    score: float
    reasons: dict | None
    source_name: str | None = None
    source_ref: str | None = None


@dataclass(slots=True)
class TrendSourceCandidateInput:
    topic_id: int
    domain: str
    source_url: str | None
    score: float
    reasons: dict | None


class TrendCandidateRepository:
    async def create_topic(
        self,
        session: AsyncSession,
        *,
        payload: TrendTopicInput,
    ) -> TrendTopic:
        topic = TrendTopic(
            profile_id=payload.profile_id,
            topic_name=payload.topic_name,
            topic_slug=payload.topic_slug,
            trend_score=payload.trend_score,
            confidence=payload.confidence,
            reasons=payload.reasons,
            discovered_at=payload.discovered_at,
        )
        session.add(topic)
        await session.flush()
        return topic

    async def get_topic(self, session: AsyncSession, topic_id: int) -> TrendTopic | None:
        result = await session.execute(select(TrendTopic).where(TrendTopic.id == topic_id))
        return result.scalar_one_or_none()

    async def list_topics_since(
        self,
        session: AsyncSession,
        *,
        since: datetime,
        limit: int,
    ) -> list[TrendTopic]:
        result = await session.execute(
            select(TrendTopic)
            .where(TrendTopic.discovered_at >= since)
            .order_by(TrendTopic.discovered_at.desc(), TrendTopic.trend_score.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create_or_update_article_candidate(
        self,
        session: AsyncSession,
        *,
        payload: TrendArticleCandidateInput,
    ) -> TrendArticleCandidate:
        existing = await self.get_article_candidate_by_normalized_url(
            session,
            normalized_url=payload.normalized_url,
        )
        if existing is None:
            candidate = TrendArticleCandidate(
                topic_id=payload.topic_id,
                title=payload.title,
                url=payload.url,
                normalized_url=payload.normalized_url,
                domain=payload.domain,
                snippet=payload.snippet,
                score=payload.score,
                reasons=payload.reasons,
                source_name=payload.source_name,
                source_ref=payload.source_ref,
            )
            session.add(candidate)
            await session.flush()
            return candidate

        if existing.status in {
            TrendCandidateStatus.INGESTED,
            TrendCandidateStatus.APPROVED,
        }:
            return existing

        existing.topic_id = payload.topic_id
        existing.title = payload.title
        existing.url = payload.url
        existing.domain = payload.domain
        existing.snippet = payload.snippet
        existing.score = payload.score
        existing.reasons = payload.reasons
        existing.source_name = payload.source_name
        existing.source_ref = payload.source_ref
        await session.flush()
        return existing

    async def get_article_candidate(
        self,
        session: AsyncSession,
        candidate_id: int,
    ) -> TrendArticleCandidate | None:
        result = await session.execute(
            select(TrendArticleCandidate).where(TrendArticleCandidate.id == candidate_id)
        )
        return result.scalar_one_or_none()

    async def get_article_candidate_by_normalized_url(
        self,
        session: AsyncSession,
        *,
        normalized_url: str,
    ) -> TrendArticleCandidate | None:
        result = await session.execute(
            select(TrendArticleCandidate).where(
                TrendArticleCandidate.normalized_url == normalized_url
            )
        )
        return result.scalar_one_or_none()

    async def list_article_candidates(
        self,
        session: AsyncSession,
        *,
        topic_id: int,
        limit: int,
    ) -> list[TrendArticleCandidate]:
        result = await session.execute(
            select(TrendArticleCandidate)
            .where(TrendArticleCandidate.topic_id == topic_id)
            .order_by(TrendArticleCandidate.score.desc(), TrendArticleCandidate.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_pending_article_candidates(self, session: AsyncSession) -> int:
        result = await session.execute(
            select(TrendArticleCandidate.id).where(
                TrendArticleCandidate.status == TrendCandidateStatus.PENDING
            )
        )
        return len(result.scalars().all())

    async def list_pending_article_candidates(
        self,
        session: AsyncSession,
        *,
        limit: int,
        offset: int,
    ) -> list[TrendArticleCandidate]:
        result = await session.execute(
            select(TrendArticleCandidate)
            .where(TrendArticleCandidate.status == TrendCandidateStatus.PENDING)
            .order_by(TrendArticleCandidate.score.desc(), TrendArticleCandidate.id.asc())
            .limit(limit)
            .offset(max(offset, 0))
        )
        return list(result.scalars().all())

    async def create_or_update_source_candidate(
        self,
        session: AsyncSession,
        *,
        payload: TrendSourceCandidateInput,
    ) -> TrendSourceCandidate:
        existing = await self.get_source_candidate_by_topic_domain(
            session,
            topic_id=payload.topic_id,
            domain=payload.domain,
        )
        if existing is None:
            candidate = TrendSourceCandidate(
                topic_id=payload.topic_id,
                domain=payload.domain,
                source_url=payload.source_url,
                score=payload.score,
                reasons=payload.reasons,
            )
            session.add(candidate)
            await session.flush()
            return candidate

        if existing.status == TrendCandidateStatus.APPROVED:
            return existing

        existing.source_url = payload.source_url
        existing.score = payload.score
        existing.reasons = payload.reasons
        await session.flush()
        return existing

    async def get_source_candidate(
        self,
        session: AsyncSession,
        candidate_id: int,
    ) -> TrendSourceCandidate | None:
        result = await session.execute(
            select(TrendSourceCandidate).where(TrendSourceCandidate.id == candidate_id)
        )
        return result.scalar_one_or_none()

    async def get_source_candidate_by_topic_domain(
        self,
        session: AsyncSession,
        *,
        topic_id: int,
        domain: str,
    ) -> TrendSourceCandidate | None:
        result = await session.execute(
            select(TrendSourceCandidate)
            .where(TrendSourceCandidate.topic_id == topic_id)
            .where(TrendSourceCandidate.domain == domain)
        )
        return result.scalar_one_or_none()

    async def list_source_candidates(
        self,
        session: AsyncSession,
        *,
        topic_id: int,
        limit: int,
    ) -> list[TrendSourceCandidate]:
        result = await session.execute(
            select(TrendSourceCandidate)
            .where(TrendSourceCandidate.topic_id == topic_id)
            .order_by(TrendSourceCandidate.score.desc(), TrendSourceCandidate.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_pending_source_candidates(self, session: AsyncSession) -> int:
        result = await session.execute(
            select(TrendSourceCandidate.id).where(
                TrendSourceCandidate.status == TrendCandidateStatus.PENDING
            )
        )
        return len(result.scalars().all())

    async def list_pending_source_candidates(
        self,
        session: AsyncSession,
        *,
        limit: int,
        offset: int,
    ) -> list[TrendSourceCandidate]:
        result = await session.execute(
            select(TrendSourceCandidate)
            .where(TrendSourceCandidate.status == TrendCandidateStatus.PENDING)
            .order_by(TrendSourceCandidate.score.desc(), TrendSourceCandidate.id.asc())
            .limit(limit)
            .offset(max(offset, 0))
        )
        return list(result.scalars().all())
