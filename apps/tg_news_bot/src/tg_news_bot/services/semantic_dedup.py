"""Semantic near-duplicate detection using lightweight embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import math
import re

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_news_bot.config import SemanticDedupSettings
from tg_news_bot.repositories.semantic_fingerprints import SemanticFingerprintRepository


@dataclass(slots=True)
class NearDuplicateMatch:
    normalized_url: str
    similarity: float


class SemanticDedupService:
    def __init__(
        self,
        *,
        settings: SemanticDedupSettings,
        session_factory: async_sessionmaker[AsyncSession],
        repository: SemanticFingerprintRepository | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._repo = repository or SemanticFingerprintRepository()

    async def find_near_duplicate(
        self,
        *,
        normalized_url: str,
        domain: str | None,
        title: str | None,
        text: str | None,
    ) -> NearDuplicateMatch | None:
        if not self._settings.enabled:
            return None
        vector, text_hash = self._make_embedding(title=title, text=text)
        if not vector:
            return None
        since = datetime.now(timezone.utc) - timedelta(hours=self._settings.lookback_hours)
        async with self._session_factory() as session:
            async with session.begin():
                candidates = await self._repo.list_recent_candidates(
                    session,
                    since=since,
                    domain=domain,
                    limit=self._settings.max_candidates,
                )
                if not candidates and domain:
                    candidates = await self._repo.list_recent_candidates(
                        session,
                        since=since,
                        domain=None,
                        limit=self._settings.max_candidates,
                    )

        best_url = ""
        best_score = 0.0
        for candidate in candidates:
            if candidate.normalized_url == normalized_url:
                continue
            if candidate.text_hash and candidate.text_hash == text_hash:
                return NearDuplicateMatch(
                    normalized_url=candidate.normalized_url,
                    similarity=1.0,
                )
            score = _cosine_similarity(vector, candidate.vector)
            if score > best_score:
                best_score = score
                best_url = candidate.normalized_url

        if best_score >= self._settings.similarity_threshold and best_url:
            return NearDuplicateMatch(normalized_url=best_url, similarity=best_score)
        return None

    async def store(
        self,
        *,
        normalized_url: str,
        domain: str | None,
        title: str | None,
        text: str | None,
    ) -> None:
        if not self._settings.enabled:
            return
        vector, text_hash = self._make_embedding(title=title, text=text)
        async with self._session_factory() as session:
            async with session.begin():
                await self._repo.upsert(
                    session,
                    normalized_url=normalized_url,
                    domain=domain,
                    vector=vector if self._settings.store_vectors else None,
                    text_hash=text_hash,
                )

    def _make_embedding(self, *, title: str | None, text: str | None) -> tuple[list[float], str]:
        content = f"{title or ''}\n{text or ''}".strip().lower()
        compact = re.sub(r"\s+", " ", content)
        text_hash = hashlib.sha1(compact.encode("utf-8")).hexdigest()
        tokens = [token for token in re.split(r"[^a-z0-9]+", compact) if len(token) >= 3]
        if not tokens:
            return [], text_hash

        dims = self._settings.dimensions
        vector = [0.0] * dims
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], byteorder="big") % dims
            sign = -1.0 if (digest[4] & 1) else 1.0
            vector[idx] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return [], text_hash
        normalized = [value / norm for value in vector]
        return normalized, text_hash


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return float(sum(a * b for a, b in zip(left, right)))
