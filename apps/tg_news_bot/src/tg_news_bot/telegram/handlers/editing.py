"""Edit session handlers."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_news_bot.config import Settings
from telegram_publisher import TelegramPublisher
from telegram_publisher.exceptions import PublisherNotFound
from tg_news_bot.repositories.bot_settings import BotSettingsRepository
from tg_news_bot.services.edit_sessions import EditPayload, EditSessionService


@dataclass(slots=True)
class EditContext:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    edit_sessions: EditSessionService
    publisher: TelegramPublisher


def create_edit_router(context: EditContext) -> Router:
    router = Router()
    settings_repo = BotSettingsRepository()

    def is_admin(message: Message) -> bool:
        return bool(message.from_user and message.from_user.id == context.settings.admin_user_id)

    async def is_editing_topic(message: Message) -> bool:
        if not message.message_thread_id:
            return False
        async with context.session_factory() as session:
            async with session.begin():
                bot_settings = await settings_repo.get_or_create(session)

        if not bot_settings.group_chat_id or not bot_settings.editing_topic_id:
            return False
        return (
            message.chat.id == bot_settings.group_chat_id
            and message.message_thread_id == bot_settings.editing_topic_id
        )

    @router.message(Command("cancel"))
    async def cancel(message: Message) -> None:
        if not is_admin(message):
            return
        if not await is_editing_topic(message):
            return
        async with context.session_factory() as session:
            async with session.begin():
                await context.edit_sessions.cancel_active_for_topic(
                    session,
                    group_chat_id=message.chat.id,
                    topic_id=message.message_thread_id,
                )
        try:
            await context.publisher.delete_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
            )
        except PublisherNotFound:
            return

    @router.message()
    async def handle_edit_message(message: Message) -> None:
        if not is_admin(message):
            return
        if not await is_editing_topic(message):
            return
        if not message.message_thread_id:
            return

        text = None
        photo_file_id = None
        photo_unique_id = None

        if message.photo:
            largest = message.photo[-1]
            photo_file_id = largest.file_id
            photo_unique_id = largest.file_unique_id
            if message.caption:
                text = message.caption
        elif message.text:
            text = message.text

        if not text and not photo_file_id:
            return

        payload = EditPayload(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            user_id=message.from_user.id,
            message_id=message.message_id,
            text=text,
            photo_file_id=photo_file_id,
            photo_unique_id=photo_unique_id,
        )

        async with context.session_factory() as session:
            async with session.begin():
                await context.edit_sessions.apply_edit(session, payload)

    return router
