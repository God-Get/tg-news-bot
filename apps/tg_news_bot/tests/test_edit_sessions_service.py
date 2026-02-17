from __future__ import annotations

import pytest

from telegram_publisher.exceptions import PublisherEditNotAllowed
from tg_news_bot.services.edit_sessions import EditSessionService


class _DeleteDeniedPublisher:
    async def delete_message(self, *, chat_id: int, message_id: int) -> None:  # noqa: ARG002
        raise PublisherEditNotAllowed("message can't be deleted")


@pytest.mark.asyncio
async def test_safe_delete_ignores_delete_not_allowed() -> None:
    service = EditSessionService(_DeleteDeniedPublisher())

    await service._safe_delete(chat_id=-1001, message_id=42)
