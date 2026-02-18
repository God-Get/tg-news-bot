from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from tg_news_bot.ports.publisher import PublisherEditNotAllowed
from tg_news_bot.db.models import BotSettings, Draft, DraftState
from tg_news_bot.services.workflow import DraftWorkflowService
from tg_news_bot.services.workflow_types import DraftAction, TransitionRequest


class _AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


class DummySession:
    def begin(self) -> _AsyncContext:
        return _AsyncContext()


class DummySessionFactory:
    def __init__(self, session: DummySession) -> None:
        self._session = session

    def __call__(self) -> _AsyncContext:
        return self

    async def __aenter__(self) -> DummySession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


class FakeDraftRepo:
    def __init__(self, draft: Draft) -> None:
        self._draft = draft

    async def get_for_update(self, session: DummySession, draft_id: int) -> Draft:  # noqa: ARG002
        if draft_id != self._draft.id:
            raise LookupError
        return self._draft


class FakeSettingsRepo:
    def __init__(self, settings: BotSettings) -> None:
        self._settings = settings

    async def get_or_create(self, session: DummySession) -> BotSettings:  # noqa: ARG002
        return self._settings


class FakePublisherDeleteDenied:
    async def delete_message(self, *, chat_id: int, message_id: int) -> None:  # noqa: ARG002
        raise PublisherEditNotAllowed("can't delete")


@dataclass
class FakeScheduleService:
    schedule_calls: list[tuple[int, datetime]]
    cancel_calls: int = 0
    mark_published_calls: int = 0

    async def schedule(self, session: DummySession, *, draft_id: int, schedule_at: datetime) -> None:  # noqa: ARG002
        self.schedule_calls.append((draft_id, schedule_at))

    async def cancel(self, session: DummySession, *, draft_id: int) -> None:  # noqa: ARG002, ARG002
        self.cancel_calls += 1

    async def mark_published(self, session: DummySession, *, draft_id: int) -> None:  # noqa: ARG002, ARG002
        self.mark_published_calls += 1


@dataclass
class FakeEditSessionService:
    start_calls: int = 0
    cancel_calls: int = 0

    async def start(self, session: DummySession, draft_id: int, user_id: int) -> None:  # noqa: ARG002, ARG002, ARG002
        self.start_calls += 1

    async def cancel(self, session: DummySession, draft_id: int) -> None:  # noqa: ARG002, ARG002
        self.cancel_calls += 1


@dataclass
class FakePublishFailureRepository:
    created: int = 0
    resolved: int = 0

    async def create(self, session: DummySession, **kwargs) -> None:  # noqa: ARG002, ANN003
        self.created += 1

    async def mark_resolved_for_draft(self, session: DummySession, *, draft_id: int) -> None:  # noqa: ARG002, ARG002
        self.resolved += 1


class SpyWorkflow(DraftWorkflowService):
    def __init__(self, *, draft: Draft) -> None:
        self.session = DummySession()
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
        self.schedule = FakeScheduleService(schedule_calls=[])
        self.edit_sessions = FakeEditSessionService()
        self.publish_failures = FakePublishFailureRepository()
        self.move_calls = 0
        self.publish_calls = 0
        self.refresh_scheduled_calls = 0
        super().__init__(
            session_factory=DummySessionFactory(self.session),
            publisher=object(),
            settings_repo=FakeSettingsRepo(self.settings),
            draft_repo=FakeDraftRepo(draft),
            schedule_service=self.schedule,
            edit_session_service=self.edit_sessions,
            publish_failure_repo=self.publish_failures,
        )

    async def _move_in_group(self, *, session, draft, settings, target_state) -> None:  # noqa: ANN001, D401
        self.move_calls += 1

    async def _publish_now(self, session, draft, settings) -> None:  # noqa: ANN001, D401
        self.publish_calls += 1

    async def _refresh_scheduled_messages(self, *, session, draft) -> None:  # noqa: ANN001, D401
        self.refresh_scheduled_calls += 1


def _make_draft(*, state: DraftState) -> Draft:
    return Draft(
        id=1,
        state=state,
        normalized_url="https://example.com/a",
        domain="example.com",
        title_en="title",
        post_text_ru="text",
    )


@pytest.mark.asyncio
async def test_to_editing_is_idempotent() -> None:
    draft = _make_draft(state=DraftState.INBOX)
    workflow = SpyWorkflow(draft=draft)
    request = TransitionRequest(draft_id=1, action=DraftAction.TO_EDITING, user_id=1)

    await workflow.transition(request)
    await workflow.transition(request)

    assert draft.state == DraftState.EDITING
    assert workflow.move_calls == 1
    assert workflow.edit_sessions.start_calls == 1


@pytest.mark.asyncio
async def test_publish_now_is_idempotent() -> None:
    draft = _make_draft(state=DraftState.READY)
    workflow = SpyWorkflow(draft=draft)
    request = TransitionRequest(draft_id=1, action=DraftAction.PUBLISH_NOW, user_id=1)

    await workflow.transition(request)
    await workflow.transition(request)

    assert draft.state == DraftState.PUBLISHED
    assert workflow.publish_calls == 1
    assert workflow.move_calls == 1
    assert workflow.publish_failures.resolved == 1


@pytest.mark.asyncio
async def test_reschedule_in_scheduled_state_updates_time_without_move() -> None:
    draft = _make_draft(state=DraftState.SCHEDULED)
    workflow = SpyWorkflow(draft=draft)
    schedule_at = datetime(2026, 2, 17, 10, 0, tzinfo=timezone.utc)
    request = TransitionRequest(
        draft_id=1,
        action=DraftAction.SCHEDULE,
        user_id=1,
        schedule_at=schedule_at,
    )

    await workflow.transition(request)

    assert draft.state == DraftState.SCHEDULED
    assert workflow.schedule.schedule_calls == [(1, schedule_at)]
    assert workflow.move_calls == 0
    assert workflow.refresh_scheduled_calls == 1


@pytest.mark.asyncio
async def test_safe_delete_ignores_delete_not_allowed() -> None:
    workflow = DraftWorkflowService(
        session_factory=DummySessionFactory(DummySession()),
        publisher=FakePublisherDeleteDenied(),
    )

    await workflow._safe_delete(chat_id=-1001, message_id=123)
