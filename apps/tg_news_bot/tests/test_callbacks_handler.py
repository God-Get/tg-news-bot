from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from tg_news_bot.telegram.handlers.callbacks import CallbackContext, create_callback_router


class _BeginContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


class _Session:
    def begin(self) -> _BeginContext:
        return _BeginContext()


class _SessionFactory:
    def __call__(self) -> _SessionFactory:
        return self

    async def __aenter__(self) -> _Session:
        return _Session()

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


@dataclass
class _WorkflowSpy:
    show_calls: list[dict] = field(default_factory=list)
    restore_calls: list[int] = field(default_factory=list)
    transition_calls: list[object] = field(default_factory=list)
    process_calls: list[int] = field(default_factory=list)
    transition_exc: Exception | None = None

    async def show_schedule_menu(
        self,
        *,
        draft_id: int,
        menu: str,
        now: datetime,
        timezone_name: str,
        selected_day: date | None = None,
    ) -> None:
        self.show_calls.append(
            {
                "draft_id": draft_id,
                "menu": menu,
                "now": now,
                "timezone_name": timezone_name,
                "selected_day": selected_day,
            }
        )

    async def restore_state_keyboard(self, *, draft_id: int) -> None:
        self.restore_calls.append(draft_id)

    async def transition(self, request) -> None:  # noqa: ANN001
        if self.transition_exc:
            raise self.transition_exc
        self.transition_calls.append(request)

    async def process_editing_text(self, *, draft_id: int) -> None:
        self.process_calls.append(draft_id)


@dataclass
class _EditSessionsSpy:
    cancel_calls: list[int] = field(default_factory=list)

    async def cancel(self, session, *, draft_id: int) -> None:  # noqa: ANN001
        self.cancel_calls.append(draft_id)


@dataclass
class _ScheduleInputSpy:
    open_calls: list[dict] = field(default_factory=list)
    cancel_calls: list[int] = field(default_factory=list)

    async def open_session(
        self,
        *,
        draft_id: int,
        chat_id: int,
        topic_id: int,
        user_id: int,
    ) -> None:
        self.open_calls.append(
            {
                "draft_id": draft_id,
                "chat_id": chat_id,
                "topic_id": topic_id,
                "user_id": user_id,
            }
        )

    async def cancel_for_draft(self, *, draft_id: int) -> None:
        self.cancel_calls.append(draft_id)


@dataclass
class _TrendDiscoverySpy:
    ingest_calls: list[int] = field(default_factory=list)
    reject_article_calls: list[int] = field(default_factory=list)
    add_source_calls: list[int] = field(default_factory=list)
    reject_source_calls: list[int] = field(default_factory=list)

    async def ingest_article_candidate(self, *, candidate_id: int, user_id: int):  # noqa: ANN001
        self.ingest_calls.append(candidate_id)
        return SimpleNamespace(message=f"ingested {candidate_id}")

    async def reject_article_candidate(self, *, candidate_id: int, user_id: int):  # noqa: ANN001
        self.reject_article_calls.append(candidate_id)
        return SimpleNamespace(message=f"rejected article {candidate_id}")

    async def add_source_candidate(self, *, candidate_id: int, user_id: int):  # noqa: ANN001
        self.add_source_calls.append(candidate_id)
        return SimpleNamespace(message=f"added source {candidate_id}")

    async def reject_source_candidate(self, *, candidate_id: int, user_id: int):  # noqa: ANN001
        self.reject_source_calls.append(candidate_id)
        return SimpleNamespace(message=f"rejected source {candidate_id}")


@dataclass
class _Query:
    data: str | None
    user_id: int = 10
    answers: list[str | None] = field(default_factory=list)
    chat_id: int = -1001
    topic_id: int | None = 13

    @property
    def from_user(self):
        return SimpleNamespace(id=self.user_id)

    @property
    def message(self):
        if self.topic_id is None:
            return None
        return SimpleNamespace(
            chat=SimpleNamespace(id=self.chat_id),
            message_thread_id=self.topic_id,
        )

    async def answer(self, text: str | None = None) -> None:
        self.answers.append(text)


def _make_context(
    workflow: _WorkflowSpy,
    edit_sessions: _EditSessionsSpy,
    schedule_input: _ScheduleInputSpy,
    *,
    timezone_name: str = "UTC",
    trend_discovery: _TrendDiscoverySpy | None = None,
) -> CallbackContext:
    settings = SimpleNamespace(
        admin_user_id=10,
        scheduler=SimpleNamespace(timezone=timezone_name),
    )
    return CallbackContext(
        settings=settings,
        session_factory=_SessionFactory(),
        workflow=workflow,
        edit_sessions=edit_sessions,
        schedule_input=schedule_input,
        trend_discovery=trend_discovery,
    )


def _get_handler(context: CallbackContext):
    router = create_callback_router(context)
    return router.callback_query.handlers[0].callback


@pytest.mark.asyncio
async def test_schedule_open_callback_calls_menu() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(_make_context(workflow, edit_sessions, schedule_input))
    query = _Query(data="draft:7:schedule_open")

    await handler(query)

    assert len(workflow.show_calls) == 1
    assert workflow.show_calls[0]["draft_id"] == 7
    assert workflow.show_calls[0]["menu"] == "presets"
    assert workflow.show_calls[0]["timezone_name"] == "UTC"
    assert workflow.show_calls[0]["selected_day"] is None
    assert workflow.show_calls[0]["now"].tzinfo is not None
    assert query.answers == [None]


@pytest.mark.asyncio
async def test_schedule_list_and_back_callbacks() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(_make_context(workflow, edit_sessions, schedule_input))

    await handler(_Query(data="draft:7:schedule_list"))
    await handler(_Query(data="draft:7:schedule_back"))

    assert len(workflow.show_calls) == 1
    assert workflow.show_calls[0]["menu"] == "list"
    assert workflow.restore_calls == [7]
    assert schedule_input.cancel_calls == [7]


@pytest.mark.asyncio
async def test_schedule_day_menu_and_day_callback() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(_make_context(workflow, edit_sessions, schedule_input))

    await handler(_Query(data="draft:7:schedule_day_menu"))
    await handler(_Query(data="draft:7:schedule_day_20260217"))

    assert [call["menu"] for call in workflow.show_calls] == ["days", "times"]
    assert workflow.show_calls[1]["selected_day"] == date(2026, 2, 17)


@pytest.mark.asyncio
async def test_schedule_manual_open_starts_session() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(_make_context(workflow, edit_sessions, schedule_input))
    query = _Query(data="draft:9:schedule_manual_open", chat_id=-100777, topic_id=222)

    await handler(query)

    assert schedule_input.open_calls == [
        {
            "draft_id": 9,
            "chat_id": -100777,
            "topic_id": 222,
            "user_id": 10,
        }
    ]
    assert "Введите дату/время" in (query.answers[0] or "")


@pytest.mark.asyncio
async def test_schedule_tz_info_callback() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(
        _make_context(workflow, edit_sessions, schedule_input, timezone_name="Europe/Moscow")
    )
    query = _Query(data="draft:1:schedule_tz_info")

    await handler(query)

    assert query.answers == ["Таймзона расписания: Europe/Moscow"]


@pytest.mark.asyncio
async def test_schedule_manual_cancel_callback() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(_make_context(workflow, edit_sessions, schedule_input))
    query = _Query(data="draft:3:schedule_manual_cancel")

    await handler(query)

    assert schedule_input.cancel_calls == [3]
    assert query.answers == ["Ввод даты отменён"]


@pytest.mark.asyncio
async def test_schedule_at_callback_creates_transition_request() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(_make_context(workflow, edit_sessions, schedule_input))
    ts = int(datetime(2099, 1, 1, 10, 0, tzinfo=timezone.utc).timestamp())
    query = _Query(data=f"draft:7:schedule_at_{ts}")

    await handler(query)

    assert len(workflow.transition_calls) == 1
    request = workflow.transition_calls[0]
    assert request.draft_id == 7
    assert request.user_id == 10
    assert request.action.value == "schedule"
    assert request.schedule_at == datetime(2099, 1, 1, 10, 0, tzinfo=timezone.utc)
    assert schedule_input.cancel_calls == [7]
    assert query.answers == [None]


@pytest.mark.asyncio
async def test_schedule_time_callback_converts_local_time_to_utc() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(
        _make_context(
            workflow,
            edit_sessions,
            schedule_input,
            timezone_name="Europe/Moscow",
        )
    )
    query = _Query(data="draft:7:schedule_time_20990101_1000")

    await handler(query)

    assert len(workflow.transition_calls) == 1
    request = workflow.transition_calls[0]
    assert request.action.value == "schedule"
    assert request.schedule_at == datetime(2099, 1, 1, 7, 0, tzinfo=timezone.utc)
    assert schedule_input.cancel_calls == [7]
    assert query.answers == [None]


@pytest.mark.asyncio
async def test_schedule_time_callback_rejects_invalid_or_past() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(_make_context(workflow, edit_sessions, schedule_input))

    invalid_query = _Query(data="draft:7:schedule_time_20260217_2500")
    await handler(invalid_query)

    past_query = _Query(data="draft:7:schedule_time_20000101_1000")
    await handler(past_query)

    assert workflow.transition_calls == []
    assert len(workflow.show_calls) == 1
    assert workflow.show_calls[0]["menu"] == "times"
    assert workflow.show_calls[0]["selected_day"] == date(2000, 1, 1)
    assert invalid_query.answers == ["Некорректное время"]
    assert past_query.answers == ["Время уже прошло"]


@pytest.mark.asyncio
async def test_schedule_at_callback_rejects_past_time() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(_make_context(workflow, edit_sessions, schedule_input))
    ts = int(datetime(2000, 1, 1, 10, 0, tzinfo=timezone.utc).timestamp())
    query = _Query(data=f"draft:7:schedule_at_{ts}")

    await handler(query)

    assert workflow.transition_calls == []
    assert schedule_input.cancel_calls == []
    assert query.answers == ["Время уже прошло"]


@pytest.mark.asyncio
async def test_cancel_edit_callback_uses_edit_session_service() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(_make_context(workflow, edit_sessions, schedule_input))
    query = _Query(data="draft:22:cancel_edit")

    await handler(query)

    assert edit_sessions.cancel_calls == [22]
    assert query.answers == [None]


@pytest.mark.asyncio
async def test_process_now_callback_triggers_processing() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(_make_context(workflow, edit_sessions, schedule_input))
    query = _Query(data="draft:22:process_now")

    await handler(query)

    assert workflow.process_calls == [22]
    assert query.answers == ["Выжимка и перевод обновлены"]


@pytest.mark.asyncio
async def test_non_admin_callback_is_ignored_but_answered() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(_make_context(workflow, edit_sessions, schedule_input))
    query = _Query(data="draft:1:publish_now", user_id=999)

    await handler(query)

    assert workflow.transition_calls == []
    assert query.answers == [None]


@pytest.mark.asyncio
async def test_callback_transition_errors_are_reported() -> None:
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()

    workflow_lookup = _WorkflowSpy(transition_exc=LookupError("not found"))
    handler_lookup = _get_handler(
        _make_context(workflow_lookup, edit_sessions, schedule_input)
    )
    query_lookup = _Query(data="draft:1:publish_now")
    await handler_lookup(query_lookup)
    assert query_lookup.answers == ["Draft не найден"]

    workflow_value = _WorkflowSpy(transition_exc=ValueError("bad transition"))
    handler_value = _get_handler(
        _make_context(workflow_value, edit_sessions, schedule_input)
    )
    query_value = _Query(data="draft:1:publish_now")
    await handler_value(query_value)
    assert query_value.answers == ["Переход недоступен"]


@pytest.mark.asyncio
async def test_callback_content_safety_error_is_reported() -> None:
    workflow = _WorkflowSpy(transition_exc=ValueError("content_safety_failed:ad:sponsored"))
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    handler = _get_handler(_make_context(workflow, edit_sessions, schedule_input))
    query = _Query(data="draft:1:to_ready")

    await handler(query)

    assert query.answers == ["Контент не прошёл safety: ad:sponsored"]


@pytest.mark.asyncio
async def test_trend_article_callbacks_dispatch_to_trend_service() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    trend_discovery = _TrendDiscoverySpy()
    handler = _get_handler(
        _make_context(
            workflow,
            edit_sessions,
            schedule_input,
            trend_discovery=trend_discovery,
        )
    )

    ingest_query = _Query(data="trend:article:15:ingest")
    reject_query = _Query(data="trend:article:16:reject")
    await handler(ingest_query)
    await handler(reject_query)

    assert trend_discovery.ingest_calls == [15]
    assert trend_discovery.reject_article_calls == [16]
    assert ingest_query.answers == ["ingested 15"]
    assert reject_query.answers == ["rejected article 16"]


@pytest.mark.asyncio
async def test_trend_source_callbacks_dispatch_to_trend_service() -> None:
    workflow = _WorkflowSpy()
    edit_sessions = _EditSessionsSpy()
    schedule_input = _ScheduleInputSpy()
    trend_discovery = _TrendDiscoverySpy()
    handler = _get_handler(
        _make_context(
            workflow,
            edit_sessions,
            schedule_input,
            trend_discovery=trend_discovery,
        )
    )

    add_query = _Query(data="trend:source:77:add")
    reject_query = _Query(data="trend:source:78:reject")
    await handler(add_query)
    await handler(reject_query)

    assert trend_discovery.add_source_calls == [77]
    assert trend_discovery.reject_source_calls == [78]
    assert add_query.answers == ["added source 77"]
    assert reject_query.answers == ["rejected source 78"]
