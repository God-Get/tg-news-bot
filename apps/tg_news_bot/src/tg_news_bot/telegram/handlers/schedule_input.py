"""Manual schedule input handlers."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import Router
from aiogram.types import Message

from tg_news_bot.config import Settings
from tg_news_bot.services.schedule_input import ScheduleInputService
from telegram_publisher import TelegramPublisher
from telegram_publisher.exceptions import PublisherNotFound


@dataclass(slots=True)
class ScheduleInputContext:
    settings: Settings
    schedule_input: ScheduleInputService
    publisher: TelegramPublisher


def create_schedule_input_router(context: ScheduleInputContext) -> Router:
    router = Router()

    def is_admin(message: Message) -> bool:
        return bool(message.from_user and message.from_user.id == context.settings.admin_user_id)

    @router.message()
    async def handle_schedule_input(message: Message) -> None:
        if not is_admin(message):
            return
        if not message.text:
            return
        if message.text.startswith("/"):
            return
        if not message.message_thread_id:
            return

        result = await context.schedule_input.process_message(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            user_id=message.from_user.id,
            text=message.text,
        )
        if result is None:
            return

        if result.accepted:
            try:
                await context.publisher.delete_message(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                )
            except PublisherNotFound:
                return
            return

        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=result.message,
            parse_mode=None,
            keyboard=None,
        )

    return router
