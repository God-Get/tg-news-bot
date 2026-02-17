from __future__ import annotations

from datetime import datetime, timezone

from tg_news_bot.services.schedule_input import ScheduleInputService


class _DummySessionFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None


class _DummyWorkflow:
    async def transition(self, request):  # noqa: ANN001
        return None


def _service(timezone_name: str = "UTC") -> ScheduleInputService:
    return ScheduleInputService(
        session_factory=_DummySessionFactory(),
        workflow=_DummyWorkflow(),
        timezone_name=timezone_name,
    )


def test_parse_schedule_input_accepts_two_formats() -> None:
    service = _service("UTC")
    now = datetime(2026, 2, 17, 9, 0, tzinfo=timezone.utc)

    dt1, err1 = service._parse_schedule_input("17.02.2026 10:30", now=now)
    dt2, err2 = service._parse_schedule_input("2026-02-17 10:30", now=now)

    assert err1 is None
    assert err2 is None
    assert dt1 == datetime(2026, 2, 17, 10, 30, tzinfo=timezone.utc)
    assert dt2 == datetime(2026, 2, 17, 10, 30, tzinfo=timezone.utc)


def test_parse_schedule_input_uses_timezone() -> None:
    service = _service("Europe/Moscow")
    now = datetime(2026, 2, 17, 9, 0, tzinfo=timezone.utc)

    dt, err = service._parse_schedule_input("17.02.2026 10:00", now=now)

    assert err is None
    assert dt == datetime(2026, 2, 17, 7, 0, tzinfo=timezone.utc)


def test_parse_schedule_input_rejects_invalid_format() -> None:
    service = _service("UTC")
    now = datetime(2026, 2, 17, 9, 0, tzinfo=timezone.utc)

    dt, err = service._parse_schedule_input("bad text", now=now)

    assert dt is None
    assert err == "Формат: ДД.ММ.ГГГГ ЧЧ:ММ или YYYY-MM-DD HH:MM"
