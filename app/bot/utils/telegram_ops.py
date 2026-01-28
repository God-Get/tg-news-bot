from __future__ import annotations

from typing import Optional

from aiogram import Bot
from aiogram.types import Message

from app.bot.utils.card_renderer import render_card_text, build_card_keyboard
from app.db.models.draft import Draft
from app.db.models.settings import BotSettings


async def safe_delete(bot: Bot, chat_id: int, message_id: Optional[int]) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=int(message_id))
    except Exception:
        # не падаем, если уже удалено/нет прав
        return


async def send_card(bot: Bot, chat_id: int, topic_id: int, d: Draft) -> Message:
    """
    Карточка Draft #... отдельным сообщением (без кнопок).
    """
    return await bot.send_message(
        chat_id=chat_id,
        message_thread_id=topic_id,
        text=render_card_text(d),
        parse_mode=None,
        disable_web_page_preview=True,
    )


async def post_ru_draft(bot: Bot, chat_id: int, topic_id: int, d: Draft, settings: BotSettings) -> Message:
    """
    Сам пост (текст/фото) — ИМЕННО под ним клавиатура.
    """
    text = (d.body_ru or "").strip()
    kb = build_card_keyboard(d, settings)

    # Если есть картинка — отправляем фото с caption
    photo = None
    if getattr(d, "tg_image_file_id", None):
        photo = d.tg_image_file_id
    elif getattr(d, "source_image_url", None):
        photo = d.source_image_url

    if photo:
        # Telegram caption ограничен; но для твоего ТЗ (<=900) ок
        return await bot.send_photo(
            chat_id=chat_id,
            message_thread_id=topic_id,
            photo=photo,
            caption=text or " ",
            reply_markup=kb,
            parse_mode=None,
        )

    return await bot.send_message(
        chat_id=chat_id,
        message_thread_id=topic_id,
        text=text or " ",
        reply_markup=kb,
        parse_mode=None,
        disable_web_page_preview=True,
    )


async def edit_post(bot: Bot, chat_id: int, topic_id: int, d: Draft, settings: BotSettings) -> None:
    """
    Обновить текст/кнопки под постом.
    Если пост был фото — правим caption, иначе text.
    """
    if not d.post_message_id:
        return

    kb = build_card_keyboard(d, settings)
    text = (d.body_ru or "").strip() or " "

    # Пытаемся обновить caption (если это photo message), если не получится — обновим text
    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=int(d.post_message_id),
            caption=text,
            reply_markup=kb,
            parse_mode=None,
        )
        return
    except Exception:
        pass

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=int(d.post_message_id),
            text=text,
            reply_markup=kb,
            parse_mode=None,
            disable_web_page_preview=True,
        )
    except Exception:
        # если не вышло (например, сообщение удалено) — игнор
        return


async def edit_card(bot: Bot, chat_id: int, d: Draft) -> None:
    """
    Обновить текст карточки.
    """
    if not d.message_id:
        return
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=int(d.message_id),
            text=render_card_text(d),
            parse_mode=None,
            disable_web_page_preview=True,
        )
    except Exception:
        return


async def move_card_to_topic(
    bot: Bot,
    settings: BotSettings,
    d: Draft,
    target_topic_id: int,
) -> tuple[int, int]:
    """
    Перемещение между топиками в Telegram делаем как:
    - удаляем старые сообщения (card + post)
    - создаём заново (post с кнопками + card отдельно)
    Возвращаем (new_card_message_id, new_post_message_id)
    """
    chat_id = int(settings.group_chat_id)

    # удалить старые сообщения (в том же чате)
    await safe_delete(bot, chat_id, d.message_id)
    await safe_delete(bot, chat_id, d.post_message_id)

    # создать заново в новом топике
    post_msg = await post_ru_draft(bot, chat_id, int(target_topic_id), d, settings)
    card_msg = await send_card(bot, chat_id, int(target_topic_id), d)

    return int(card_msg.message_id), int(post_msg.message_id)
