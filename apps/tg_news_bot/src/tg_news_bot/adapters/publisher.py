"""Publisher adapter bridging app port to telegram-publisher."""

from __future__ import annotations

from telegram_publisher import TelegramPublisher
from telegram_publisher.exceptions import (
    PublisherEditNotAllowed as UpstreamPublisherEditNotAllowed,
)
from telegram_publisher.exceptions import PublisherNotFound as UpstreamPublisherNotFound
from telegram_publisher.exceptions import (
    PublisherNotModified as UpstreamPublisherNotModified,
)
from telegram_publisher.types import PostContent, SendResult
from tg_news_bot.ports.publisher import (
    PublisherEditNotAllowed,
    PublisherNotFound,
    PublisherNotModified,
)


class PublisherAdapter:
    def __init__(self, publisher: TelegramPublisher) -> None:
        self._publisher = publisher

    async def send_post(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        content: PostContent,
        keyboard,
    ) -> SendResult:
        try:
            return await self._publisher.send_post(
                chat_id=chat_id,
                topic_id=topic_id,
                content=content,
                keyboard=keyboard,
            )
        except UpstreamPublisherNotFound as exc:
            raise PublisherNotFound(str(exc)) from exc
        except UpstreamPublisherEditNotAllowed as exc:
            raise PublisherEditNotAllowed(str(exc)) from exc
        except UpstreamPublisherNotModified as exc:
            raise PublisherNotModified(str(exc)) from exc

    async def send_text(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        text: str,
        keyboard=None,
        parse_mode: str | None = None,
    ) -> SendResult:
        try:
            return await self._publisher.send_text(
                chat_id=chat_id,
                topic_id=topic_id,
                text=text,
                keyboard=keyboard,
                parse_mode=parse_mode,
            )
        except UpstreamPublisherNotFound as exc:
            raise PublisherNotFound(str(exc)) from exc
        except UpstreamPublisherEditNotAllowed as exc:
            raise PublisherEditNotAllowed(str(exc)) from exc
        except UpstreamPublisherNotModified as exc:
            raise PublisherNotModified(str(exc)) from exc

    async def edit_post(
        self,
        *,
        chat_id: int,
        message_id: int,
        content: PostContent,
        keyboard,
    ) -> SendResult:
        try:
            return await self._publisher.edit_post(
                chat_id=chat_id,
                message_id=message_id,
                content=content,
                keyboard=keyboard,
            )
        except UpstreamPublisherNotFound as exc:
            raise PublisherNotFound(str(exc)) from exc
        except UpstreamPublisherEditNotAllowed as exc:
            raise PublisherEditNotAllowed(str(exc)) from exc
        except UpstreamPublisherNotModified as exc:
            raise PublisherNotModified(str(exc)) from exc

    async def edit_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        keyboard,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = False,
    ) -> None:
        try:
            await self._publisher.edit_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                keyboard=keyboard,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
        except UpstreamPublisherNotFound as exc:
            raise PublisherNotFound(str(exc)) from exc
        except UpstreamPublisherEditNotAllowed as exc:
            raise PublisherEditNotAllowed(str(exc)) from exc
        except UpstreamPublisherNotModified as exc:
            raise PublisherNotModified(str(exc)) from exc

    async def edit_caption(
        self,
        *,
        chat_id: int,
        message_id: int,
        caption: str,
        keyboard,
        parse_mode: str | None = None,
    ) -> None:
        try:
            await self._publisher.edit_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=caption,
                keyboard=keyboard,
                parse_mode=parse_mode,
            )
        except UpstreamPublisherNotFound as exc:
            raise PublisherNotFound(str(exc)) from exc
        except UpstreamPublisherEditNotAllowed as exc:
            raise PublisherEditNotAllowed(str(exc)) from exc
        except UpstreamPublisherNotModified as exc:
            raise PublisherNotModified(str(exc)) from exc

    async def edit_reply_markup(
        self,
        *,
        chat_id: int,
        message_id: int,
        keyboard,
    ) -> None:
        try:
            await self._publisher.edit_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                keyboard=keyboard,
            )
        except UpstreamPublisherNotFound as exc:
            raise PublisherNotFound(str(exc)) from exc
        except UpstreamPublisherEditNotAllowed as exc:
            raise PublisherEditNotAllowed(str(exc)) from exc
        except UpstreamPublisherNotModified as exc:
            raise PublisherNotModified(str(exc)) from exc

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        try:
            await self._publisher.delete_message(chat_id=chat_id, message_id=message_id)
        except UpstreamPublisherNotFound as exc:
            raise PublisherNotFound(str(exc)) from exc
        except UpstreamPublisherEditNotAllowed as exc:
            raise PublisherEditNotAllowed(str(exc)) from exc
        except UpstreamPublisherNotModified as exc:
            raise PublisherNotModified(str(exc)) from exc
