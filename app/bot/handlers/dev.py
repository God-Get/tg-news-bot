from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import DraftState
from app.db.repos.drafts import DraftsRepo
from app.db.repos.settings import SettingsRepo
from app.bot.utils.telegram_ops import post_ru_draft, send_card

router = Router()
TZ = ZoneInfo("Asia/Tbilisi")


@router.message(F.text.regexp(r"^/seed_demo(@\\w+)?(\\s+.*)?$"))
async def seed_demo_entry(m: Message, session: AsyncSession, bot: Bot) -> None:
    # DEBUG: чтобы всегда было видно, что команда попала именно в dev.py
    

    settings = await SettingsRepo(session).get_singleton()
    if not settings.group_chat_id or not settings.inbox_topic_id:
        await m.reply("❌ Сначала настроь /set_group и /set_inbox_topic (внутри топика).", parse_mode=None)
        return

    ru = (
        "Новый чип ускоряет ИИ на краю сети\n"
        "Исследователи показали энергоэффективный ускоритель для локальных моделей.\n"
        "• быстрее инференс на малых устройствах\n"
        "• меньше энергопотребление\n"
        "• подходит для роботов и IoT\n"
        "#ai #science"
    )

    drafts = DraftsRepo(session)

    d = await drafts.create(
        url="https://example.com/demo-article",
        normalized_url=f"https://example.com/demo-article?ts={int(datetime.now(TZ).timestamp())}",
        domain="example.com",
        title_en="Demo: New chip accelerates edge AI",
        extracted_text=None,
        extracted_text_expires_at=None,
        text_hash=None,
        score=0.42,
        reasons={"demo": True},
        post_text_ru=ru,
        topics=[],
        state=DraftState.INBOX.value,
        group_chat_id=int(settings.group_chat_id),
        topic_id=int(settings.inbox_topic_id),
        message_id=None,
        scheduled_at=None,
        published_at=None,
        published_message_id=None,
        has_image=False,
        image_status="NONE",
        source_image_url=None,
        tg_image_file_id=None,
        tg_image_unique_id=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    # RU draft (пост) в INBOX
    await post_ru_draft(bot, int(settings.group_chat_id), int(settings.inbox_topic_id), d)

    # Карточка модерации в INBOX
    msg = await send_card(bot, int(settings.group_chat_id), int(settings.inbox_topic_id), d)
    await drafts.update(d.id, message_id=msg.message_id)

    await m.reply(f"✅ seed_demo создан: Draft #{d.id} в INBOX.", parse_mode=None)
