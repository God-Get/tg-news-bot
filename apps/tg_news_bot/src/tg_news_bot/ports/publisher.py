"""Publisher port for tg-news-bot."""

from __future__ import annotations

from typing import Protocol

from telegram_publisher.types import PostContent, SendResult


class PublisherError(Exception):
    """Base publisher error used by tg-news-bot."""


class PublisherEditNotAllowed(PublisherError):
    """Raised when Telegram does not allow edit/delete operation."""


class PublisherNotFound(PublisherError):
    """Raised when a target message is not found."""


class PublisherNotModified(PublisherError):
    """Raised when edit operation has no actual changes."""


class PublisherPort(Protocol):
    async def send_post(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        content: PostContent,
        keyboard,
    ) -> SendResult: ...

    async def send_text(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        text: str,
        keyboard,
        parse_mode: str | None = None,
    ) -> SendResult: ...

    async def edit_post(
        self,
        *,
        chat_id: int,
        message_id: int,
        content: PostContent,
        keyboard,
    ) -> SendResult: ...

    async def edit_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        keyboard,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = False,
    ) -> None: ...

    async def edit_caption(
        self,
        *,
        chat_id: int,
        message_id: int,
        caption: str,
        keyboard,
        parse_mode: str | None = None,
    ) -> None: ...

    async def edit_reply_markup(
        self,
        *,
        chat_id: int,
        message_id: int,
        keyboard,
    ) -> None: ...

    async def delete_message(self, *, chat_id: int, message_id: int) -> None: ...
