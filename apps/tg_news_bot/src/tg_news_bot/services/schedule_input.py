"""Manual schedule input service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_news_bot.repositories.schedule_input_sessions import ScheduleInputSessionRepository
from tg_news_bot.services.workflow import DraftWorkflowService
from tg_news_bot.services.workflow_types import DraftAction, TransitionRequest


@dataclass(slots=True)
class ScheduleInputResult:
    accepted: bool
    message: str


class ScheduleInputService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        workflow: DraftWorkflowService,
        timezone_name: str,
        ttl_minutes: int = 10,
        repository: ScheduleInputSessionRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._workflow = workflow
        self._timezone_name = timezone_name
        self._ttl_minutes = ttl_minutes
        self._repo = repository or ScheduleInputSessionRepository()

    async def open_session(
        self,
        *,
        draft_id: int,
        chat_id: int,
        topic_id: int,
        user_id: int,
    ) -> None:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self._ttl_minutes)
        async with self._session_factory() as session:
            async with session.begin():
                await self._repo.upsert_active(
                    session,
                    draft_id=draft_id,
                    group_chat_id=chat_id,
                    topic_id=topic_id,
                    user_id=user_id,
                    expires_at=expires_at,
                )

    async def cancel_for_draft(self, *, draft_id: int) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await self._repo.cancel_by_draft(session, draft_id=draft_id)

    async def process_message(
        self,
        *,
        chat_id: int,
        topic_id: int,
        user_id: int,
        text: str,
    ) -> ScheduleInputResult | None:
        now = datetime.now(timezone.utc)

        async with self._session_factory() as session:
            async with session.begin():
                active = await self._repo.get_active_for_topic_user(
                    session,
                    group_chat_id=chat_id,
                    topic_id=topic_id,
                    user_id=user_id,
                    now=now,
                )
        if not active:
            return None

        schedule_at, parse_error = self._parse_schedule_input(text, now=now)
        if parse_error:
            return ScheduleInputResult(accepted=False, message=parse_error)
        if schedule_at is None:
            return ScheduleInputResult(accepted=False, message="Некорректная дата")

        if schedule_at <= now:
            return ScheduleInputResult(accepted=False, message="Время уже прошло")

        request = TransitionRequest(
            draft_id=active.draft_id,
            action=DraftAction.SCHEDULE,
            user_id=user_id,
            schedule_at=schedule_at,
        )
        await self._workflow.transition(request)

        async with self._session_factory() as session:
            async with session.begin():
                await self._repo.complete(session, session_id=active.id)

        local_schedule = schedule_at.astimezone(ZoneInfo(self._timezone_name))
        return ScheduleInputResult(
            accepted=True,
            message=f"Запланировано на {local_schedule:%d.%m.%Y %H:%M} ({self._timezone_name})",
        )

    def _parse_schedule_input(
        self,
        text: str,
        *,
        now: datetime,
    ) -> tuple[datetime | None, str | None]:
        raw = text.strip()
        if not raw:
            return None, "Отправьте дату и время"

        tz = ZoneInfo(self._timezone_name)
        formats = [
            "%d.%m.%Y %H:%M",
            "%Y-%m-%d %H:%M",
            "%d.%m %H:%M",
        ]

        parsed_local: datetime | None = None
        matched_format: str | None = None
        for fmt in formats:
            try:
                parsed = datetime.strptime(raw, fmt)
                if fmt == "%d.%m %H:%M":
                    local_now = now.astimezone(tz)
                    parsed = parsed.replace(year=local_now.year)
                parsed_local = parsed.replace(tzinfo=tz)
                matched_format = fmt
                break
            except ValueError:
                continue

        if not parsed_local:
            return None, "Формат: ДД.ММ.ГГГГ ЧЧ:ММ или YYYY-MM-DD HH:MM"

        if parsed_local < now.astimezone(tz) and matched_format == "%d.%m %H:%M":
            parsed_local = parsed_local.replace(year=parsed_local.year + 1)

        return parsed_local.astimezone(timezone.utc), None
