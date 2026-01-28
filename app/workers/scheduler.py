from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.core.constants import DraftState
from app.db.session import SessionFactory
from app.db.repos.drafts import DraftsRepo
from app.db.repos.settings import SettingsRepo
from app.bot.utils.telegram_ops import move_card_to_topic, post_ru_draft


POLL_SECONDS = 20


async def _publish_due_one(bot: Bot, session: AsyncSession) -> int:
    """
    Публикует 1 due-draft (если есть).
    Возвращает количество опубликованных (0/1).
    """
    settings = await SettingsRepo(session).get_singleton()
    if not settings or not settings.group_chat_id or not settings.published_topic_id:
        return 0

    if not settings.channel_chat_id:
        # канал не настроен — не публикуем, просто выходим
        return 0

    drafts = DraftsRepo(session)
    now = datetime.now(timezone.utc)

    # ВАЖНО: repo метод может отличаться. Если у тебя нет такого метода,
    # скажи — подстрою под твой DraftsRepo.
    d = await drafts.get_next_due_scheduled(now)
    if not d:
        return 0

    # Публикуем в канал
    channel_id = int(settings.channel_chat_id)
    text = d.post_text_ru or ""

    if getattr(d, "has_image", False) and getattr(d, "tg_image_file_id", None):
        sent = await bot.send_photo(
            chat_id=channel_id,
            photo=d.tg_image_file_id,
            caption=text,
            parse_mode=None,
        )
    else:
        sent = await bot.send_message(
            chat_id=channel_id,
            text=text,
            parse_mode=None,
            disable_web_page_preview=True,
        )

    # Обновляем draft
    d = await drafts.update(
        d.id,
        state=DraftState.PUBLISHED.value,
        published_at=now,
        published_message_id=sent.message_id,
        scheduled_at=None,
    )

    # Переносим карточку в "Опубликованные" + кидаем RU draft в топик
    new_topic = int(settings.published_topic_id)
    topic_id, message_id = await move_card_to_topic(bot, d, new_topic)
    await drafts.update(d.id, topic_id=topic_id, message_id=message_id)

    await post_ru_draft(bot, int(settings.group_chat_id), new_topic, d)

    return 1


async def run_scheduler(bot: Bot) -> None:
    """
    Фоновый цикл. Никогда не должен падать.
    """
    log.info("scheduler_started")

    while True:
        try:
            published = 0
            async with SessionFactory() as session:
                # публикуем все due за этот тик (защита от очереди)
                while True:
                    n = await _publish_due_one(bot, session)
                    published += n
                    if n == 0:
                        break

            if published:
                log.info("scheduler_published", count=published)

        except asyncio.CancelledError:
            log.info("scheduler_stopped")
            raise
        except Exception as e:
            # не валим бота — только логируем и ждём следующий тик
            log.exception("scheduler_error", err=str(e))

        await asyncio.sleep(POLL_SECONDS)
