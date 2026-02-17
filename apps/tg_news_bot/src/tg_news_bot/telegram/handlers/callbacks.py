"""Callback handlers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_news_bot.config import Settings
from tg_news_bot.logging import get_logger
from tg_news_bot.services.edit_sessions import EditSessionService
from tg_news_bot.services.schedule_input import ScheduleInputService

from tg_news_bot.services.workflow import DraftWorkflowService
from tg_news_bot.services.workflow_types import DraftAction, TransitionRequest
from tg_news_bot.telegram.callbacks import parse_callback


@dataclass(slots=True)
class CallbackContext:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    workflow: DraftWorkflowService
    edit_sessions: EditSessionService
    schedule_input: ScheduleInputService


def create_callback_router(context: CallbackContext) -> Router:
    router = Router()
    log = get_logger(__name__)

    def is_admin(query: CallbackQuery) -> bool:
        return bool(query.from_user and query.from_user.id == context.settings.admin_user_id)

    async def safe_answer(query: CallbackQuery, text: str | None = None) -> None:
        try:
            await query.answer(text=text)
        except TelegramBadRequest:
            return

    @router.callback_query()
    async def handle_callback(query: CallbackQuery) -> None:
        if not is_admin(query):
            await safe_answer(query)
            return
        if not query.data:
            await safe_answer(query)
            return
        parsed = parse_callback(query.data)
        if not parsed:
            await safe_answer(query)
            return

        try:
            now_utc = datetime.now(timezone.utc)

            if parsed.action == "cancel_edit":
                async with context.session_factory() as session:
                    async with session.begin():
                        await context.edit_sessions.cancel(session, draft_id=parsed.draft_id)
                await safe_answer(query)
                return

            if parsed.action == "schedule_open":
                await context.workflow.show_schedule_menu(
                    draft_id=parsed.draft_id,
                    menu="presets",
                    now=now_utc,
                    timezone_name=context.settings.scheduler.timezone,
                )
                await safe_answer(query)
                return

            if parsed.action == "schedule_tz_info":
                await safe_answer(
                    query,
                    text=f"Таймзона расписания: {context.settings.scheduler.timezone}",
                )
                return

            if parsed.action == "schedule_list":
                await context.workflow.show_schedule_menu(
                    draft_id=parsed.draft_id,
                    menu="list",
                    now=now_utc,
                    timezone_name=context.settings.scheduler.timezone,
                )
                await safe_answer(query)
                return

            if parsed.action == "schedule_day_menu":
                await context.workflow.show_schedule_menu(
                    draft_id=parsed.draft_id,
                    menu="days",
                    now=now_utc,
                    timezone_name=context.settings.scheduler.timezone,
                )
                await safe_answer(query)
                return

            if parsed.action == "schedule_manual_open":
                if not query.message or not query.message.message_thread_id:
                    await safe_answer(query, text="Откройте меню в топике")
                    return
                await context.schedule_input.open_session(
                    draft_id=parsed.draft_id,
                    chat_id=query.message.chat.id,
                    topic_id=query.message.message_thread_id,
                    user_id=query.from_user.id,
                )
                await safe_answer(
                    query,
                    text=(
                        "Введите дату/время: ДД.ММ.ГГГГ ЧЧ:ММ "
                        f"(TZ {context.settings.scheduler.timezone})"
                    ),
                )
                return

            if parsed.action == "schedule_manual_cancel":
                await context.schedule_input.cancel_for_draft(draft_id=parsed.draft_id)
                await safe_answer(query, text="Ввод даты отменён")
                return

            if parsed.action == "schedule_back":
                await context.schedule_input.cancel_for_draft(draft_id=parsed.draft_id)
                await context.workflow.restore_state_keyboard(draft_id=parsed.draft_id)
                await safe_answer(query)
                return

            if parsed.action.startswith("schedule_day_"):
                raw = parsed.action.removeprefix("schedule_day_")
                try:
                    selected_day = datetime.strptime(raw, "%Y%m%d").date()
                except ValueError:
                    await safe_answer(query, text="Некорректная дата")
                    return
                await context.workflow.show_schedule_menu(
                    draft_id=parsed.draft_id,
                    menu="times",
                    now=now_utc,
                    timezone_name=context.settings.scheduler.timezone,
                    selected_day=selected_day,
                )
                await safe_answer(query)
                return

            if parsed.action.startswith("schedule_time_"):
                raw = parsed.action.removeprefix("schedule_time_")
                selected_day = None
                try:
                    day_raw, time_raw = raw.split("_", maxsplit=1)
                    selected_day = datetime.strptime(day_raw, "%Y%m%d").date()
                    if len(time_raw) != 4 or not time_raw.isdigit():
                        raise ValueError
                    hour = int(time_raw[:2])
                    minute = int(time_raw[2:])
                    if hour > 23 or minute > 59:
                        raise ValueError
                    tz = ZoneInfo(context.settings.scheduler.timezone)
                    local_dt = datetime(
                        selected_day.year,
                        selected_day.month,
                        selected_day.day,
                        hour,
                        minute,
                        tzinfo=tz,
                    )
                    schedule_at = local_dt.astimezone(timezone.utc)
                except ValueError:
                    await safe_answer(query, text="Некорректное время")
                    return

                if schedule_at <= now_utc:
                    if selected_day is not None:
                        await context.workflow.show_schedule_menu(
                            draft_id=parsed.draft_id,
                            menu="times",
                            now=now_utc,
                            timezone_name=context.settings.scheduler.timezone,
                            selected_day=selected_day,
                        )
                    await safe_answer(query, text="Время уже прошло")
                    return

                request = TransitionRequest(
                    draft_id=parsed.draft_id,
                    action=DraftAction.SCHEDULE,
                    user_id=query.from_user.id,
                    schedule_at=schedule_at,
                )
                await context.workflow.transition(request)
                await context.schedule_input.cancel_for_draft(draft_id=parsed.draft_id)
                await safe_answer(query)
                return

            if parsed.action.startswith("schedule_at_"):
                raw = parsed.action.removeprefix("schedule_at_")
                if raw.isdigit():
                    schedule_at = datetime.fromtimestamp(int(raw), tz=timezone.utc)
                    if schedule_at <= now_utc:
                        await safe_answer(query, text="Время уже прошло")
                        return
                    request = TransitionRequest(
                        draft_id=parsed.draft_id,
                        action=DraftAction.SCHEDULE,
                        user_id=query.from_user.id,
                        schedule_at=schedule_at,
                    )
                    await context.workflow.transition(request)
                    await context.schedule_input.cancel_for_draft(draft_id=parsed.draft_id)
                await safe_answer(query)
                return

            try:
                action = DraftAction(parsed.action)
            except ValueError:
                await safe_answer(query)
                return

            request = TransitionRequest(
                draft_id=parsed.draft_id,
                action=action,
                user_id=query.from_user.id,
            )
            await context.workflow.transition(request)
            await safe_answer(query)
        except LookupError:
            log.warning(
                "callback.draft_not_found",
                draft_id=parsed.draft_id,
                action=parsed.action,
            )
            await safe_answer(query, text="Draft не найден")
        except ValueError:
            log.warning(
                "callback.invalid_transition",
                draft_id=parsed.draft_id,
                action=parsed.action,
            )
            await safe_answer(query, text="Переход недоступен")
        except Exception:
            log.exception(
                "callback.unhandled_error",
                draft_id=parsed.draft_id,
                action=parsed.action,
            )
            await safe_answer(query, text="Ошибка операции")

    return router
