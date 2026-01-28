from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import DraftState
from app.core.logging import log
from app.db.repos.drafts import DraftsRepo

from app.bot.utils.callback_data import DraftCB, DraftAction
from app.bot.utils.card_renderer import render_card_text, build_card_keyboard, render_schedule_shortcuts
from app.bot.utils.telegram_ops import (
    move_card_to_topic,
    post_ru_draft,
    send_card_info,
    update_card,
    update_post,
)

from app.core.state_machine import transition


router = Router()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.callback_query(DraftCB.filter())
async def on_draft_cb(cq: CallbackQuery, callback_data: DraftCB, session: AsyncSession, bot: Bot) -> None:
    action = DraftAction(callback_data.a)
    draft_id = int(callback_data.id)

    drafts = DraftsRepo(session)
    d = await drafts.get(draft_id)
    if not d:
        await cq.answer("Черновик не найден", show_alert=True)
        return

    # Для клавиатуры нам нужен пост_message_id
    post_id = getattr(d, "post_message_id", None)

    # --- SOURCE / PREVIEW ---
    if action == DraftAction.SOURCE:
        url = getattr(d, "source_url", None) or getattr(d, "url", None) or "-"
        await cq.answer()
        # отвечаем в тот же чат/топик, где нажали кнопку
        await bot.send_message(
            chat_id=int(d.group_chat_id),
            message_thread_id=int(d.topic_id),
            text=f"Источник: {url}",
            parse_mode=None,
            disable_web_page_preview=False,
        )
        return

    if action == DraftAction.SCHEDULE_MENU:
        # Меняем клавиатуру на "меню планирования" (на ПОСТЕ)
        if not post_id:
            await cq.answer("У поста нет message_id (post_message_id).", show_alert=True)
            return
        await cq.answer()
        await update_post(
            bot=bot,
            group_chat_id=int(d.group_chat_id),
            post_message_id=int(post_id),
            text=getattr(d, "text_ru", "") or getattr(d, "text", "") or "",
            reply_markup=render_schedule_shortcuts(draft_id),
        )
        return

    if action in (DraftAction.SCHEDULE_PLUS_1H, DraftAction.SCHEDULE_TOMORROW_10):
        if not settings.scheduled_topic_id:
            await cq.answer("scheduled_topic_id не настроен", show_alert=True)
            return

        # ставим время
        if action == DraftAction.SCHEDULE_PLUS_1H:
            d.scheduled_at = _utcnow() + timedelta(hours=1)
        else:
            # завтра 10:00 UTC (можешь поменять на локаль/настройку)
            t = _utcnow().astimezone(timezone.utc)
            tomorrow = (t + timedelta(days=1)).date()
            d.scheduled_at = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 10, 0, tzinfo=timezone.utc)

        # переводим в SCHEDULED
        ns = await transition(session, d, DraftState.SCHEDULED)
        if ns is None:
            await cq.answer("Действие не доступно в текущей версии", show_alert=True)
            return

        # переносим пост+карточку
        new_topic = int(settings.scheduled_topic_id)
        card_text = render_card_text(d)
        d.card_text = card_text  # удобное поле (если есть), иначе игнор

        new_topic_id, new_card_id, new_post_id = await move_card_to_topic(bot, d, new_topic)
        d.topic_id = new_topic_id
        d.message_id = new_card_id
        if new_post_id:
            d.post_message_id = new_post_id

        # Перерисовываем кнопки на ПОСТЕ в новом топике
        kb = build_card_keyboard(d)
        await update_post(
            bot=bot,
            group_chat_id=int(d.group_chat_id),
            post_message_id=int(d.post_message_id),
            text=getattr(d, "text_ru", "") or getattr(d, "text", "") or "",
            reply_markup=kb,
        )

        await session.commit()
        await cq.answer("Запланировано ✅")
        return

    # --- Переходы состояний ---
    target_state = None
    if action == DraftAction.TO_EDITING:
        target_state = DraftState.EDITING
    elif action == DraftAction.TO_READY:
        target_state = DraftState.READY
    elif action == DraftAction.TO_ARCHIVE:
        target_state = DraftState.ARCHIVE
    elif action == DraftAction.BACK_TO_EDITING:
        target_state = DraftState.EDITING
    elif action == DraftAction.PUBLISH_NOW:
        target_state = DraftState.PUBLISHED

    if not target_state:
        await cq.answer("Действие не доступно в текущей версии", show_alert=True)
        return

    ns = await transition(session, d, target_state)
    if ns is None:
        await cq.answer("Действие не доступно в текущей версии", show_alert=True)
        return

    # Определяем нужный топик
    topic_map = {
        DraftState.INBOX: settings.inbox_topic_id,
        DraftState.EDITING: settings.service_topic_id,
        DraftState.READY: settings.ready_topic_id,
        DraftState.SCHEDULED: settings.scheduled_topic_id,
        DraftState.PUBLISHED: settings.published_topic_id,
        DraftState.ARCHIVE: settings.archive_topic_id,
    }
    new_topic = topic_map.get(ns)

    if not new_topic:
        await cq.answer("Нет topic_id для этого состояния", show_alert=True)
        return

    # переносим (пост+карточку) в новый топик
    card_text = render_card_text(d)
    d.card_text = card_text

    new_topic_id, new_card_id, new_post_id = await move_card_to_topic(bot, d, int(new_topic))
    d.topic_id = new_topic_id
    d.message_id = new_card_id
    if new_post_id:
        d.post_message_id = new_post_id

    # если не было поста — создаём пост заново (важно для старых черновиков)
    if not getattr(d, "post_message_id", None):
        kb = build_card_keyboard(d)
        msg = await post_ru_draft(bot, int(d.group_chat_id), int(d.topic_id), d, reply_markup=kb)
        d.post_message_id = msg.message_id

        # карточку рисуем НИЖЕ поста
        card_msg = await send_card_info(bot, int(d.group_chat_id), int(d.topic_id), card_text)
        d.message_id = card_msg.message_id

    # обновляем карточку и кнопки на посте
    kb = build_card_keyboard(d)
    await update_post(
        bot=bot,
        group_chat_id=int(d.group_chat_id),
        post_message_id=int(d.post_message_id),
        text=getattr(d, "text_ru", "") or getattr(d, "text", "") or "",
        reply_markup=kb,
    )
    await update_card(bot, int(d.group_chat_id), int(d.message_id), card_text)

    await session.commit()
    await cq.answer("Готово ✅")
