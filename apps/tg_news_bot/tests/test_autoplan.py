from __future__ import annotations

from datetime import datetime, timezone

from tg_news_bot.services.autoplan import (
    AutoPlanDraft,
    AutoPlanRules,
    build_autoplan,
    rules_from_payload,
    rules_to_payload,
)


def test_build_autoplan_respects_gap_and_daily_limit() -> None:
    now = datetime(2026, 2, 20, 8, 5, tzinfo=timezone.utc)
    rules = AutoPlanRules(
        timezone_name="UTC",
        min_gap_minutes=120,
        max_posts_per_day=2,
        quiet_start_hour=23,
        quiet_end_hour=8,
        slot_step_minutes=30,
        horizon_hours=24,
    )
    drafts = [
        AutoPlanDraft(draft_id=1, score=5.0, created_at=now, source_trust=1.0),
        AutoPlanDraft(draft_id=2, score=4.5, created_at=now, source_trust=0.0),
        AutoPlanDraft(draft_id=3, score=4.0, created_at=now, source_trust=0.0),
        AutoPlanDraft(draft_id=4, score=3.5, created_at=now, source_trust=0.0),
    ]

    result = build_autoplan(
        drafts=drafts,
        existing_schedule_utc=[],
        rules=rules,
        now_utc=now,
        limit=10,
    )

    assert len(result.scheduled) == 3
    assert [item.draft_id for item in result.scheduled] == [1, 2, 3]
    assert result.scheduled[0].schedule_at == datetime(2026, 2, 20, 8, 30, tzinfo=timezone.utc)
    assert result.scheduled[1].schedule_at == datetime(2026, 2, 20, 10, 30, tzinfo=timezone.utc)
    assert result.scheduled[2].schedule_at == datetime(2026, 2, 21, 8, 0, tzinfo=timezone.utc)
    assert result.unscheduled == [4]


def test_build_autoplan_accounts_for_existing_scheduled_posts() -> None:
    now = datetime(2026, 2, 20, 8, 5, tzinfo=timezone.utc)
    rules = AutoPlanRules(
        timezone_name="UTC",
        min_gap_minutes=120,
        max_posts_per_day=4,
        quiet_start_hour=23,
        quiet_end_hour=8,
        slot_step_minutes=30,
        horizon_hours=24,
    )
    drafts = [
        AutoPlanDraft(draft_id=10, score=5.0, created_at=now, source_trust=0.0),
    ]
    existing = [
        datetime(2026, 2, 20, 8, 30, tzinfo=timezone.utc),
        datetime(2026, 2, 20, 10, 30, tzinfo=timezone.utc),
    ]

    result = build_autoplan(
        drafts=drafts,
        existing_schedule_utc=existing,
        rules=rules,
        now_utc=now,
        limit=5,
    )

    assert len(result.scheduled) == 1
    assert result.scheduled[0].draft_id == 10
    assert result.scheduled[0].schedule_at == datetime(2026, 2, 20, 12, 30, tzinfo=timezone.utc)


def test_rules_payload_roundtrip_and_bounds() -> None:
    raw = {
        "timezone_name": "Europe/Moscow",
        "min_gap_minutes": 1,
        "max_posts_per_day": 99,
        "quiet_start_hour": -5,
        "quiet_end_hour": 77,
        "slot_step_minutes": 1,
        "horizon_hours": 999,
    }

    rules = rules_from_payload(raw, timezone_name="UTC")
    payload = rules_to_payload(rules)

    assert rules.timezone_name == "Europe/Moscow"
    assert rules.min_gap_minutes == 10
    assert rules.max_posts_per_day == 24
    assert rules.quiet_start_hour == 0
    assert rules.quiet_end_hour == 23
    assert rules.slot_step_minutes == 5
    assert rules.horizon_hours == 168
    assert payload["timezone_name"] == "Europe/Moscow"
