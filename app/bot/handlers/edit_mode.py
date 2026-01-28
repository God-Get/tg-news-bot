from __future__ import annotations

import time
from dataclasses import dataclass

from aiogram import Bot, Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos.drafts import DraftsRepo
from app.bot.utils.telegram_ops import update_card, delete_message_safe
from app.core.logging import log

router = Router()

# ===== Active edit sessions (in-memory MVP) =====
# key: (chat_id, thread_id) -> ActiveEdit
_EDIT: dict[tuple[int, int], "ActiveEdit"] = {}
_EDIT_TTL_SEC = 2 * 60 * 60  # 2 hours


@dataclass
class ActiveEdit:
    draft_id: int
    preview_message_id: int
    created_ts: float


def _set_active(chat_id: int, thread_id: int, draft_id: int, preview_message_id: int) -> None:
    _EDIT[(chat_id, thread_id)] = ActiveEdit(
        draft_id=int(draft_id),
        preview_message_id=int(preview_message_id),
        created_ts=time.time(),
    )


def _get_active(chat_id: int, thread_id: int) -> ActiveEdit | None:
    s = _EDIT.get((chat_id, thread_id))
    if not s:
        return None
    if (time.time() - s.created_ts) > _EDIT_TTL_SEC:
        _EDIT.pop((chat_id, thread_id), None)
        return None
    return s


def _clear_active(chat_id: int, thread_id: int) -> bool:
    return _EDIT.pop((chat_id, thread_id), None) is not None


def _clear_all_chat(chat_id: int) -> int:
    keys = [k for k in _EDIT.keys() if k[0] == chat_id]
    for k in keys:
        _EDIT.pop(k, None)
    return len(keys)


class CancelEditCB(CallbackData, prefix="cancel_edit"):
    thread_id: int


def cancel_edit_keyboard(thread_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=CancelEditCB(thread_id=thread_id).pack())]
        ]
    )


def _extract_text_and_photo(m: Message) -> tuple[str | None, tuple[str, str] | None]:
    """
    Returns:
      text: str | None
      photo: (file_id, unique_id) | None
    """
    photo = None
    if m.photo:
        ph = m.photo[-1]  # biggest
        photo = (ph.file_id, ph.file_unique_id)

    text = None
    if m.caption and m.caption.strip():
        text = m.caption.strip()
    elif m.text and m.text.strip():
        text = m.text.strip()

    return text, photo


async def _send_preview(bot: Bot, chat_id: int, thread_id: int, d) -> Message:
    """
    Creates a single "preview message" to be later edited in-place.
    """
    text = d.post_text_ru or ""

    if d.has_image and d.tg_image_file_id:
        return await bot.send_photo(
            chat_id=chat_id,
            message_thread_id=thread_id,
            photo=d.tg_image_file_id,
            caption=text,
            parse_mode=None,
        )

    return await bot.send_message(
        chat_id=chat_id,
        message_thread_id=thread_id,
        text=text,
        disable_web_page_preview=True,
        parse_mode=None,
    )


async def start_edit_session(bot: Bot, chat_id: int, thread_id: int, d) -> None:
    """
    Starts/overwrites active edit session for the thread.
    Posts preview + instruction with Cancel button.
    """
    preview = await _send_preview(bot, chat_id, thread_id, d)
    _set_active(chat_id, thread_id, int(d.id), int(preview.message_id))

    await bot.send_message(
        chat_id=chat_id,
        message_thread_id=thread_id,
        text=(
            "✏️ Режим редактирования\n\n"
            "Просто отправь новый пост сюда:\n"
            "• текст (до 1000 символов), или\n"
            "• фото + подпись.\n\n"
            "Отмена — кнопкой ниже."
        ),
        reply_markup=cancel_edit_keyboard(thread_id),
        parse_mode=None,
    )


@router.callback_query(CancelEditCB.filter())
async def on_cancel_edit_cb(cq: CallbackQuery, callback_data: CancelEditCB) -> None:
    if not cq.message or not cq.message.chat:
        await cq.answer()
        return
    chat_id = int(cq.message.chat.id)
    thread_id = int(callback_data.thread_id)

    ok = _clear_active(chat_id, thread_id)
    await cq.answer("Отменено." if ok else "Нечего отменять.", show_alert=False)


@router.message(Command("cancel"))
async def on_cancel_cmd(m: Message) -> None:
    if not m.chat:
        return
    dropped = _clear_all_chat(int(m.chat.id))
    if dropped:
        await m.reply("✅ Отмена: активные режимы редактирования сброшены.", parse_mode=None)
    else:
        await m.reply("ℹ️ Нечего отменять.", parse_mode=None)


@router.message()
async def on_any_message_in_editing(
    m: Message,
    bot: Bot,
    session: AsyncSession,
) -> None:
    """
    ВАЖНО:
    - НЕ reply
    - Любое обычное сообщение в треде с активной edit-сессией = новая версия поста.
    """
    if not m.chat:
        return
    if m.from_user and m.from_user.is_bot:
        return

    thread_id = m.message_thread_id
    if not thread_id:
        return

    chat_id = int(m.chat.id)
    thread_id = int(thread_id)

    active = _get_active(chat_id, thread_id)
    if not active:
        return

    text, photo = _extract_text_and_photo(m)

    # Если пришло фото без подписи, разрешаем (текст останется прежним)
    if text is None and photo is None:
        return  # не мешаем (стикеры/прочее)

    drafts = DraftsRepo(session)
    d = await drafts.get(active.draft_id)
    if not d:
        return

    update_payload: dict = {}

    # текст
    if text is not None:
        if len(text) > 1000:
            await m.reply("❌ Слишком длинно. Макс 1000 символов.", parse_mode=None)
            return
        update_payload["post_text_ru"] = text

    # фото
    if photo is not None:
        file_id, uniq_id = photo
        update_payload.update(
            {
                "tg_image_file_id": file_id,
                "tg_image_unique_id": uniq_id,
                "has_image": True,
                "image_status": "tg",
            }
        )

    if not update_payload:
        return

    d = await drafts.update(d.id, **update_payload)

    # Обновляем карточку
    await update_card(bot, d)

    # Обновляем preview-сообщение БОТА "на месте"
    try:
        if d.has_image and d.tg_image_file_id:
            # Если preview уже photo -> редактируем media/caption
            # Если preview был текстом и теперь появилась картинка -> пересоздадим preview
            try:
                await bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=active.preview_message_id,
                    media={
                        "type": "photo",
                        "media": d.tg_image_file_id,
                        "caption": d.post_text_ru or "",
                        "parse_mode": None,
                    },
                )
            except TelegramBadRequest:
                # fallback: удаляем старый preview и шлём новый photo
                await delete_message_safe(bot, chat_id, active.preview_message_id)
                new_preview = await bot.send_photo(
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    photo=d.tg_image_file_id,
                    caption=d.post_text_ru or "",
                    parse_mode=None,
                )
                _set_active(chat_id, thread_id, int(d.id), int(new_preview.message_id))
        else:
            # текстовый preview
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=active.preview_message_id,
                text=d.post_text_ru or "",
                disable_web_page_preview=True,
                parse_mode=None,
            )
    except TelegramBadRequest:
        pass

    # Чтобы тред был чистый: удаляем сообщение редактора (можно убрать, если не хочешь)
    await delete_message_safe(bot, chat_id, int(m.message_id))

    # Короткое подтверждение
    await bot.send_message(
        chat_id=chat_id,
        message_thread_id=thread_id,
        text="✅ Сохранено и превью обновлено.",
        parse_mode=None,
    )

    log.info("edit_saved", extra={"draft_id": int(d.id), "thread_id": thread_id})
