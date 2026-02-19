"""Settings commands."""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse

import feedparser
from httpx import AsyncClient
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_news_bot.config import Settings
from tg_news_bot.db.models import BotSettings, DraftState, ScheduledPostStatus
from tg_news_bot.logging import get_logger
from tg_news_bot.ports.publisher import PublisherPort
from tg_news_bot.repositories.drafts import DraftRepository
from tg_news_bot.repositories.scheduled_posts import ScheduledPostRepository
from tg_news_bot.repositories.sources import SourceRepository
from tg_news_bot.repositories.trend_topic_profiles import (
    TrendTopicProfileInput,
    TrendTopicProfileRepository,
)
from tg_news_bot.services.analytics import AnalyticsService
from tg_news_bot.services.ingestion import IngestionRunner, IngestionStats
from tg_news_bot.services.trend_discovery import TrendDiscoveryService
from tg_news_bot.services.trends import TrendCollector
from tg_news_bot.services.workflow import DraftWorkflowService
from tg_news_bot.services.workflow_types import DraftAction, TransitionRequest
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
    trend_collector: TrendCollector | None = None
    trend_discovery: TrendDiscoveryService | None = None
    scheduled_repo: ScheduledPostRepository | None = None
    draft_repo: DraftRepository | None = None
    analytics: AnalyticsService | None = None


def create_settings_router(context: SettingsContext) -> Router:
    router = Router()
    command_meta = {
        "commands": {
            "syntax": "/commands",
            "description": "Показывает полный список команд с назначением и синтаксисом.",
            "where": "Любой топик рабочей группы.",
        },
        "status": {
            "syntax": "/status",
            "description": "Показывает текущую конфигурацию бота, топиков и канал публикации.",
            "where": "Обычно #General.",
        },
        "set_group": {
            "syntax": "/set_group",
            "description": "Сохраняет ID текущей супергруппы как рабочую группу бота.",
            "where": "Внутри нужной рабочей супергруппы.",
        },
        "set_inbox_topic": {
            "syntax": "/set_inbox_topic",
            "description": "Назначает текущий топик как INBOX (входящие черновики).",
            "where": "Запускать внутри INBOX topic.",
        },
        "set_service_topic": {
            "syntax": "/set_service_topic",
            "description": "Назначает текущий топик как EDITING (редактирование).",
            "where": "Запускать внутри EDITING topic.",
        },
        "set_ready_topic": {
            "syntax": "/set_ready_topic",
            "description": "Назначает текущий топик как READY (готово к публикации).",
            "where": "Запускать внутри READY topic.",
        },
        "set_scheduled_topic": {
            "syntax": "/set_scheduled_topic",
            "description": "Назначает текущий топик как SCHEDULED (отложенные).",
            "where": "Запускать внутри SCHEDULED topic.",
        },
        "set_published_topic": {
            "syntax": "/set_published_topic",
            "description": "Назначает текущий топик как PUBLISHED (опубликованные).",
            "where": "Запускать внутри PUBLISHED topic.",
        },
        "set_archive_topic": {
            "syntax": "/set_archive_topic",
            "description": "Назначает текущий топик как ARCHIVE (архив).",
            "where": "Запускать внутри ARCHIVE topic.",
        },
        "set_trend_topic": {
            "syntax": "/set_trend_topic",
            "description": "Назначает текущий топик как TREND_CANDIDATES (модерация трендов).",
            "where": "Запускать внутри topic для тренд-кандидатов.",
        },
        "set_channel": {
            "syntax": "/set_channel <channel_id>",
            "description": "Сохраняет канал, куда отправляются публикации.",
            "where": "Обычно #General.",
            "example": "/set_channel -1001234567890",
        },
        "set_hashtag_mode": {
            "syntax": "/set_hashtag_mode <ru|en|both>",
            "description": "Переключает режим хэштегов в постах: только RU, только EN или оба.",
            "where": "Обычно #General.",
            "example": "/set_hashtag_mode ru",
        },
        "set_draft_hashtags": {
            "syntax": "/set_draft_hashtags <draft_id> <tag1 tag2 ... | tag1,tag2,...>",
            "description": "Ручная установка хэштегов для draft (перезаписывает авто-теги).",
            "where": "Обычно #General.",
            "example": "/set_draft_hashtags 245 #ии #космос #технологии",
        },
        "clear_draft_hashtags": {
            "syntax": "/clear_draft_hashtags <draft_id>",
            "description": "Очищает ручные хэштеги и возвращает авто-генерацию.",
            "where": "Обычно #General.",
            "example": "/clear_draft_hashtags 245",
        },
        "add_source": {
            "syntax": "/add_source <rss_url> [name]",
            "description": "Добавляет новый RSS-источник или обновляет существующий.",
            "where": "Обычно #General.",
            "example": "/add_source https://example.com/rss Tech News",
        },
        "list_sources": {
            "syntax": "/list_sources",
            "description": "Выводит список источников: статус, trust score, topics, SSL-флаги.",
            "where": "Обычно #General.",
        },
        "set_source_topics": {
            "syntax": "/set_source_topics <source_id> <topics>",
            "description": "Задает topic hints для конкретного источника (через запятую).",
            "where": "Обычно #General.",
            "example": "/set_source_topics 3 ai,space,science",
        },
        "clear_source_topics": {
            "syntax": "/clear_source_topics <source_id>",
            "description": "Очищает topic hints у указанного источника.",
            "where": "Обычно #General.",
            "example": "/clear_source_topics 3",
        },
        "set_source_ssl_insecure": {
            "syntax": "/set_source_ssl_insecure <source_id> <on|off>",
            "description": "Включает/выключает insecure SSL fallback для источника.",
            "where": "Обычно #General.",
            "example": "/set_source_ssl_insecure 3 on",
        },
        "enable_source": {
            "syntax": "/enable_source <source_id>",
            "description": "Включает источник для регулярного RSS-поллинга.",
            "where": "Обычно #General.",
            "example": "/enable_source 3",
        },
        "disable_source": {
            "syntax": "/disable_source <source_id>",
            "description": "Отключает источник от RSS-поллинга.",
            "where": "Обычно #General.",
            "example": "/disable_source 3",
        },
        "remove_source": {
            "syntax": "/remove_source <source_id>",
            "description": "Удаляет источник; если есть связанные данные, источник будет выключен.",
            "where": "Обычно #General.",
            "example": "/remove_source 3",
        },
        "source_quality": {
            "syntax": "/source_quality [source_id]",
            "description": "Показывает trust score источников или подробности по одному source_id.",
            "where": "Обычно #General.",
            "example": "/source_quality 3",
        },
        "ingest_now": {
            "syntax": "/ingest_now",
            "description": "Немедленно запускает ingestion по всем включенным источникам.",
            "where": "Обычно #General.",
        },
        "ingest_source": {
            "syntax": "/ingest_source <source_id>",
            "description": "Запускает ingestion только для одного источника.",
            "where": "Обычно #General.",
            "example": "/ingest_source 3",
        },
        "ingest_url": {
            "syntax": "/ingest_url <article_url> [source_id]",
            "description": "Ручной импорт одной статьи во Входящие с полной обработкой.",
            "where": "Обычно #General.",
            "example": "/ingest_url https://example.com/article 3",
        },
        "process_range": {
            "syntax": "/process_range <from_id> <to_id>",
            "description": "Пакетно запускает выжимку и перевод для диапазона Draft ID.",
            "where": "Обычно #General.",
            "example": "/process_range 120 140",
        },
        "scheduled_failed_list": {
            "syntax": "/scheduled_failed_list [limit]",
            "description": "Показывает список failed scheduled-задач публикации.",
            "where": "Обычно #General.",
            "example": "/scheduled_failed_list 20",
        },
        "scheduled_retry": {
            "syntax": "/scheduled_retry <draft_id>",
            "description": "Ставит failed scheduled-задачу на немедленный повтор.",
            "where": "Обычно #General.",
            "example": "/scheduled_retry 132",
        },
        "scheduled_cancel": {
            "syntax": "/scheduled_cancel <draft_id>",
            "description": "Отменяет scheduled-задачу и возвращает draft в READY.",
            "where": "Обычно #General.",
            "example": "/scheduled_cancel 132",
        },
        "collect_trends": {
            "syntax": "/collect_trends",
            "description": "Принудительно собирает trend-сигналы (arXiv/HN/X/Reddit).",
            "where": "Обычно #General.",
        },
        "trends": {
            "syntax": "/trends [hours] [limit]",
            "description": "Показывает последние trend-сигналы из БД.",
            "where": "Обычно #General.",
            "example": "/trends 24 30",
        },
        "trend_scan": {
            "syntax": "/trend_scan [hours] [limit]",
            "description": "Анализирует сеть по профилям тем, формирует topic/article/source кандидаты.",
            "where": "Обычно #General.",
            "example": "/trend_scan 24 6",
        },
        "trend_profile_add": {
            "syntax": "/trend_profile_add <name>|<seed_csv>[|<exclude_csv>|<trusted_domains_csv>|<min_score>]",
            "description": "Добавляет или обновляет профиль темы для trend scan.",
            "where": "Обычно #General.",
            "example": "/trend_profile_add Quantum|quantum,qubit,superconducting|casino,betting|nature.com,arxiv.org|1.4",
        },
        "trend_profile_list": {
            "syntax": "/trend_profile_list [all]",
            "description": "Показывает профили тем, по которым бот ищет тренды.",
            "where": "Обычно #General.",
            "example": "/trend_profile_list all",
        },
        "trend_profile_enable": {
            "syntax": "/trend_profile_enable <profile_id>",
            "description": "Включает профиль темы в trend scan.",
            "where": "Обычно #General.",
            "example": "/trend_profile_enable 7",
        },
        "trend_profile_disable": {
            "syntax": "/trend_profile_disable <profile_id>",
            "description": "Отключает профиль темы в trend scan.",
            "where": "Обычно #General.",
            "example": "/trend_profile_disable 7",
        },
        "trend_theme_add": {
            "syntax": "/trend_theme_add <name>|<seed_csv>[|<exclude_csv>|<trusted_domains_csv>|<min_score>]",
            "description": "Алиас темы трендов: добавляет/обновляет тему поиска в интернет-скоринге.",
            "where": "Обычно #General.",
            "example": "/trend_theme_add AI|ai,llm,inference|casino,betting|openai.com,arxiv.org|1.3",
        },
        "trend_theme_list": {
            "syntax": "/trend_theme_list [all]",
            "description": "Алиас темы трендов: список активных/всех тем для сканирования.",
            "where": "Обычно #General.",
            "example": "/trend_theme_list all",
        },
        "trend_theme_enable": {
            "syntax": "/trend_theme_enable <theme_id>",
            "description": "Алиас темы трендов: включает тему поиска.",
            "where": "Обычно #General.",
            "example": "/trend_theme_enable 7",
        },
        "trend_theme_disable": {
            "syntax": "/trend_theme_disable <theme_id>",
            "description": "Алиас темы трендов: отключает тему поиска.",
            "where": "Обычно #General.",
            "example": "/trend_theme_disable 7",
        },
        "trend_topics": {
            "syntax": "/trend_topics [hours] [limit]",
            "description": "Показывает найденные трендовые темы за окно времени.",
            "where": "Обычно #General.",
            "example": "/trend_topics 24 10",
        },
        "trend_articles": {
            "syntax": "/trend_articles <topic_id> [limit]",
            "description": "Показывает кандидаты статей по теме и их score.",
            "where": "Обычно #General.",
            "example": "/trend_articles 17 15",
        },
        "trend_sources": {
            "syntax": "/trend_sources <topic_id> [limit]",
            "description": "Показывает кандидаты источников по теме и их score.",
            "where": "Обычно #General.",
            "example": "/trend_sources 17 10",
        },
        "trend_ingest": {
            "syntax": "/trend_ingest <candidate_id>",
            "description": "Подтверждает статью-кандидат и отправляет её во Входящие.",
            "where": "Обычно #General.",
            "example": "/trend_ingest 144",
        },
        "trend_add_source": {
            "syntax": "/trend_add_source <candidate_id>",
            "description": "Подтверждает source-кандидат и добавляет источник (disabled).",
            "where": "Обычно #General.",
            "example": "/trend_add_source 42",
        },
        "analytics": {
            "syntax": "/analytics [hours]",
            "description": "Показывает сводную операционную аналитику по пайплайну.",
            "where": "Обычно #General.",
            "example": "/analytics 24",
        },
        # editing router command
        "cancel": {
            "syntax": "/cancel",
            "description": "Отменяет активную edit-сессию для текущего draft.",
            "where": "Внутри EDITING topic.",
        },
    }
    command_sections = {
        "Общие": {"commands", "status"},
        "Настройка группы/топиков": {
            "set_group",
            "set_inbox_topic",
            "set_service_topic",
            "set_ready_topic",
            "set_scheduled_topic",
            "set_published_topic",
            "set_archive_topic",
            "set_trend_topic",
            "set_channel",
            "set_hashtag_mode",
            "set_draft_hashtags",
            "clear_draft_hashtags",
        },
        "Источники": {
            "add_source",
            "list_sources",
            "set_source_topics",
            "clear_source_topics",
            "set_source_ssl_insecure",
            "enable_source",
            "disable_source",
            "remove_source",
            "source_quality",
        },
        "Ingestion/обработка": {
            "ingest_now",
            "ingest_source",
            "ingest_url",
            "process_range",
        },
        "Operations": {
            "scheduled_failed_list",
            "scheduled_retry",
            "scheduled_cancel",
            "collect_trends",
            "trends",
            "trend_scan",
            "trend_profile_add",
            "trend_profile_list",
            "trend_profile_enable",
            "trend_profile_disable",
            "trend_theme_add",
            "trend_theme_list",
            "trend_theme_enable",
            "trend_theme_disable",
            "trend_topics",
            "trend_articles",
            "trend_sources",
            "trend_ingest",
            "trend_add_source",
            "analytics",
        },
        "EDITING": {"cancel"},
    }
    preferred_order = list(command_meta.keys())
    scheduled_repo = context.scheduled_repo or ScheduledPostRepository()
    draft_repo = context.draft_repo or DraftRepository()
    analytics_service = context.analytics or AnalyticsService(context.session_factory)
    trend_profiles_repo = TrendTopicProfileRepository()
    trend_status_labels = {
        "PENDING": "ожидает",
        "APPROVED": "подтверждён",
        "REJECTED": "отклонён",
        "INGESTED": "добавлен во входящие",
        "FAILED": "ошибка",
    }

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

    def parse_csv(raw: str) -> list[str]:
        values = [item.strip().lower() for item in raw.split(",")]
        return [value for value in values if value]

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

    def parse_ingest_url_args(raw: str) -> tuple[str, int | None] | None:
        parts = raw.strip().split()
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0], None
        if len(parts) == 2:
            try:
                source_id = int(parts[1])
            except ValueError:
                return None
            if source_id <= 0:
                return None
            return parts[0], source_id
        return None

    def parse_positive_int(raw: str | None) -> int | None:
        if raw is None:
            return None
        text = raw.strip()
        if not text:
            return None
        try:
            value = int(text)
        except ValueError:
            return None
        if value <= 0:
            return None
        return value

    def parse_non_negative_float(raw: str | None) -> float | None:
        if raw is None:
            return None
        text = raw.strip()
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
        if value < 0:
            return None
        return value

    def parse_trend_profile_args(raw: str | None) -> tuple[str, list[str], list[str], list[str], float | None] | None:
        if raw is None:
            return None
        parts = [item.strip() for item in raw.split("|")]
        if len(parts) < 2 or len(parts) > 5:
            return None
        name = parts[0]
        seeds = parse_csv(parts[1])
        if not name or not seeds:
            return None
        excludes = parse_csv(parts[2]) if len(parts) >= 3 else []
        trusted_domains = parse_csv(parts[3]) if len(parts) >= 4 else []
        min_score = parse_non_negative_float(parts[4]) if len(parts) >= 5 else None
        if len(parts) >= 5 and min_score is None:
            return None
        return name, seeds, excludes, trusted_domains, min_score

    def parse_hashtag_mode(raw: str | None) -> str | None:
        if raw is None:
            return None
        mode = raw.strip().lower()
        if mode not in {"ru", "en", "both"}:
            return None
        return mode

    def parse_draft_hashtags_args(raw: str | None) -> tuple[int, list[str]] | None:
        if raw is None:
            return None
        text = raw.strip()
        if not text:
            return None
        parts = text.split(maxsplit=1)
        draft_id = parse_positive_int(parts[0])
        if draft_id is None:
            return None
        if len(parts) == 1:
            return draft_id, []
        raw_tags = parts[1]
        tags: list[str] = []
        seen: set[str] = set()
        for token in re.split(r"[,\s]+", raw_tags):
            value = token.strip().lstrip("#").lower()
            if not value:
                continue
            if not re.fullmatch(r"[0-9a-zа-яё_]{2,32}", value):
                continue
            if value not in seen:
                seen.add(value)
                tags.append(value)
        return draft_id, tags

    def parse_id_and_optional_limit(raw: str | None) -> tuple[int, int | None] | None:
        if raw is None:
            return None
        parts = raw.strip().split()
        if not parts:
            return None
        first = parse_positive_int(parts[0])
        if first is None:
            return None
        if len(parts) == 1:
            return first, None
        if len(parts) == 2:
            second = parse_positive_int(parts[1])
            if second is None:
                return None
            return first, second
        return None

    def _discover_router_commands() -> list[str]:
        discovered: list[str] = []
        for handler in router.message.handlers:
            for filter_obj in handler.filters:
                command_filter = getattr(filter_obj, "callback", filter_obj)
                names = getattr(command_filter, "commands", None)
                if not names:
                    names = getattr(filter_obj, "commands", None)
                if not names:
                    continue
                for name in names:
                    command_value = getattr(name, "command", name)
                    command_name = str(command_value).strip().lstrip("/").lower()
                    if command_name and command_name not in discovered:
                        discovered.append(command_name)
        return discovered

    def render_commands_help_pages() -> list[str]:
        page_limit = 3500

        def render_command_lines(name: str) -> list[str]:
            meta = command_meta.get(name, {})
            syntax = str(meta.get("syntax", f"/{name}"))
            description = str(meta.get("description", "Описание команды не указано."))
            where = str(meta.get("where", "Любой топик рабочей группы."))
            lines = [
                f"• {syntax}",
                f"  Синтаксис: {syntax}",
                f"  Что делает: {description}",
                f"  Где запускать: {where}",
            ]
            example = meta.get("example")
            if example:
                lines.append(f"  Пример: {example}")
            return lines

        discovered = _discover_router_commands()
        for extra in (*command_meta.keys(), "cancel"):
            if extra not in discovered:
                discovered.append(extra)

        ordered = [item for item in preferred_order if item in discovered]
        ordered.extend(sorted(item for item in discovered if item not in ordered))

        blocks: list[str] = []
        for section_title, section_items in command_sections.items():
            rows = [item for item in ordered if item in section_items]
            if not rows:
                continue
            section_lines = [f"{section_title}:"]
            for name in rows:
                section_lines.extend(render_command_lines(name))
                section_lines.append("")
            blocks.append("\n".join(section_lines).rstrip())

        other = [item for item in ordered if not any(item in group for group in command_sections.values())]
        if other:
            section_lines = ["Прочее:"]
            for name in other:
                section_lines.extend(render_command_lines(name))
                section_lines.append("")
            blocks.append("\n".join(section_lines).rstrip())

        blocks.append(
            "\n".join(
                [
                    "Подсказки:",
                    "1) Команды /set_*_topic запускайте внутри нужного топика.",
                    "2) Операционные команды обычно запускаются в #General.",
                    "3) Параметры в <угловых> скобках обязательны, в [квадратных] опциональны.",
                ]
            )
        )

        pages: list[str] = []
        current = (
            "У каждой команды указан правильный синтаксис и назначение.\n"
            "Справка может приходить несколькими сообщениями."
        )
        for block in blocks:
            separator = "\n\n"
            candidate = f"{current}{separator}{block}".strip()
            if len(candidate) <= page_limit:
                current = candidate
                continue
            pages.append(current)
            current = block
        if current:
            pages.append(current)

        total = max(len(pages), 1)
        formatted_pages: list[str] = []
        for idx, page in enumerate(pages, start=1):
            if idx == 1:
                header = f"Справка команд (admin, {idx}/{total}):"
            else:
                header = f"Справка команд (продолжение, {idx}/{total}):"
            formatted_pages.append(f"{header}\n{page}")
        return formatted_pages

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
            f"Пропущено (unsafe): {stats.skipped_unsafe}",
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

    @router.message(Command("set_trend_topic"))
    async def set_trend_topic(message: Message) -> None:
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
            bs.trend_candidates_topic_id = topic_id

        await update_settings(updater)
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"TREND_CANDIDATES топик сохранён: {topic_id}",
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

    @router.message(Command("set_hashtag_mode"))
    async def set_hashtag_mode(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        mode = parse_hashtag_mode(command.args if command else None)
        if mode is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /set_hashtag_mode <ru|en|both>",
            )
            return
        context.settings.post_formatting.hashtag_mode = mode
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Режим хэштегов обновлён: {mode}",
        )

    @router.message(Command("set_draft_hashtags"))
    async def set_draft_hashtags(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        parsed = parse_draft_hashtags_args(command.args if command else None)
        if parsed is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /set_draft_hashtags <draft_id> <tag1 tag2 ... | tag1,tag2,...>",
            )
            return
        draft_id, tags = parsed
        if not tags:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Нужно указать хотя бы один валидный хэштег.",
            )
            return
        async with context.session_factory() as session:
            async with session.begin():
                draft = await draft_repo.get_for_update(session, draft_id)
                if draft is None:
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text=f"Draft #{draft_id} не найден.",
                    )
                    return
                reasons = draft.score_reasons if isinstance(draft.score_reasons, dict) else {}
                reasons["manual_hashtags"] = [f"#{tag}" for tag in tags]
                draft.score_reasons = reasons
                await session.flush()
        if context.workflow is not None:
            try:
                await context.workflow.refresh_draft_messages(draft_id=draft_id)
            except Exception:
                log.exception("settings.set_draft_hashtags_refresh_failed", draft_id=draft_id)
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Ручные хэштеги для Draft #{draft_id}: {' '.join(f'#{tag}' for tag in tags)}",
        )

    @router.message(Command("clear_draft_hashtags"))
    async def clear_draft_hashtags(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        draft_id = parse_positive_int(command.args if command else None)
        if draft_id is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /clear_draft_hashtags <draft_id>",
            )
            return
        async with context.session_factory() as session:
            async with session.begin():
                draft = await draft_repo.get_for_update(session, draft_id)
                if draft is None:
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text=f"Draft #{draft_id} не найден.",
                    )
                    return
                reasons = draft.score_reasons if isinstance(draft.score_reasons, dict) else {}
                reasons.pop("manual_hashtags", None)
                draft.score_reasons = reasons if reasons else None
                await session.flush()
        if context.workflow is not None:
            try:
                await context.workflow.refresh_draft_messages(draft_id=draft_id)
            except Exception:
                log.exception("settings.clear_draft_hashtags_refresh_failed", draft_id=draft_id)
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Ручные хэштеги для Draft #{draft_id} очищены. Включена авто-генерация.",
        )

    @router.message(Command("status"))
    async def status(message: Message) -> None:
        if not is_admin(message):
            return
        sources_total = 0
        enabled_total = 0
        avg_trust = 0.0
        async with context.session_factory() as session:
            async with session.begin():
                bot_settings = await context.repository.get_or_create(session)
                sources = await context.source_repository.list_all(session)
                sources_total = len(sources)
                enabled_total = sum(1 for item in sources if item.enabled)
                if sources_total:
                    avg_trust = sum(float(item.trust_score or 0.0) for item in sources) / sources_total
        lines = [
            "Текущие настройки:",
            f"group_chat_id: {bot_settings.group_chat_id}",
            f"inbox_topic_id: {bot_settings.inbox_topic_id}",
            f"editing_topic_id: {bot_settings.editing_topic_id}",
            f"ready_topic_id: {bot_settings.ready_topic_id}",
            f"scheduled_topic_id: {bot_settings.scheduled_topic_id}",
            f"published_topic_id: {bot_settings.published_topic_id}",
            f"archive_topic_id: {bot_settings.archive_topic_id}",
            f"trend_candidates_topic_id: {bot_settings.trend_candidates_topic_id}",
            f"channel_id: {bot_settings.channel_id}",
            f"sources_total: {sources_total}",
            f"sources_enabled: {enabled_total}",
            f"sources_avg_trust: {avg_trust:.2f}",
            f"hashtags_mode: {context.settings.post_formatting.hashtag_mode}",
            f"trend_discovery_mode: {context.settings.trend_discovery.mode}",
            f"internet_scoring_enabled: {context.settings.internet_scoring.enabled}",
        ]
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(lines),
        )

    @router.message(Command("commands"))
    async def commands_help(message: Message) -> None:
        if not is_admin(message):
            return
        for page in render_commands_help_pages():
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=page,
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
                f"trust_score: {float(source.trust_score or 0.0):.2f}\n"
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
            lines.append(
                f"#{item.id} [{state}] trust={float(item.trust_score or 0.0):.2f} {item.name}"
            )
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
                quality = item.tags.get("quality")
                if isinstance(quality, dict):
                    lines.append(f"quality_events: {quality.get('events_total', 0)}")
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

    @router.message(Command("ingest_url"))
    async def ingest_url(message: Message, command: CommandObject) -> None:
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
                text="Формат: /ingest_url <article_url> [source_id]",
            )
            return
        parsed_args = parse_ingest_url_args(command.args)
        if not parsed_args:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /ingest_url <article_url> [source_id]",
            )
            return
        article_url, source_id = parsed_args
        if not valid_source_url(article_url):
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Некорректный URL. Нужен http/https URL статьи.",
            )
            return

        preface = "Запускаю обработку статьи по ссылке..."
        if source_id is not None:
            preface = f"Запускаю обработку статьи по ссылке (source #{source_id})..."
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=preface,
        )
        try:
            result = await context.ingestion_runner.ingest_url(
                url=article_url,
                source_id=source_id,
            )
        except Exception:
            log.exception("settings.ingest_url_failed", article_url=article_url)
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Ошибка обработки ссылки. Смотри логи контейнера.",
            )
            return

        if result.created:
            draft_label = f"#{result.draft_id}" if result.draft_id is not None else "создан"
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=(
                    f"Готово: Draft {draft_label} отправлен во Входящие.\n"
                    f"url: {result.normalized_url or article_url}"
                ),
            )
            return

        reason_map = {
            "duplicate": "Дубликат: такой материал уже есть в drafts/articles.",
            "blocked": "URL отклонён правилами blocked_domains/keywords.",
            "low_score": "Материал отклонён: score ниже порога.",
            "no_html": "Не удалось получить HTML страницы.",
            "unsafe": "Материал отклонён фильтром контент-безопасности.",
            "invalid_entry": "Не удалось обработать ссылку как статью.",
            "invalid_url": "Некорректный URL.",
            "source_not_found": "Источник не найден. Проверьте source_id.",
            "not_created": "Материал не был создан (неизвестная причина).",
        }
        reason_text = reason_map.get(result.reason or "", "Материал не был создан.")
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=(
                f"{reason_text}\n"
                f"url: {result.normalized_url or article_url}"
            ),
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

    @router.message(Command("source_quality"))
    async def source_quality(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return

        source_id = parse_positive_int(command.args if command and command.args else None)
        async with context.session_factory() as session:
            async with session.begin():
                if source_id is not None:
                    source = await context.source_repository.get_by_id(session, source_id)
                    if not source:
                        await context.publisher.send_text(
                            chat_id=message.chat.id,
                            topic_id=message.message_thread_id,
                            text=f"Источник #{source_id} не найден.",
                        )
                        return
                    tags = source.tags if isinstance(source.tags, dict) else {}
                    quality = tags.get("quality") if isinstance(tags.get("quality"), dict) else {}
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text=(
                            f"Источник #{source.id}: {source.name}\n"
                            f"enabled: {source.enabled}\n"
                            f"trust_score: {float(source.trust_score):.2f}\n"
                            f"events_total: {quality.get('events_total', 0)}\n"
                            f"last_event: {quality.get('last_event', '-')}"
                        ),
                    )
                    return
                sources = await context.source_repository.list_all(session)

        if not sources:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Источники не найдены.",
            )
            return

        ordered = sorted(sources, key=lambda item: float(item.trust_score or 0.0), reverse=True)
        lines = [f"Source quality: {len(ordered)}"]
        for source in ordered[:25]:
            lines.append(
                f"#{source.id} trust={float(source.trust_score):.2f} enabled={source.enabled} {source.name}"
            )
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(lines),
        )

    @router.message(Command("scheduled_failed_list"))
    async def scheduled_failed_list(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        raw_limit = command.args if command else None
        limit = parse_positive_int(raw_limit) or 10
        limit = min(limit, 50)
        async with context.session_factory() as session:
            async with session.begin():
                rows = await scheduled_repo.list_failed(session, limit=limit)
                if not rows:
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text="FAILED scheduled задач нет.",
                    )
                    return
                lines = [f"FAILED scheduled: {len(rows)}"]
                for row in rows:
                    draft = await draft_repo.get(session, row.draft_id)
                    state = draft.state.value if draft else "N/A"
                    next_retry = row.next_retry_at.isoformat() if row.next_retry_at else "-"
                    lines.append(
                        f"draft #{row.draft_id} state={state} attempts={row.attempts} "
                        f"next_retry={next_retry} err={row.last_error or '-'}"
                    )
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(lines),
        )

    @router.message(Command("scheduled_retry"))
    async def scheduled_retry(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        draft_id = parse_positive_int(command.args if command else None)
        if draft_id is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /scheduled_retry <draft_id>",
            )
            return
        async with context.session_factory() as session:
            async with session.begin():
                ok = await scheduled_repo.retry_now_by_draft(session, draft_id=draft_id)
        if not ok:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=f"Scheduled задача для Draft #{draft_id} не найдена.",
            )
            return
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Повтор запланирован для Draft #{draft_id}.",
        )

    @router.message(Command("scheduled_cancel"))
    async def scheduled_cancel(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        draft_id = parse_positive_int(command.args if command else None)
        if draft_id is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /scheduled_cancel <draft_id>",
            )
            return

        if context.workflow is not None:
            async with context.session_factory() as session:
                async with session.begin():
                    draft = await draft_repo.get(session, draft_id)
            if draft and draft.state == DraftState.SCHEDULED:
                try:
                    await context.workflow.transition(
                        TransitionRequest(
                            draft_id=draft_id,
                            action=DraftAction.CANCEL_SCHEDULE,
                            user_id=message.from_user.id if message.from_user else 0,
                        )
                    )
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text=f"Draft #{draft_id} переведён в READY, schedule отменён.",
                    )
                    return
                except Exception:
                    log.exception("settings.scheduled_cancel_workflow_failed", draft_id=draft_id)

        async with context.session_factory() as session:
            async with session.begin():
                draft = await draft_repo.get(session, draft_id)
                ok = await scheduled_repo.cancel_by_draft(session, draft_id=draft_id)
                if draft and draft.state == DraftState.SCHEDULED:
                    draft.state = DraftState.READY
                    await session.flush()
        if not ok:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=f"Scheduled задача для Draft #{draft_id} не найдена.",
            )
            return
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Scheduled задача Draft #{draft_id} отменена.",
        )

    @router.message(Command("collect_trends"))
    async def collect_trends(message: Message) -> None:
        if not is_admin(message):
            return
        if context.trend_collector is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Сборщик трендов недоступен.",
            )
            return
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="Собираю тренды...",
        )
        try:
            stats = await context.trend_collector.collect_once()
        except Exception:
            log.exception("settings.collect_trends_failed")
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Ошибка сбора трендов. Смотри логи.",
            )
            return
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=(
                f"Тренды обновлены.\n"
                f"добавлено сигналов: {stats.inserted}\n"
                f"ключевых слов: {stats.keywords_total}"
            ),
        )

    @router.message(Command("trends"))
    async def trends(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if context.trend_collector is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Сборщик трендов недоступен.",
            )
            return
        hours = 24
        limit = 20
        if command and command.args:
            parts = command.args.strip().split()
            if len(parts) >= 1:
                parsed_hours = parse_positive_int(parts[0])
                if parsed_hours is not None:
                    hours = min(parsed_hours, 240)
            if len(parts) >= 2:
                parsed_limit = parse_positive_int(parts[1])
                if parsed_limit is not None:
                    limit = min(parsed_limit, 50)
        rows = await context.trend_collector.list_recent_signals(hours=hours, limit=limit)
        if not rows:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=f"Нет trend-сигналов за {hours}ч.",
            )
            return
        lines = [f"Тренд-сигналы за {hours}ч (топ {len(rows)}):"]
        for source_name, keyword, weight, observed_at in rows:
            lines.append(
                f"{observed_at:%m-%d %H:%M} [{source_name}] {keyword} (w={weight:.2f})"
            )
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(lines),
        )

    @router.message(Command("trend_scan"))
    async def trend_scan(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if context.trend_discovery is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Модуль trend discovery недоступен в текущей конфигурации.",
            )
            return
        hours: int | None = None
        limit: int | None = None
        if command and command.args:
            parts = command.args.strip().split()
            if len(parts) >= 1:
                hours = parse_positive_int(parts[0])
            if len(parts) >= 2:
                limit = parse_positive_int(parts[1])
            if len(parts) > 2:
                hours = None
                limit = None
            if (len(parts) >= 1 and hours is None) or (len(parts) >= 2 and limit is None):
                await context.publisher.send_text(
                    chat_id=message.chat.id,
                    topic_id=message.message_thread_id,
                    text="Формат: /trend_scan [hours] [limit]",
                )
                return
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="Запускаю сканирование трендов...",
        )
        try:
            result = await context.trend_discovery.scan(hours=hours, limit=limit)
        except Exception:
            log.exception("settings.trend_scan_failed")
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Ошибка сканирования трендов. Смотри логи.",
            )
            return
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=(
                f"Сканирование трендов завершено.\n"
                f"режим: {result.mode}\n"
                f"проанализировано материалов: {result.scanned_items}\n"
                f"создано тем: {result.topics_created}\n"
                f"кандидатов статей: {result.article_candidates}\n"
                f"кандидатов источников: {result.source_candidates}\n"
                f"отправлено сообщений в topic: {result.announced_messages}\n"
                f"авто-добавлено во входящие: {result.auto_ingested}\n"
                f"авто-добавлено источников: {result.auto_sources_added}"
            ),
        )

    @router.message(Command("trend_profile_add"))
    async def trend_profile_add(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        parsed = parse_trend_profile_args(command.args if command else None)
        if parsed is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=(
                    "Формат: /trend_profile_add <name>|<seed_csv>"
                    "[|<exclude_csv>|<trusted_domains_csv>|<min_score>]"
                ),
            )
            return

        name, seeds, excludes, trusted_domains, min_score = parsed
        async with context.session_factory() as session:
            async with session.begin():
                existing = await trend_profiles_repo.get_by_name(session, name)
                existed = existing is not None
                current_min_score = float(existing.min_article_score) if existing else 1.2
                profile = await trend_profiles_repo.upsert_by_name(
                    session,
                    payload=TrendTopicProfileInput(
                        name=name,
                        enabled=True,
                        seed_keywords=seeds,
                        exclude_keywords=excludes,
                        trusted_domains=trusted_domains,
                        min_article_score=min_score if min_score is not None else current_min_score,
                        tags={"created_via": "telegram_command"},
                    ),
                )
        action = "обновлён" if existed else "добавлен"
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=(
                f"Профиль #{profile.id} {action}: {profile.name}\n"
                f"ключевых слов: {len(profile.seed_keywords or [])}\n"
                f"исключений: {len(profile.exclude_keywords or [])}\n"
                f"доверенных доменов: {len(profile.trusted_domains or [])}\n"
                f"минимальный score: {float(profile.min_article_score):.2f}\n"
                f"включён: {bool(profile.enabled)}"
            ),
        )

    @router.message(Command("trend_profile_list"))
    async def trend_profile_list(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        include_all = bool(command and command.args and command.args.strip().lower() == "all")
        async with context.session_factory() as session:
            async with session.begin():
                rows = (
                    await trend_profiles_repo.list_all(session)
                    if include_all
                    else await trend_profiles_repo.list_enabled(session)
                )
        if not rows:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Профили trend discovery не найдены.",
            )
            return
        lines = [
            "Профили trend discovery:"
            if include_all
            else "Включённые профили trend discovery:"
        ]
        for row in rows:
            lines.append(
                f"#{row.id} [{('вкл' if row.enabled else 'выкл')}] "
                f"{row.name} | ключевые={len(row.seed_keywords or [])} | "
                f"min_score={float(row.min_article_score):.2f}"
            )
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(lines),
        )

    @router.message(Command("trend_profile_enable"))
    async def trend_profile_enable(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        profile_id = parse_positive_int(command.args if command else None)
        if profile_id is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /trend_profile_enable <profile_id>",
            )
            return
        async with context.session_factory() as session:
            async with session.begin():
                updated = await trend_profiles_repo.set_enabled(
                    session,
                    profile_id=profile_id,
                    enabled=True,
                )
        if updated is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=f"Профиль #{profile_id} не найден.",
            )
            return
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Профиль #{profile_id} включён.",
        )

    @router.message(Command("trend_profile_disable"))
    async def trend_profile_disable(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        profile_id = parse_positive_int(command.args if command else None)
        if profile_id is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /trend_profile_disable <profile_id>",
            )
            return
        async with context.session_factory() as session:
            async with session.begin():
                updated = await trend_profiles_repo.set_enabled(
                    session,
                    profile_id=profile_id,
                    enabled=False,
                )
        if updated is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=f"Профиль #{profile_id} не найден.",
            )
            return
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=f"Профиль #{profile_id} отключён.",
        )

    @router.message(Command("trend_theme_add"))
    async def trend_theme_add(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        await trend_profile_add(message, command)

    @router.message(Command("trend_theme_list"))
    async def trend_theme_list(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        await trend_profile_list(message, command)

    @router.message(Command("trend_theme_enable"))
    async def trend_theme_enable(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        await trend_profile_enable(message, command)

    @router.message(Command("trend_theme_disable"))
    async def trend_theme_disable(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        await trend_profile_disable(message, command)

    @router.message(Command("trend_topics"))
    async def trend_topics(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if context.trend_discovery is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Модуль trend discovery недоступен в текущей конфигурации.",
            )
            return
        hours = context.settings.trend_discovery.default_window_hours
        limit = 10
        if command and command.args:
            parts = command.args.strip().split()
            if len(parts) >= 1:
                parsed_hours = parse_positive_int(parts[0])
                if parsed_hours is None:
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text="Формат: /trend_topics [hours] [limit]",
                    )
                    return
                hours = parsed_hours
            if len(parts) >= 2:
                parsed_limit = parse_positive_int(parts[1])
                if parsed_limit is None:
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text="Формат: /trend_topics [hours] [limit]",
                    )
                    return
                limit = parsed_limit
        rows = await context.trend_discovery.list_topics(hours=hours, limit=limit)
        if not rows:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=f"Трендовые темы за {hours}ч не найдены.",
            )
            return
        lines = [f"Трендовые темы за {hours}ч (топ {len(rows)}):"]
        for row in rows:
            lines.append(
                f"#{row.id} score={float(row.trend_score):.2f} "
                f"доверие={float(row.confidence):.2f} {row.topic_name}"
            )
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(lines),
        )

    @router.message(Command("trend_articles"))
    async def trend_articles(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if context.trend_discovery is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Модуль trend discovery недоступен в текущей конфигурации.",
            )
            return
        parsed = parse_id_and_optional_limit(command.args if command else None)
        if not parsed:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /trend_articles <topic_id> [limit]",
            )
            return
        topic_id, limit_raw = parsed
        limit = min(limit_raw or 20, 50)
        rows = await context.trend_discovery.list_articles(topic_id=topic_id, limit=limit)
        if not rows:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=f"Кандидатов статей для темы #{topic_id} нет.",
            )
            return
        lines = [f"Кандидаты статей для темы #{topic_id} (топ {len(rows)}):"]
        for row in rows:
            status_text = trend_status_labels.get(row.status.value, row.status.value.lower())
            lines.append(
                f"#{row.id} [{status_text}] score={float(row.score):.2f} {row.title or row.url}"
            )
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(lines),
        )

    @router.message(Command("trend_sources"))
    async def trend_sources(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if context.trend_discovery is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Модуль trend discovery недоступен в текущей конфигурации.",
            )
            return
        parsed = parse_id_and_optional_limit(command.args if command else None)
        if not parsed:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /trend_sources <topic_id> [limit]",
            )
            return
        topic_id, limit_raw = parsed
        limit = min(limit_raw or 20, 50)
        rows = await context.trend_discovery.list_sources(topic_id=topic_id, limit=limit)
        if not rows:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=f"Кандидатов источников для темы #{topic_id} нет.",
            )
            return
        lines = [f"Кандидаты источников для темы #{topic_id} (топ {len(rows)}):"]
        for row in rows:
            status_text = trend_status_labels.get(row.status.value, row.status.value.lower())
            lines.append(
                f"#{row.id} [{status_text}] score={float(row.score):.2f} домен={row.domain}"
            )
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(lines),
        )

    @router.message(Command("trend_ingest"))
    async def trend_ingest(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if context.trend_discovery is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Модуль trend discovery недоступен в текущей конфигурации.",
            )
            return
        candidate_id = parse_positive_int(command.args if command else None)
        if candidate_id is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /trend_ingest <candidate_id>",
            )
            return
        result = await context.trend_discovery.ingest_article_candidate(
            candidate_id=candidate_id,
            user_id=message.from_user.id if message.from_user else 0,
        )
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=result.message,
        )

    @router.message(Command("trend_add_source"))
    async def trend_add_source(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if context.trend_discovery is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Модуль trend discovery недоступен в текущей конфигурации.",
            )
            return
        candidate_id = parse_positive_int(command.args if command else None)
        if candidate_id is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /trend_add_source <candidate_id>",
            )
            return
        result = await context.trend_discovery.add_source_candidate(
            candidate_id=candidate_id,
            user_id=message.from_user.id if message.from_user else 0,
        )
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=result.message,
        )

    @router.message(Command("analytics"))
    async def analytics(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        hours = context.settings.analytics.default_window_hours
        if command and command.args:
            parsed = parse_positive_int(command.args.strip())
            if parsed is None:
                await context.publisher.send_text(
                    chat_id=message.chat.id,
                    topic_id=message.message_thread_id,
                    text="Формат: /analytics [hours]",
                )
                return
            hours = min(parsed, context.settings.analytics.max_window_hours)
        snapshot = await analytics_service.snapshot(window_hours=hours)
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=analytics_service.render(snapshot),
        )

    return router
