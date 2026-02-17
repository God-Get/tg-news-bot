from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from tg_news_bot.db.models import BotSettings, Draft, DraftState, ScheduledPostStatus
from tg_news_bot.services.scheduler import SchedulerConfig, SchedulerRunner


class _AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


class _DummySession:
    def begin(self) -> _AsyncContext:
        return _AsyncContext()


class _DummySessionFactory:
    def __init__(self, session: _DummySession) -> None:
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


@dataclass
class _ScheduledRow:
    draft_id: int
    status: ScheduledPostStatus = ScheduledPostStatus.SCHEDULED
    schedule_at: datetime = datetime.now(timezone.utc)
    id: int = 1
    attempts: int = 0
    last_error: str | None = None
    next_retry_at: datetime | None = None
    updated_at: datetime = datetime.now(timezone.utc)


class _ScheduledRepo:
    def __init__(self, rows: list[_ScheduledRow]) -> None:
        self.rows = rows

    async def count_pending(self, session) -> int:  # noqa: ANN001
        now = datetime.now(timezone.utc)
        return sum(
            1
            for row in self.rows
            if row.status == ScheduledPostStatus.SCHEDULED
            or (
                row.status == ScheduledPostStatus.FAILED
                and row.next_retry_at is not None
                and row.next_retry_at <= now
            )
        )

    async def list_due_for_update(self, session, *, now: datetime, limit: int):  # noqa: ANN001
        due = [
            row
            for row in self.rows
            if (
                row.status == ScheduledPostStatus.SCHEDULED and row.schedule_at <= now
            )
            or (
                row.status == ScheduledPostStatus.FAILED
                and row.next_retry_at is not None
                and row.next_retry_at <= now
            )
        ]
        return due[:limit]

    async def list_failed_without_retry_for_update(self, session, *, now: datetime, limit: int):  # noqa: ANN001
        rows = [
            row
            for row in self.rows
            if row.status == ScheduledPostStatus.FAILED
            and row.next_retry_at is None
            and row.updated_at <= now
        ]
        return rows[:limit]


class _DraftRepo:
    def __init__(self, drafts: dict[int, Draft]) -> None:
        self.drafts = drafts

    async def get_for_update(self, session, draft_id: int) -> Draft:  # noqa: ANN001
        return self.drafts[draft_id]


class _SettingsRepo:
    def __init__(self) -> None:
        self.settings = BotSettings(
            group_chat_id=-1001,
            inbox_topic_id=11,
            editing_topic_id=12,
            ready_topic_id=13,
            scheduled_topic_id=14,
            published_topic_id=15,
            archive_topic_id=16,
            channel_id=-1002,
        )

    async def get_or_create(self, session):  # noqa: ANN001
        return self.settings


class _PublishFailureRepo:
    def __init__(self) -> None:
        self.created = []
        self.resolved = []

    async def create(self, session, **kwargs):  # noqa: ANN001
        self.created.append(kwargs)

    async def mark_resolved_for_draft(self, session, *, draft_id: int):  # noqa: ANN001
        self.resolved.append(draft_id)


class _WorkflowSpy:
    def __init__(self) -> None:
        self.publish_calls = 0
        self.move_calls = 0
        self.fail_publish = False

    async def _publish_now(self, session, draft, settings) -> None:  # noqa: ANN001
        self.publish_calls += 1
        if self.fail_publish:
            raise RuntimeError("publish failed")

    async def _move_in_group(self, *, session, draft, settings, target_state) -> None:  # noqa: ANN001
        self.move_calls += 1



def _make_draft(draft_id: int, state: DraftState) -> Draft:
    return Draft(
        id=draft_id,
        state=state,
        normalized_url=f"https://example.com/{draft_id}",
        domain="example.com",
        title_en="title",
        post_text_ru="text",
    )


@pytest.mark.asyncio
async def test_scheduler_publishes_due_draft() -> None:
    due_row = _ScheduledRow(
        draft_id=1,
        status=ScheduledPostStatus.SCHEDULED,
        schedule_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    drafts = {1: _make_draft(1, DraftState.SCHEDULED)}
    workflow = _WorkflowSpy()
    failure_repo = _PublishFailureRepo()

    runner = SchedulerRunner(
        session_factory=_DummySessionFactory(_DummySession()),
        workflow=workflow,
        config=SchedulerConfig(poll_interval_seconds=10, batch_size=20),
        scheduled_repo=_ScheduledRepo([due_row]),
        draft_repo=_DraftRepo(drafts),
        settings_repo=_SettingsRepo(),
        publish_failure_repo=failure_repo,
    )

    await runner._process_due()

    assert workflow.publish_calls == 1
    assert workflow.move_calls == 1
    assert drafts[1].state == DraftState.PUBLISHED
    assert due_row.status == ScheduledPostStatus.PUBLISHED
    assert failure_repo.resolved == [1]


@pytest.mark.asyncio
async def test_scheduler_retries_on_publish_failure() -> None:
    due_row = _ScheduledRow(
        draft_id=2,
        status=ScheduledPostStatus.SCHEDULED,
        schedule_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    drafts = {2: _make_draft(2, DraftState.SCHEDULED)}
    workflow = _WorkflowSpy()
    workflow.fail_publish = True
    failure_repo = _PublishFailureRepo()

    runner = SchedulerRunner(
        session_factory=_DummySessionFactory(_DummySession()),
        workflow=workflow,
        config=SchedulerConfig(
            poll_interval_seconds=10,
            batch_size=20,
            max_publish_attempts=3,
            retry_backoff_seconds=60,
        ),
        scheduled_repo=_ScheduledRepo([due_row]),
        draft_repo=_DraftRepo(drafts),
        settings_repo=_SettingsRepo(),
        publish_failure_repo=failure_repo,
    )

    await runner._process_due()

    assert due_row.status == ScheduledPostStatus.FAILED
    assert due_row.attempts == 1
    assert due_row.next_retry_at is not None
    assert len(failure_repo.created) == 1


@pytest.mark.asyncio
async def test_scheduler_moves_to_dlq_after_max_attempts() -> None:
    due_row = _ScheduledRow(
        draft_id=3,
        status=ScheduledPostStatus.FAILED,
        schedule_at=datetime.now(timezone.utc) - timedelta(hours=1),
        attempts=2,
        next_retry_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    drafts = {3: _make_draft(3, DraftState.SCHEDULED)}
    workflow = _WorkflowSpy()
    workflow.fail_publish = True
    failure_repo = _PublishFailureRepo()

    runner = SchedulerRunner(
        session_factory=_DummySessionFactory(_DummySession()),
        workflow=workflow,
        config=SchedulerConfig(
            poll_interval_seconds=10,
            batch_size=20,
            max_publish_attempts=3,
            retry_backoff_seconds=60,
        ),
        scheduled_repo=_ScheduledRepo([due_row]),
        draft_repo=_DraftRepo(drafts),
        settings_repo=_SettingsRepo(),
        publish_failure_repo=failure_repo,
    )

    await runner._process_due()

    assert due_row.status == ScheduledPostStatus.FAILED
    assert due_row.attempts == 3
    assert due_row.next_retry_at is None
    assert len(failure_repo.created) == 1


@pytest.mark.asyncio
async def test_scheduler_recovers_failed_without_retry_timestamp() -> None:
    now = datetime.now(timezone.utc)
    row = _ScheduledRow(
        draft_id=4,
        status=ScheduledPostStatus.FAILED,
        schedule_at=now - timedelta(hours=1),
        attempts=1,
        next_retry_at=None,
        updated_at=now - timedelta(minutes=20),
    )
    drafts = {4: _make_draft(4, DraftState.SCHEDULED)}
    workflow = _WorkflowSpy()
    failure_repo = _PublishFailureRepo()

    runner = SchedulerRunner(
        session_factory=_DummySessionFactory(_DummySession()),
        workflow=workflow,
        config=SchedulerConfig(
            poll_interval_seconds=10,
            batch_size=20,
            max_publish_attempts=3,
            recover_failed_after_seconds=60,
        ),
        scheduled_repo=_ScheduledRepo([row]),
        draft_repo=_DraftRepo(drafts),
        settings_repo=_SettingsRepo(),
        publish_failure_repo=failure_repo,
    )

    await runner._process_due()

    assert workflow.publish_calls == 1
    assert row.status == ScheduledPostStatus.PUBLISHED


@pytest.mark.asyncio
async def test_scheduler_cancels_due_row_if_draft_not_scheduled() -> None:
    due_row = _ScheduledRow(
        draft_id=5,
        status=ScheduledPostStatus.SCHEDULED,
        schedule_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    drafts = {5: _make_draft(5, DraftState.READY)}
    workflow = _WorkflowSpy()
    failure_repo = _PublishFailureRepo()

    runner = SchedulerRunner(
        session_factory=_DummySessionFactory(_DummySession()),
        workflow=workflow,
        config=SchedulerConfig(poll_interval_seconds=10, batch_size=20),
        scheduled_repo=_ScheduledRepo([due_row]),
        draft_repo=_DraftRepo(drafts),
        settings_repo=_SettingsRepo(),
        publish_failure_repo=failure_repo,
    )

    await runner._process_due()

    assert workflow.publish_calls == 0
    assert workflow.move_calls == 0
    assert due_row.status == ScheduledPostStatus.CANCELLED
