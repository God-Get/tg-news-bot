"""Settings commands."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import feedparser
from httpx import AsyncClient
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_news_bot.config import Settings
from tg_news_bot.db.models import BotSettings
from tg_news_bot.logging import get_logger
from tg_news_bot.ports.publisher import PublisherPort
from tg_news_bot.repositories.sources import SourceRepository
from tg_news_bot.services.ingestion import IngestionRunner, IngestionStats
from tg_news_bot.services.workflow import DraftWorkflowService
from tg_news_bot.repositories.bot_settings import BotSettingsRepository

log = get_logger(__name__)


@dataclass(slots=True)
class SettingsContext:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    repository: BotSettingsRepository
    source_repository: SourceRepository
    publisher: PublisherPort
    ingestion_runner: IngestionRunner | None = None
    workflow: DraftWorkflowService | None = None


def create_settings_router(context: SettingsContext) -> Router:
    router = Router()

    def is_admin(message: Message) -> bool:
        return bool(message.from_user and message.from_user.id == context.settings.admin_user_id)

    def parse_source_args(raw_args: str) -> tuple[str, str]:
        raw = raw_args.strip()
        if "|" in raw:
            url, name = raw.split("|", maxsplit=1)
            return url.strip(), name.strip()
        parts = raw.split(maxsplit=1)
        if len(parts) == 1:
            return parts[0].strip(), ""
        return parts[0].strip(), parts[1].strip()

    def valid_source_url(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    def parse_topics(raw: str) -> list[str]:
        values = [item.strip().lower() for item in raw.split(",")]
        unique: list[str] = []
        for value in values:
            if value and value not in unique:
                unique.append(value)
        return unique

    def parse_id_range(raw: str) -> tuple[int, int] | None:
        parts = raw.strip().split()
        if len(parts) != 2:
            return None
        try:
            first = int(parts[0])
            second = int(parts[1])
        except ValueError:
            return None
        if first <= 0 or second <= 0:
            return None
        return min(first, second), max(first, second)

    def render_ingestion_stats(stats: IngestionStats) -> str:
        lines = [
            "Готово.",
            f"Источников: {stats.sources_total}",
            f"Проверено entries: {stats.entries_total}",
            f"Создано драфтов: {stats.created}",
            f"Дубликаты: {stats.duplicates}",
            f"Пропущено (низкий score): {stats.skipped_low_score}",
            f"Пропущено (невалидные entry): {stats.skipped_invalid_entry}",
            f"Пропущено (нет HTML): {stats.skipped_no_html}",
            f"Пропущено (blocked): {stats.skipped_blocked}",
            f"Пропущено (rate limit): {stats.skipped_rate_limited}",
            f"Ошибки загрузки RSS: {stats.rss_fetch_errors}",
        ]
        return "\n".join(lines)

    async def validate_rss_url(url: str) -> tuple[bool, str]:
        try:
            async with AsyncClient(follow_redirects=True, timeout=20) as http:
                response = await http.get(url)
        except Exception:
            return False, "Не удалось загрузить URL источника (network error)."
        if response.status_code >= 400:
            return False, f"Источник вернул HTTP {response.status_code}."

        parsed = feedparser.parse(response.text)
        entries_count = len(parsed.entries)
        feed_title = parsed.feed.get("title")
        has_feed_meta = bool(feed_title or parsed.feed.get("link"))
        if entries_count == 0 and not has_feed_meta:
            return False, "URL не похож на RSS/Atom feed."
        return True, f"OK (entries: {entries_count})"

    async def update_settings(updater) -> BotSettings:
        async with context.session_factory() as session:
            async with session.begin():
                bot_settings = await context.repository.get_or_create(session)
                updater(bot_settings)
                await session.flush()
                return bot_settings

    @router.message(Command("set_group"))
    async def set_group(message: Message) -> None:
        if not is_admin(message):
            return
        if message.chat.type not in {"supergroup", "group"}:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Команду нужно вызывать в супергруппе.",
            )
            return

        def updater(bs: BotSettings) -> None:
            bs.group_chat_id = message.chat.id

        await update_settings(updater)
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Группа сохранена: {message.chat.id}",
        )

    @router.message(Command("set_inbox_topic"))
    async def set_inbox_topic(message: Message) -> None:
        if not is_admin(message):
            return
        topic_id = message.message_thread_id
        if not topic_id:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Команду нужно вызывать внутри топика.",
            )
            return
        def updater(bs: BotSettings) -> None:
            bs.group_chat_id = message.chat.id
            bs.inbox_topic_id = topic_id

        await update_settings(updater)
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"INBOX топик сохранён: {topic_id}",
        )

    @router.message(Command("set_service_topic"))
    async def set_service_topic(message: Message) -> None:
        if not is_admin(message):
            return
        topic_id = message.message_thread_id
        if not topic_id:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Команду нужно вызывать внутри топика.",
            )
            return
        def updater(bs: BotSettings) -> None:
            bs.group_chat_id = message.chat.id
            bs.editing_topic_id = topic_id

        await update_settings(updater)
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"EDITING топик сохранён: {topic_id}",
        )

    @router.message(Command("set_ready_topic"))
    async def set_ready_topic(message: Message) -> None:
        if not is_admin(message):
            return
        topic_id = message.message_thread_id
        if not topic_id:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Команду нужно вызывать внутри топика.",
            )
            return
        def updater(bs: BotSettings) -> None:
            bs.group_chat_id = message.chat.id
            bs.ready_topic_id = topic_id

        await update_settings(updater)
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"READY топик сохранён: {topic_id}",
        )

    @router.message(Command("set_scheduled_topic"))
    async def set_scheduled_topic(message: Message) -> None:
        if not is_admin(message):
            return
        topic_id = message.message_thread_id
        if not topic_id:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Команду нужно вызывать внутри топика.",
            )
            return
        def updater(bs: BotSettings) -> None:
            bs.group_chat_id = message.chat.id
            bs.scheduled_topic_id = topic_id

        await update_settings(updater)
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"SCHEDULED топик сохранён: {topic_id}",
        )

    @router.message(Command("set_published_topic"))
    async def set_published_topic(message: Message) -> None:
        if not is_admin(message):
            return
        topic_id = message.message_thread_id
        if not topic_id:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Команду нужно вызывать внутри топика.",
            )
            return
        def updater(bs: BotSettings) -> None:
            bs.group_chat_id = message.chat.id
            bs.published_topic_id = topic_id

        await update_settings(updater)
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"PUBLISHED топик сохранён: {topic_id}",
        )

    @router.message(Command("set_archive_topic"))
    async def set_archive_topic(message: Message) -> None:
        if not is_admin(message):
            return
        topic_id = message.message_thread_id
        if not topic_id:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Команду нужно вызывать внутри топика.",
            )
            return
        def updater(bs: BotSettings) -> None:
            bs.group_chat_id = message.chat.id
            bs.archive_topic_id = topic_id

        await update_settings(updater)
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"ARCHIVE топик сохранён: {topic_id}",
        )

    @router.message(Command("set_channel"))
    async def set_channel(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if not command.args:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Нужно указать channel_id, например: /set_channel -1001234567890",
            )
            return
        raw = command.args.strip()
        try:
            channel_id = int(raw)
        except ValueError:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="channel_id должен быть числом",
            )
            return
        def updater(bs: BotSettings) -> None:
            bs.channel_id = channel_id

        await update_settings(updater)
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Канал сохранён: {channel_id}",
        )

    @router.message(Command("status"))
    async def status(message: Message) -> None:
        if not is_admin(message):
            return
        sources_total = 0
        enabled_total = 0
        async with context.session_factory() as session:
            async with session.begin():
                bot_settings = await context.repository.get_or_create(session)
                sources = await context.source_repository.list_all(session)
                sources_total = len(sources)
                enabled_total = sum(1 for item in sources if item.enabled)
        lines = [
            "Текущие настройки:",
            f"group_chat_id: {bot_settings.group_chat_id}",
            f"inbox_topic_id: {bot_settings.inbox_topic_id}",
            f"editing_topic_id: {bot_settings.editing_topic_id}",
            f"ready_topic_id: {bot_settings.ready_topic_id}",
            f"scheduled_topic_id: {bot_settings.scheduled_topic_id}",
            f"published_topic_id: {bot_settings.published_topic_id}",
            f"archive_topic_id: {bot_settings.archive_topic_id}",
            f"channel_id: {bot_settings.channel_id}",
            f"sources_total: {sources_total}",
            f"sources_enabled: {enabled_total}",
        ]
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(lines),
        )

    @router.message(Command("add_source"))
    async def add_source(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if not command.args:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=(
                    "Формат: /add_source <rss_url> [имя]\n"
                    "или: /add_source <rss_url> | <имя>"
                ),
            )
            return
        source_url, source_name = parse_source_args(command.args)
        if not valid_source_url(source_url):
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Некорректный URL. Нужен http/https RSS URL.",
            )
            return
        ok, validation_message = await validate_rss_url(source_url)
        if not ok:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=f"Источник не прошёл проверку: {validation_message}",
            )
            return
        if not source_name:
            source_name = urlparse(source_url).netloc
        async with context.session_factory() as session:
            async with session.begin():
                existing = await context.source_repository.get_by_url(session, source_url)
                if existing:
                    existing.name = source_name or existing.name
                    existing.enabled = True
                    await session.flush()
                    source = existing
                    action = "обновлён"
                else:
                    source = await context.source_repository.create(
                        session,
                        name=source_name,
                        url=source_url,
                        enabled=True,
                    )
                    action = "добавлен"
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=(
                f"Источник {action}: #{source.id}\n"
                f"name: {source.name}\n"
                f"url: {source.url}\n"
                f"enabled: {source.enabled}\n"
                f"validation: {validation_message}"
            ),
        )

    @router.message(Command("list_sources"))
    async def list_sources(message: Message) -> None:
        if not is_admin(message):
            return
        async with context.session_factory() as session:
            async with session.begin():
                sources = await context.source_repository.list_all(session)
        if not sources:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Источники не настроены.",
            )
            return
        lines = [
            f"Источники: {len(sources)}",
        ]
        for item in sources:
            state = "ON" if item.enabled else "OFF"
            lines.append(f"#{item.id} [{state}] {item.name}")
            lines.append(item.url)
            topics = []
            if isinstance(item.tags, dict):
                raw_topics = item.tags.get("topics")
                if isinstance(raw_topics, list):
                    topics = [str(topic) for topic in raw_topics if str(topic).strip()]
            if topics:
                lines.append(f"topics: {', '.join(topics)}")
            if isinstance(item.tags, dict):
                ssl_flag = item.tags.get("allow_insecure_ssl")
                if isinstance(ssl_flag, bool):
                    lines.append(f"allow_insecure_ssl: {str(ssl_flag).lower()}")
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(lines),
        )

    @router.message(Command("set_source_topics"))
    async def set_source_topics(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if not command.args:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=(
                    "Формат: /set_source_topics <source_id> <topics>\n"
                    "Пример: /set_source_topics 3 ai,space,science"
                ),
            )
            return
        parts = command.args.strip().split(maxsplit=1)
        if len(parts) != 2:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Нужно указать source_id и список topics через запятую.",
            )
            return
        try:
            source_id = int(parts[0])
        except ValueError:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="source_id должен быть числом.",
            )
            return
        topics = parse_topics(parts[1])
        if not topics:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Список topics пустой.",
            )
            return

        async with context.session_factory() as session:
            async with session.begin():
                source = await context.source_repository.get_by_id(session, source_id)
                if not source:
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text=f"Источник #{source_id} не найден.",
                    )
                    return
                tags = source.tags if isinstance(source.tags, dict) else {}
                tags["topics"] = topics
                source.tags = tags
                await session.flush()
                source_name = source.name
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Для источника #{source_id} ({source_name}) сохранены topics: {', '.join(topics)}",
        )

    @router.message(Command("clear_source_topics"))
    async def clear_source_topics(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if not command.args:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /clear_source_topics <source_id>",
            )
            return
        try:
            source_id = int(command.args.strip())
        except ValueError:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="source_id должен быть числом.",
            )
            return
        async with context.session_factory() as session:
            async with session.begin():
                source = await context.source_repository.get_by_id(session, source_id)
                if not source:
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text=f"Источник #{source_id} не найден.",
                    )
                    return
                tags = source.tags if isinstance(source.tags, dict) else {}
                tags.pop("topics", None)
                source.tags = tags if tags else None
                await session.flush()
                source_name = source.name
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Для источника #{source_id} ({source_name}) topics очищены.",
        )

    @router.message(Command("set_source_ssl_insecure"))
    async def set_source_ssl_insecure(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if not command.args:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /set_source_ssl_insecure <source_id> <on|off>",
            )
            return
        parts = command.args.strip().split(maxsplit=1)
        if len(parts) != 2:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Нужно указать source_id и режим on/off.",
            )
            return
        try:
            source_id = int(parts[0])
        except ValueError:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="source_id должен быть числом.",
            )
            return
        mode = parts[1].strip().lower()
        if mode not in {"on", "off"}:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Режим должен быть on или off.",
            )
            return
        enabled = mode == "on"
        async with context.session_factory() as session:
            async with session.begin():
                source = await context.source_repository.get_by_id(session, source_id)
                if not source:
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text=f"Источник #{source_id} не найден.",
                    )
                    return
                tags = source.tags if isinstance(source.tags, dict) else {}
                tags["allow_insecure_ssl"] = enabled
                source.tags = tags
                await session.flush()
                source_name = source.name
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=(
                f"Для источника #{source_id} ({source_name}) "
                f"allow_insecure_ssl={str(enabled).lower()}"
            ),
        )

    @router.message(Command("enable_source"))
    async def enable_source(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if not command.args:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /enable_source <source_id>",
            )
            return
        try:
            source_id = int(command.args.strip())
        except ValueError:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="source_id должен быть числом.",
            )
            return
        async with context.session_factory() as session:
            async with session.begin():
                source = await context.source_repository.get_by_id(session, source_id)
                if not source:
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text=f"Источник #{source_id} не найден.",
                    )
                    return
                source.enabled = True
                await session.flush()
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Источник #{source_id} включён.",
        )

    @router.message(Command("disable_source"))
    async def disable_source(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if not command.args:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /disable_source <source_id>",
            )
            return
        try:
            source_id = int(command.args.strip())
        except ValueError:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="source_id должен быть числом.",
            )
            return
        async with context.session_factory() as session:
            async with session.begin():
                source = await context.source_repository.get_by_id(session, source_id)
                if not source:
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text=f"Источник #{source_id} не найден.",
                    )
                    return
                source.enabled = False
                await session.flush()
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Источник #{source_id} выключен.",
        )

    @router.message(Command("remove_source"))
    async def remove_source(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if not command.args:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /remove_source <source_id>",
            )
            return
        try:
            source_id = int(command.args.strip())
        except ValueError:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="source_id должен быть числом.",
            )
            return

        result_text = ""
        async with context.session_factory() as session:
            async with session.begin():
                source = await context.source_repository.get_by_id(session, source_id)
                if not source:
                    result_text = f"Источник #{source_id} не найден."
                else:
                    has_links = await context.source_repository.has_linked_data(
                        session, source_id=source_id
                    )
                    if has_links:
                        source.enabled = False
                        await session.flush()
                        result_text = (
                            f"Источник #{source_id} имеет связанные drafts/articles и не может "
                            "быть удалён. Источник выключен."
                        )
                    else:
                        name = source.name
                        await context.source_repository.delete(session, source)
                        result_text = f"Источник #{source_id} удалён: {name}"

        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=result_text,
        )

    @router.message(Command("ingest_source"))
    async def ingest_source(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if context.ingestion_runner is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Ingestion недоступен в текущей конфигурации.",
            )
            return
        if not command.args:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /ingest_source <source_id>",
            )
            return
        try:
            source_id = int(command.args.strip())
        except ValueError:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="source_id должен быть числом.",
            )
            return
        async with context.session_factory() as session:
            async with session.begin():
                source = await context.source_repository.get_by_id(session, source_id)
                if not source:
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text=f"Источник #{source_id} не найден.",
                    )
                    return
                if not source.enabled:
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text=f"Источник #{source_id} выключен. Включите /enable_source {source_id}",
                    )
                    return
                source_name = source.name
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Запускаю RSS ingestion для #{source_id} ({source_name})...",
        )
        try:
            stats = await context.ingestion_runner.run_once(source_ids={source_id})
        except Exception:
            log.exception("settings.ingest_source_failed", source_id=source_id)
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Ошибка запуска ingestion. Смотри логи контейнера.",
            )
            return
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=render_ingestion_stats(stats),
        )

    @router.message(Command("ingest_now"))
    async def ingest_now(message: Message) -> None:
        if not is_admin(message):
            return
        if context.ingestion_runner is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Ingestion недоступен в текущей конфигурации.",
            )
            return
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="Запускаю RSS ingestion...",
        )
        try:
            stats = await context.ingestion_runner.run_once()
        except Exception:
            log.exception("settings.ingest_now_failed")
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Ошибка запуска ingestion. Смотри логи контейнера.",
            )
            return
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=render_ingestion_stats(stats),
        )

    @router.message(Command("process_range"))
    async def process_range(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if context.workflow is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Workflow недоступен в текущей конфигурации.",
            )
            return
        if not command.args:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /process_range <from_id> <to_id>",
            )
            return
        parsed = parse_id_range(command.args)
        if not parsed:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Нужно указать два положительных числовых draft_id.",
            )
            return
        start_id, end_id = parsed
        total = end_id - start_id + 1
        max_batch = 200
        if total > max_batch:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=f"Слишком большой диапазон ({total}). Максимум: {max_batch}.",
            )
            return

        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Запускаю выжимку и перевод для Draft #{start_id}..#{end_id}",
        )

        processed = 0
        skipped_not_found = 0
        skipped_not_editing = 0
        skipped_no_source = 0
        failed: list[int] = []

        for draft_id in range(start_id, end_id + 1):
            try:
                await context.workflow.process_editing_text(draft_id=draft_id)
                processed += 1
            except LookupError:
                skipped_not_found += 1
            except ValueError as exc:
                message_text = str(exc).lower()
                if "editing" in message_text:
                    skipped_not_editing += 1
                elif "source text" in message_text:
                    skipped_no_source += 1
                else:
                    failed.append(draft_id)
            except Exception:
                log.exception("settings.process_range_failed", draft_id=draft_id)
                failed.append(draft_id)

        lines = [
            f"Готово. Диапазон: #{start_id}..#{end_id}",
            f"Обработано: {processed}",
            f"Пропущено (нет Draft): {skipped_not_found}",
            f"Пропущено (не в EDITING): {skipped_not_editing}",
            f"Пропущено (нет source text): {skipped_no_source}",
            f"Ошибки: {len(failed)}",
        ]
        if failed:
            failed_preview = ", ".join(str(item) for item in failed[:20])
            if len(failed) > 20:
                failed_preview = f"{failed_preview}, ..."
            lines.append(f"Draft с ошибками: {failed_preview}")

        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(lines),
        )

    return router
