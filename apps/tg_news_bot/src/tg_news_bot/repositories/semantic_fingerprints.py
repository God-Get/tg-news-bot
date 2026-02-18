"""Semantic fingerprint repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.db.models import SemanticFingerprint


@dataclass(slots=True)
class FingerprintCandidate:
    normalized_url: str
    domain: str | None
    vector: list[float]
    text_hash: str
    created_at: datetime


class SemanticFingerprintRepository:
    async def get_by_url(self, session: AsyncSession, *, normalized_url: str) -> SemanticFingerprint | None:
        result = await session.execute(
            select(SemanticFingerprint).where(SemanticFingerprint.normalized_url == normalized_url)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        session: AsyncSession,
        *,
        normalized_url: str,
        domain: str | None,
        vector: list[float] | None,
        text_hash: str,
    ) -> SemanticFingerprint:
        row = await self.get_by_url(session, normalized_url=normalized_url)
        payload = {"values": vector} if vector is not None else None
        if row is None:
            row = SemanticFingerprint(
                normalized_url=normalized_url,
                domain=domain,
                vector=payload,
                text_hash=text_hash,
            )
            session.add(row)
            await session.flush()
            return row
        row.domain = domain
        row.vector = payload
        row.text_hash = text_hash
        await session.flush()
        return row

    async def list_recent_candidates(
        self,
        session: AsyncSession,
        *,
        since: datetime,
        domain: str | None,
        limit: int,
    ) -> list[FingerprintCandidate]:
        query = (
            select(SemanticFingerprint)
            .where(SemanticFingerprint.created_at >= since)
            .order_by(SemanticFingerprint.created_at.desc())
            .limit(limit)
        )
        if domain:
            query = query.where(SemanticFingerprint.domain == domain)
        result = await session.execute(query)
        rows = list(result.scalars().all())
        candidates: list[FingerprintCandidate] = []
        for row in rows:
            payload = row.vector if isinstance(row.vector, dict) else None
            values = payload.get("values") if payload else None
            if not isinstance(values, list):
                continue
            vector: list[float] = []
            valid = True
            for item in values:
                try:
                    vector.append(float(item))
                except (TypeError, ValueError):
                    valid = False
                    break
            if not valid or not vector:
                continue
            candidates.append(
                FingerprintCandidate(
                    normalized_url=row.normalized_url,
                    domain=row.domain,
                    vector=vector,
                    text_hash=row.text_hash or "",
                    created_at=row.created_at,
                )
            )
        return candidates
