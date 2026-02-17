"""Telegram API wrapper."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNotFound, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, InputMediaPhoto

from telegram_publisher.exceptions import (
    PublisherEditNotAllowed,
    PublisherNotFound,
    PublisherNotModified,
)
from telegram_publisher.types import PostContent, SendResult

log = logging.getLogger(__name__)


class TelegramPublisher:
    def __init__(
        self,
        bot: Bot,
        *,
        max_retry_after_attempts: int = 3,
        max_retry_after_delay_seconds: int = 60,
    ) -> None:
        self._bot = bot
        self._max_retry_after_attempts = max(1, max_retry_after_attempts)
        self._max_retry_after_delay_seconds = max(1, max_retry_after_delay_seconds)

    async def send_post(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        content: PostContent,
        keyboard: InlineKeyboardMarkup | None = None,
    ) -> SendResult:
        if content.photo:
            message = await self._call_with_retry(
                "send_photo",
                self._bot.send_photo,
                chat_id=chat_id,
                message_thread_id=topic_id,
                photo=content.photo,
                caption=content.text,
                parse_mode=content.parse_mode,
                reply_markup=keyboard,
            )
            photo_file_id = None
            photo_unique_id = None
            if message.photo:
                largest = message.photo[-1]
                photo_file_id = largest.file_id
                photo_unique_id = largest.file_unique_id
            return SendResult(
                chat_id=message.chat.id,
                message_id=message.message_id,
                    photo_file_id=photo_file_id,
                    photo_unique_id=photo_unique_id,
            )

        message = await self._call_with_retry(
            "send_message",
            self._bot.send_message,
            chat_id=chat_id,
            message_thread_id=topic_id,
            text=content.text,
            parse_mode=content.parse_mode,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return SendResult(chat_id=message.chat.id, message_id=message.message_id)

    async def send_text(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        text: str,
        keyboard: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
    ) -> SendResult:
        message = await self._call_with_retry(
            "send_message",
            self._bot.send_message,
            chat_id=chat_id,
            message_thread_id=topic_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=keyboard,
            disable_web_page_preview=False,
        )
        return SendResult(chat_id=message.chat.id, message_id=message.message_id)

    async def edit_post(
        self,
        *,
        chat_id: int,
        message_id: int,
        content: PostContent,
        keyboard: InlineKeyboardMarkup | None = None,
    ) -> SendResult:
        try:
            if content.photo:
                media = InputMediaPhoto(
                    media=content.photo,
                    caption=content.text,
                    parse_mode=content.parse_mode,
                )
                message = await self._call_with_retry(
                    "edit_message_media",
                    self._bot.edit_message_media,
                    chat_id=chat_id,
                    message_id=message_id,
                    media=media,
                    reply_markup=keyboard,
                )
                photo_file_id = None
                photo_unique_id = None
                if message.photo:
                    largest = message.photo[-1]
                    photo_file_id = largest.file_id
                    photo_unique_id = largest.file_unique_id
                return SendResult(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    photo_file_id=photo_file_id,
                    photo_unique_id=photo_unique_id,
                )

            message = await self._call_with_retry(
                "edit_message_text",
                self._bot.edit_message_text,
                chat_id=chat_id,
                message_id=message_id,
                text=content.text,
                parse_mode=content.parse_mode,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            return SendResult(chat_id=message.chat.id, message_id=message.message_id)
        except TelegramBadRequest as exc:
            self._raise_edit_error(exc)

    async def edit_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        keyboard: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = False,
    ) -> None:
        try:
            await self._call_with_retry(
                "edit_message_text",
                self._bot.edit_message_text,
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=keyboard,
                disable_web_page_preview=disable_web_page_preview,
            )
        except TelegramBadRequest as exc:
            self._raise_edit_error(exc)

    async def edit_caption(
        self,
        *,
        chat_id: int,
        message_id: int,
        caption: str,
        keyboard: InlineKeyboardMarkup | None = None,
        parse_mode: str | None = None,
    ) -> None:
        try:
            await self._call_with_retry(
                "edit_message_caption",
                self._bot.edit_message_caption,
                chat_id=chat_id,
                message_id=message_id,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=keyboard,
            )
        except TelegramBadRequest as exc:
            self._raise_edit_error(exc)

    async def edit_reply_markup(
        self,
        *,
        chat_id: int,
        message_id: int,
        keyboard: InlineKeyboardMarkup | None = None,
    ) -> None:
        try:
            await self._call_with_retry(
                "edit_message_reply_markup",
                self._bot.edit_message_reply_markup,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboard,
            )
        except TelegramBadRequest as exc:
            self._raise_edit_error(exc)

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        try:
            await self._call_with_retry(
                "delete_message",
                self._bot.delete_message,
                chat_id=chat_id,
                message_id=message_id,
            )
        except TelegramBadRequest as exc:
            self._raise_edit_error(exc)
        except TelegramNotFound as exc:
            raise PublisherNotFound(str(exc)) from exc

    async def move_post(
        self,
        *,
        chat_id: int,
        from_message_id: int,
        to_topic_id: int | None,
        content: PostContent,
        keyboard: InlineKeyboardMarkup | None = None,
    ) -> SendResult:
        result = await self.send_post(
            chat_id=chat_id, topic_id=to_topic_id, content=content, keyboard=keyboard
        )
        try:
            await self.delete_message(chat_id=chat_id, message_id=from_message_id)
        except PublisherNotFound:
            pass
        return result

    async def _call_with_retry(
        self,
        method_name: str,
        func: Callable[..., Awaitable],
        **kwargs,
    ):
        last_error: Exception | None = None
        for attempt in range(1, self._max_retry_after_attempts + 1):
            try:
                return await func(**kwargs)
            except TelegramRetryAfter as exc:
                last_error = exc
                retry_after = float(getattr(exc, "retry_after", 1))
                wait_seconds = min(
                    max(retry_after, 1.0), float(self._max_retry_after_delay_seconds)
                )
                log.warning(
                    "publisher.retry_after method=%s attempt=%s wait_seconds=%.2f",
                    method_name,
                    attempt,
                    wait_seconds,
                )
                if attempt >= self._max_retry_after_attempts:
                    break
                await asyncio.sleep(wait_seconds)
        if last_error:
            raise last_error
        raise RuntimeError("publisher retry loop failed unexpectedly")

    @staticmethod
    def _raise_edit_error(exc: TelegramBadRequest) -> None:
        message = str(exc).lower()
        if "message is not modified" in message:
            raise PublisherNotModified(str(exc)) from exc
        if "message can't be deleted" in message:
            raise PublisherEditNotAllowed(str(exc)) from exc
        if "message can't be edited" in message:
            raise PublisherEditNotAllowed(str(exc)) from exc
        if "message is not found" in message or "message to edit not found" in message:
            raise PublisherNotFound(str(exc)) from exc
        raise exc
