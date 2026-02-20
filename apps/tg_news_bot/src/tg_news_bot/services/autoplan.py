"""Smart autoplan scheduler for READY drafts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_news_bot.db.models import (
    BotSettings,
    Draft,
    DraftState,
    ScheduledPost,
    ScheduledPostStatus,
    Source,
)
from tg_news_bot.logging import get_logger
from tg_news_bot.repositories.bot_settings import BotSettingsRepository
from tg_news_bot.services.workflow import DraftWorkflowService
from tg_news_bot.services.workflow_types import DraftAction, TransitionRequest


@dataclass(slots=True)
class AutoPlanRules:
    timezone_name: str = "UTC"
    min_gap_minutes: int = 90
    max_posts_per_day: int = 6
    quiet_start_hour: int = 23
    quiet_end_hour: int = 8
    slot_step_minutes: int = 30
    horizon_hours: int = 24


@dataclass(slots=True)
class AutoPlanDraft:
    draft_id: int
    score: float
    created_at: datetime
    source_trust: float


@dataclass(slots=True)
class AutoPlanEntry:
    draft_id: int
    schedule_at: datetime
    priority: float
    reason: str


@dataclass(slots=True)
class AutoPlanResult:
    rules: AutoPlanRules
    window_hours: int
    considered_count: int
    scheduled: list[AutoPlanEntry]
    unscheduled: list[int]


@dataclass(slots=True)
class AutoPlanApplyResult:
    preview: AutoPlanResult
    scheduled_count: int
    failed_drafts: list[int]


def _bounded_int(value: object, *, fallback: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return min(max(parsed, min_value), max_value)


def rules_to_payload(rules: AutoPlanRules) -> dict[str, int | str]:
    return {
        "timezone_name": rules.timezone_name,
        "min_gap_minutes": int(rules.min_gap_minutes),
        "max_posts_per_day": int(rules.max_posts_per_day),
        "quiet_start_hour": int(rules.quiet_start_hour),
        "quiet_end_hour": int(rules.quiet_end_hour),
        "slot_step_minutes": int(rules.slot_step_minutes),
        "horizon_hours": int(rules.horizon_hours),
    }


def rules_from_payload(payload: dict | None, *, timezone_name: str) -> AutoPlanRules:
    base = AutoPlanRules(timezone_name=timezone_name)
    if not isinstance(payload, dict):
        return base

    tz_name = str(payload.get("timezone_name") or "").strip() or timezone_name
    return AutoPlanRules(
        timezone_name=tz_name,
        min_gap_minutes=_bounded_int(
            payload.get("min_gap_minutes"),
            fallback=base.min_gap_minutes,
            min_value=10,
            max_value=24 * 60,
        ),
        max_posts_per_day=_bounded_int(
            payload.get("max_posts_per_day"),
            fallback=base.max_posts_per_day,
            min_value=1,
            max_value=24,
        ),
        quiet_start_hour=_bounded_int(
            payload.get("quiet_start_hour"),
            fallback=base.quiet_start_hour,
            min_value=0,
            max_value=23,
        ),
        quiet_end_hour=_bounded_int(
            payload.get("quiet_end_hour"),
            fallback=base.quiet_end_hour,
            min_value=0,
            max_value=23,
        ),
        slot_step_minutes=_bounded_int(
            payload.get("slot_step_minutes"),
            fallback=base.slot_step_minutes,
            min_value=5,
            max_value=180,
        ),
        horizon_hours=_bounded_int(
            payload.get("horizon_hours"),
            fallback=base.horizon_hours,
            min_value=1,
            max_value=168,
        ),
    )


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _align_to_step(value: datetime, step_minutes: int) -> datetime:
    local = value.replace(second=0, microsecond=0)
    remainder = local.minute % step_minutes
    if remainder == 0:
        return local
    return local + timedelta(minutes=step_minutes - remainder)


def _is_quiet_hour(value: datetime, *, start_hour: int, end_hour: int) -> bool:
    if start_hour == end_hour:
        return False
    hour = value.hour
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def _priority_for_draft(draft: AutoPlanDraft, *, now_utc: datetime) -> tuple[float, str]:
    score = float(draft.score or 0.0)
    age_hours = max(0.0, (now_utc - _to_utc(draft.created_at)).total_seconds() / 3600.0)
    freshness_boost = max(0.0, 1.25 - age_hours / 24.0)
    trust_component = max(-1.0, min(float(draft.source_trust or 0.0), 5.0))
    trust_boost = trust_component * 0.2
    priority = score + freshness_boost + trust_boost
    reason = (
        f"score={score:.2f}; freshness={freshness_boost:.2f}; "
        f"source_trust={trust_component:.2f}"
    )
    return priority, reason


def _find_next_slot(
    *,
    now_local: datetime,
    end_local: datetime,
    occupied_local: list[datetime],
    daily_counts: dict[datetime.date, int],
    rules: AutoPlanRules,
) -> datetime | None:
    candidate = _align_to_step(now_local, rules.slot_step_minutes)
    step = timedelta(minutes=rules.slot_step_minutes)
    min_gap_seconds = rules.min_gap_minutes * 60

    while candidate <= end_local:
        if _is_quiet_hour(
            candidate,
            start_hour=rules.quiet_start_hour,
            end_hour=rules.quiet_end_hour,
        ):
            candidate += step
            continue

        if daily_counts.get(candidate.date(), 0) >= rules.max_posts_per_day:
            candidate += step
            continue

        too_close = any(
            abs((candidate - scheduled_at).total_seconds()) < min_gap_seconds
            for scheduled_at in occupied_local
        )
        if too_close:
            candidate += step
            continue

        return candidate

    return None


def build_autoplan(
    *,
    drafts: list[AutoPlanDraft],
    existing_schedule_utc: list[datetime],
    rules: AutoPlanRules,
    now_utc: datetime,
    limit: int,
    hours_override: int | None = None,
) -> AutoPlanResult:
    tz = ZoneInfo(rules.timezone_name)
    window_hours = _bounded_int(
        hours_override if hours_override is not None else rules.horizon_hours,
        fallback=rules.horizon_hours,
        min_value=1,
        max_value=168,
    )
    safe_limit = _bounded_int(limit, fallback=10, min_value=1, max_value=100)

    now_utc = _to_utc(now_utc)
    now_local = now_utc.astimezone(tz)
    end_local = (now_utc + timedelta(hours=window_hours)).astimezone(tz)

    occupied_local: list[datetime] = []
    daily_counts: dict[datetime.date, int] = {}

    for value in existing_schedule_utc:
        normalized = _to_utc(value).astimezone(tz).replace(second=0, microsecond=0)
        occupied_local.append(normalized)
        if normalized >= now_local:
            daily_counts[normalized.date()] = daily_counts.get(normalized.date(), 0) + 1

    ranked: list[tuple[float, AutoPlanDraft, str]] = []
    for draft in drafts:
        priority, reason = _priority_for_draft(draft, now_utc=now_utc)
        ranked.append((priority, draft, reason))
    ranked.sort(key=lambda item: item[0], reverse=True)

    scheduled: list[AutoPlanEntry] = []
    unscheduled: list[int] = []

    for priority, draft, reason in ranked:
        if len(scheduled) >= safe_limit:
            break
        slot_local = _find_next_slot(
            now_local=now_local,
            end_local=end_local,
            occupied_local=occupied_local,
            daily_counts=daily_counts,
            rules=rules,
        )
        if slot_local is None:
            unscheduled.append(draft.draft_id)
            continue
        occupied_local.append(slot_local)
        daily_counts[slot_local.date()] = daily_counts.get(slot_local.date(), 0) + 1
        scheduled.append(
            AutoPlanEntry(
                draft_id=draft.draft_id,
                schedule_at=slot_local.astimezone(timezone.utc),
                priority=priority,
                reason=reason,
            )
        )

    return AutoPlanResult(
        rules=rules,
        window_hours=window_hours,
        considered_count=len(ranked),
        scheduled=scheduled,
        unscheduled=unscheduled,
    )


class AutoPlanService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        timezone_name: str,
        workflow: DraftWorkflowService | None = None,
        settings_repo: BotSettingsRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._workflow = workflow
        self._timezone_name = timezone_name
        self._settings_repo = settings_repo or BotSettingsRepository()
        self._log = get_logger(__name__)

    async def get_rules(self) -> AutoPlanRules:
        async with self._session_factory() as session:
            async with session.begin():
                settings = await self._settings_repo.get_or_create(session)
                payload = settings.autoplan_rules if isinstance(settings.autoplan_rules, dict) else None
        return rules_from_payload(payload, timezone_name=self._timezone_name)

    async def set_rules(self, rules: AutoPlanRules) -> AutoPlanRules:
        payload = rules_to_payload(rules)
        async with self._session_factory() as session:
            async with session.begin():
                settings = await self._settings_repo.get_or_create(session)
                settings.autoplan_rules = payload
                await session.flush()
        return rules

    async def preview(self, *, hours: int | None = None, limit: int = 10) -> AutoPlanResult:
        rules = await self.get_rules()
        now_utc = datetime.now(timezone.utc)
        safe_limit = _bounded_int(limit, fallback=10, min_value=1, max_value=100)
        fetch_limit = min(max(safe_limit * 6, 30), 400)

        async with self._session_factory() as session:
            async with session.begin():
                drafts_result = await session.execute(
                    select(Draft)
                    .where(Draft.state == DraftState.READY)
                    .order_by(Draft.updated_at.asc())
                    .limit(fetch_limit)
                )
                ready_drafts = list(drafts_result.scalars().all())

                source_ids = sorted(
                    {int(item.source_id) for item in ready_drafts if item.source_id is not None}
                )
                trust_by_source: dict[int, float] = {}
                if source_ids:
                    sources_result = await session.execute(
                        select(Source.id, Source.trust_score).where(Source.id.in_(source_ids))
                    )
                    trust_by_source = {
                        int(source_id): float(trust_score or 0.0)
                        for source_id, trust_score in sources_result.all()
                    }

                scheduled_result = await session.execute(
                    select(ScheduledPost.schedule_at).where(
                        ScheduledPost.status == ScheduledPostStatus.SCHEDULED
                    )
                )
                existing_schedule = [
                    _to_utc(value)
                    for value in scheduled_result.scalars().all()
                    if isinstance(value, datetime)
                ]

        candidates = [
            AutoPlanDraft(
                draft_id=int(item.id),
                score=float(item.score or 0.0),
                created_at=item.created_at,
                source_trust=(
                    trust_by_source.get(int(item.source_id), 0.0)
                    if item.source_id is not None
                    else 0.0
                ),
            )
            for item in ready_drafts
        ]
        return build_autoplan(
            drafts=candidates,
            existing_schedule_utc=existing_schedule,
            rules=rules,
            now_utc=now_utc,
            limit=safe_limit,
            hours_override=hours,
        )

    async def apply(
        self,
        *,
        user_id: int,
        hours: int | None = None,
        limit: int = 10,
    ) -> AutoPlanApplyResult:
        if self._workflow is None:
            raise RuntimeError("workflow is not configured for autoplan apply")

        preview = await self.preview(hours=hours, limit=limit)
        failed: list[int] = []
        scheduled_count = 0

        for item in preview.scheduled:
            try:
                await self._workflow.transition(
                    TransitionRequest(
                        draft_id=item.draft_id,
                        action=DraftAction.SCHEDULE,
                        user_id=user_id,
                        schedule_at=item.schedule_at,
                    )
                )
                scheduled_count += 1
            except Exception:
                failed.append(item.draft_id)
                self._log.exception("autoplan.apply_failed", draft_id=item.draft_id)

        return AutoPlanApplyResult(
            preview=preview,
            scheduled_count=scheduled_count,
            failed_drafts=failed,
        )


def render_rules(rules: AutoPlanRules) -> str:
    return (
        f"timezone: {rules.timezone_name}\n"
        f"min_gap_minutes: {rules.min_gap_minutes}\n"
        f"max_posts_per_day: {rules.max_posts_per_day}\n"
        f"quiet_hours: {rules.quiet_start_hour:02d}:00-{rules.quiet_end_hour:02d}:00\n"
        f"slot_step_minutes: {rules.slot_step_minutes}\n"
        f"horizon_hours: {rules.horizon_hours}"
    )

