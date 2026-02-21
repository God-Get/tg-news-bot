"""Source trust score and auto-demotion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from tg_news_bot.config import SourceQualitySettings
from tg_news_bot.logging import get_logger
from tg_news_bot.repositories.sources import SourceRepository


@dataclass(slots=True)
class SourceQualityResult:
    source_id: int
    source_name: str
    trust_score: float
    auto_disabled: bool
    events_total: int
    consecutive_failures: int


class SourceQualityService:
    def __init__(
        self,
        settings: SourceQualitySettings,
        *,
        source_repo: SourceRepository | None = None,
    ) -> None:
        self._settings = settings
        self._source_repo = source_repo or SourceRepository()
        self._log = get_logger(__name__)

    async def apply_event(
        self,
        session: AsyncSession,
        *,
        source_id: int | None,
        event: str,
        details: dict | None = None,
    ) -> SourceQualityResult | None:
        if not self._settings.enabled or source_id is None:
            return None
        source = await self._source_repo.get_by_id(session, source_id)
        if source is None:
            return None

        delta = self._delta_for_event(event)
        source.trust_score = float(source.trust_score or 0.0) + delta
        tags = source.tags if isinstance(source.tags, dict) else {}
        quality = tags.get("quality") if isinstance(tags.get("quality"), dict) else {}
        events = quality.get("events") if isinstance(quality.get("events"), dict) else {}
        health = quality.get("health") if isinstance(quality.get("health"), dict) else {}

        events[event] = int(events.get(event, 0)) + 1
        events_total = int(quality.get("events_total", 0)) + 1
        consecutive_failures = int(health.get("consecutive_failures", 0))
        if event == "created":
            consecutive_failures = 0
        elif event in {
            "rss_http_error",
            "rss_http_403",
            "rss_empty",
            "no_html",
            "invalid_entry",
            "blocked",
            "unsafe",
            "low_score",
            "high_duplicate_rate",
        }:
            consecutive_failures += 1
        elif event in {"duplicate", "near_duplicate"}:
            consecutive_failures = max(consecutive_failures, 1)

        if event == "rss_http_error":
            health["rss_http_errors"] = int(health.get("rss_http_errors", 0)) + 1
        if event == "rss_http_403":
            health["rss_http_403"] = int(health.get("rss_http_403", 0)) + 1
        if event == "rss_empty":
            health["rss_empty"] = int(health.get("rss_empty", 0)) + 1
        if event == "duplicate":
            health["duplicates_total"] = int(health.get("duplicates_total", 0)) + 1
        if event == "created":
            health["created_total"] = int(health.get("created_total", 0)) + 1
        if event == "high_duplicate_rate":
            health["high_duplicate_rate_hits"] = int(health.get("high_duplicate_rate_hits", 0)) + 1

        health["consecutive_failures"] = consecutive_failures
        quality["health"] = health
        quality["events"] = events
        quality["events_total"] = events_total
        quality["last_event"] = event
        quality["last_event_at"] = datetime.now(timezone.utc).isoformat()
        if details:
            quality["last_event_details"] = details
        quality["trust_score"] = source.trust_score
        tags["quality"] = quality

        auto_disabled = False
        if (
            self._settings.auto_disable_enabled
            and source.enabled
            and events_total >= self._settings.min_events_for_auto_disable
            and source.trust_score <= self._settings.auto_disable_threshold
        ):
            source.enabled = False
            auto_disabled = True
            quality["auto_disabled"] = True
            quality["auto_disabled_at"] = datetime.now(timezone.utc).isoformat()
            self._log.warning(
                "source_quality.auto_disabled",
                source_id=source_id,
                trust_score=source.trust_score,
                events_total=events_total,
            )
        elif (
            self._settings.auto_disable_enabled
            and source.enabled
            and consecutive_failures >= self._settings.consecutive_failures_disable_threshold
        ):
            source.enabled = False
            auto_disabled = True
            quality["auto_disabled"] = True
            quality["auto_disabled_at"] = datetime.now(timezone.utc).isoformat()
            quality["auto_disabled_reason"] = "consecutive_failures"
            self._log.warning(
                "source_quality.auto_disabled_consecutive_failures",
                source_id=source_id,
                consecutive_failures=consecutive_failures,
            )
        source.tags = tags
        await session.flush()

        return SourceQualityResult(
            source_id=source_id,
            source_name=source.name,
            trust_score=float(source.trust_score),
            auto_disabled=auto_disabled,
            events_total=events_total,
            consecutive_failures=consecutive_failures,
        )

    def _delta_for_event(self, event: str) -> float:
        mapping = {
            "created": self._settings.created_delta,
            "duplicate": self._settings.duplicate_delta,
            "blocked": self._settings.blocked_delta,
            "low_score": self._settings.low_score_delta,
            "no_html": self._settings.no_html_delta,
            "invalid_entry": self._settings.invalid_entry_delta,
            "unsafe": self._settings.unsafe_delta,
            "near_duplicate": self._settings.near_duplicate_delta,
            "rss_http_error": self._settings.rss_http_error_delta,
            "rss_http_403": self._settings.rss_http_403_delta,
            "rss_empty": self._settings.rss_empty_delta,
            "high_duplicate_rate": self._settings.high_duplicate_rate_delta,
        }
        return float(mapping.get(event, 0.0))
