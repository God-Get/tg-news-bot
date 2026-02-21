"""Settings commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from types import SimpleNamespace
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import feedparser
from httpx import AsyncClient
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_news_bot.config import Settings
from tg_news_bot.db.models import BotSettings, DraftState, ScheduledPostStatus
from tg_news_bot.logging import get_logger
from tg_news_bot.ports.publisher import (
    PublisherEditNotAllowed,
    PublisherNotFound,
    PublisherNotModified,
    PublisherPort,
)
from tg_news_bot.repositories.drafts import DraftRepository
from tg_news_bot.repositories.scheduled_posts import ScheduledPostRepository
from tg_news_bot.repositories.sources import SourceRepository
from tg_news_bot.repositories.trend_candidates import TrendCandidateRepository
from tg_news_bot.repositories.trend_topic_profiles import (
    TrendTopicProfileInput,
    TrendTopicProfileRepository,
)
from tg_news_bot.services.analytics import AnalyticsService
from tg_news_bot.services.autoplan import (
    AutoPlanRules,
    AutoPlanService,
    render_rules,
)
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
    autoplan: AutoPlanService | None = None
    trend_profile_repository: TrendTopicProfileRepository | None = None
    trend_candidates_repository: TrendCandidateRepository | None = None


def parse_source_args(raw_args: str) -> tuple[str, str]:
    raw = raw_args.strip()
    if "|" in raw:
        url, name = raw.split("|", maxsplit=1)
        return url.strip(), name.strip()
    parts = raw.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0].strip(), ""
    return parts[0].strip(), parts[1].strip()


def parse_source_batch_args(raw_args: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw_line in raw_args.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("/add_source"):
            line = line[len("/add_source") :].strip()
        if not line:
            continue
        entries.append(parse_source_args(line))

    if entries:
        return entries

    single = raw_args.strip()
    if not single:
        return []
    if single.lower().startswith("/add_source"):
        single = single[len("/add_source") :].strip()
    if not single:
        return []
    return [parse_source_args(single)]


def create_settings_router(context: SettingsContext) -> Router:
    router = Router()
    telegram_page_limit = 3500
    command_meta = {
        "commands": {
            "syntax": "/commands",
            "description": "Показывает полный список команд с назначением и синтаксисом.",
            "where": "Любой топик рабочей группы.",
        },
        "setup_ui": {
            "syntax": "/setup_ui",
            "description": "Открывает мастер настройки группы/топиков кнопками (без ручного ввода /set_*).",
            "where": "Внутри рабочей супергруппы, желательно из нужного topic.",
        },
        "menu": {
            "syntax": "/menu",
            "description": "Открывает операционный центр с кнопками для быстрых действий без ручного ввода команд.",
            "where": "Обычно #General.",
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
            "description": "Добавляет/обновляет RSS-источник. Поддерживает пакет: несколько строк '<url> | <name>' в одном сообщении.",
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
            "syntax": "/enable_source <source_id[,source_id...]>",
            "description": "Включает один или несколько источников для регулярного RSS-поллинга.",
            "where": "Обычно #General.",
            "example": "/enable_source 3,6,9",
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
        "source_health": {
            "syntax": "/source_health [source_id]",
            "description": "Показывает health источников: HTTP ошибки, пустые фиды, дубликаты, consecutive failures.",
            "where": "Обычно #General.",
            "example": "/source_health 3",
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
        "schedule_map": {
            "syntax": "/schedule_map [hours] [limit]",
            "description": "Показывает карту отложенных публикаций: какой draft и в какое время выйдет.",
            "where": "Обычно #General.",
            "example": "/schedule_map 48 30",
        },
        "autoplan_rules": {
            "syntax": "/autoplan_rules",
            "description": "Показывает текущие правила Smart Scheduler (автопланирование публикаций).",
            "where": "Обычно #General.",
        },
        "set_autoplan_rules": {
            "syntax": "/set_autoplan_rules <min_gap_minutes> <max_posts_per_day> <quiet_start_hour> <quiet_end_hour> [slot_step_minutes] [horizon_hours]",
            "description": "Обновляет правила Smart Scheduler (интервал, лимит в день, quiet hours, шаг слотов, горизонт).",
            "where": "Обычно #General.",
            "example": "/set_autoplan_rules 120 6 23 8 30 24",
        },
        "autoplan_preview": {
            "syntax": "/autoplan_preview [hours] [limit]",
            "description": "Строит предварительный автоплан для READY draft без применения.",
            "where": "Обычно #General.",
            "example": "/autoplan_preview 24 8",
        },
        "autoplan_apply": {
            "syntax": "/autoplan_apply [hours] [limit]",
            "description": "Применяет Smart Scheduler: переводит READY draft в SCHEDULED по рассчитанным слотам.",
            "where": "Обычно #General.",
            "example": "/autoplan_apply 24 8",
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
        "Общие": {"commands", "menu", "status"},
        "Настройка группы/топиков": {
            "setup_ui",
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
            "source_health",
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
            "schedule_map",
            "autoplan_rules",
            "set_autoplan_rules",
            "autoplan_preview",
            "autoplan_apply",
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
    scheduler_timezone = (
        getattr(getattr(context.settings, "scheduler", None), "timezone", None) or "UTC"
    )
    autoplan_service = context.autoplan or (
        AutoPlanService(
            session_factory=context.session_factory,
            timezone_name=scheduler_timezone,
            workflow=context.workflow,
            settings_repo=context.repository,
            peak_hours=getattr(
                getattr(context.settings, "scheduler", None),
                "autoplan_peak_hours",
                [],
            ),
            peak_bonus=float(
                getattr(
                    getattr(context.settings, "scheduler", None),
                    "autoplan_peak_bonus",
                    0.0,
                )
            ),
            topic_weights=getattr(
                getattr(context.settings, "scheduler", None),
                "autoplan_topic_weights",
                {},
            ),
        )
        if context.workflow is not None
        else None
    )
    trend_profiles_repo = context.trend_profile_repository or TrendTopicProfileRepository()
    trend_candidates_repo = context.trend_candidates_repository or TrendCandidateRepository()
    trend_status_labels = {
        "PENDING": "ожидает",
        "APPROVED": "подтверждён",
        "REJECTED": "отклонён",
        "INGESTED": "добавлен во входящие",
        "FAILED": "ошибка",
    }
    ops_callback_prefix = "ops:"
    ops_menu_messages: dict[tuple[int, int | None], int] = {}
    background_jobs: set[asyncio.Task] = set()
    ops_menu_text = (
        "Операционный центр.\n"
        "Разделы: Система, Источники, Тренды, Планировщик.\n"
        "Кнопки выполняют типовые сценарии, команды оставлены для тонкой настройки."
    )

    def ops_data(action: str) -> str:
        return f"{ops_callback_prefix}{action}"

    def menu_scope_key(chat_id: int, topic_id: int | None) -> tuple[int, int | None]:
        return chat_id, topic_id

    async def safe_delete_message(*, chat_id: int, message_id: int) -> None:
        try:
            await context.publisher.delete_message(chat_id=chat_id, message_id=message_id)
        except (PublisherNotFound, PublisherEditNotAllowed, PublisherNotModified):
            return
        except Exception:
            log.exception(
                "settings.ops_menu_delete_failed",
                chat_id=chat_id,
                message_id=message_id,
            )

    def launch_background_job(*, job_name: str, coro) -> None:
        task = asyncio.create_task(coro)
        background_jobs.add(task)

        def _on_done(done_task: asyncio.Task) -> None:
            background_jobs.discard(done_task)
            try:
                done_task.result()
            except Exception:
                log.exception("settings.background_job_failed", job=job_name)

        task.add_done_callback(_on_done)

    async def open_ops_menu(*, chat_id: int, topic_id: int | None) -> None:
        key = menu_scope_key(chat_id, topic_id)
        previous_message_id = ops_menu_messages.get(key)
        send_result = await context.publisher.send_text(
            chat_id=chat_id,
            topic_id=topic_id,
            text=ops_menu_text,
            keyboard=build_ops_menu_keyboard(),
        )
        new_message_id = getattr(send_result, "message_id", None)
        if isinstance(new_message_id, int):
            ops_menu_messages[key] = new_message_id
            if previous_message_id is not None and previous_message_id != new_message_id:
                await safe_delete_message(chat_id=chat_id, message_id=previous_message_id)

    async def run_background_with_menu(
        *,
        job_name: str,
        proxy_message: Message | SimpleNamespace,
        action_coro,
    ) -> None:
        async def _job() -> None:
            await action_coro
            try:
                await open_ops_menu(
                    chat_id=proxy_message.chat.id,
                    topic_id=proxy_message.message_thread_id,
                )
            except Exception:
                log.exception("settings.ops_menu_reopen_failed", job=job_name)

        launch_background_job(job_name=job_name, coro=_job())

    async def maybe_reopen_ops_menu(*, chat_id: int, topic_id: int | None) -> None:
        if menu_scope_key(chat_id, topic_id) not in ops_menu_messages:
            return
        await open_ops_menu(chat_id=chat_id, topic_id=topic_id)

    def build_ops_menu_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Система",
                        callback_data=ops_data("page:system"),
                    ),
                    InlineKeyboardButton(
                        text="Источники",
                        callback_data=ops_data("page:sources:1"),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Тренды",
                        callback_data=ops_data("page:trends"),
                    ),
                    InlineKeyboardButton(
                        text="Планировщик",
                        callback_data=ops_data("page:scheduler"),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Обновить меню",
                        callback_data=ops_data("menu"),
                    ),
                ],
            ]
        )

    def build_ops_system_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Статус", callback_data=ops_data("act:status")),
                    InlineKeyboardButton(text="Команды", callback_data=ops_data("act:commands")),
                ],
                [
                    InlineKeyboardButton(text="Ingest сейчас", callback_data=ops_data("act:ingest_now")),
                    InlineKeyboardButton(text="Аналитика 24ч", callback_data=ops_data("act:analytics24")),
                ],
                [
                    InlineKeyboardButton(text="Source health", callback_data=ops_data("act:source_health")),
                ],
                [
                    InlineKeyboardButton(text="Setup Wizard", callback_data=ops_data("page:setup")),
                ],
                [
                    InlineKeyboardButton(text="Назад", callback_data=ops_data("menu")),
                ],
            ]
        )

    def build_ops_scheduler_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Карта публикаций",
                        callback_data=ops_data("act:schedule_map"),
                    ),
                    InlineKeyboardButton(
                        text="Autoplan preview",
                        callback_data=ops_data("act:autoplan_preview"),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Autoplan apply",
                        callback_data=ops_data("act:autoplan_apply"),
                    ),
                    InlineKeyboardButton(
                        text="Autoplan rules",
                        callback_data=ops_data("act:autoplan_rules"),
                    ),
                ],
                [
                    InlineKeyboardButton(text="Назад", callback_data=ops_data("menu")),
                ],
            ]
        )

    def build_ops_trends_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Collect trends", callback_data=ops_data("tr:collect")),
                    InlineKeyboardButton(text="Trend scan", callback_data=ops_data("tr:scan")),
                ],
                [
                    InlineKeyboardButton(text="Signals", callback_data=ops_data("tr:signals")),
                    InlineKeyboardButton(text="Topics", callback_data=ops_data("tr:topics")),
                ],
                [
                    InlineKeyboardButton(text="Очередь кандидатов", callback_data=ops_data("page:trend_queue:1")),
                ],
                [
                    InlineKeyboardButton(text="Профили тем", callback_data=ops_data("page:trend_profiles:1")),
                ],
                [
                    InlineKeyboardButton(text="Назад", callback_data=ops_data("menu")),
                ],
            ]
        )

    async def render_ops_sources_page(
        *,
        page: int,
        page_size: int = 6,
    ) -> tuple[str, InlineKeyboardMarkup]:
        requested_page = max(page, 1)
        async with context.session_factory() as session:
            async with session.begin():
                sources = await context.source_repository.list_all(session)

        total = len(sources)
        total_pages = max((total + page_size - 1) // page_size, 1)
        current_page = min(requested_page, total_pages)
        offset = (current_page - 1) * page_size
        rows = sources[offset : offset + page_size]

        lines = [
            f"Источники RSS: {total}",
            f"Страница: {current_page}/{total_pages}",
            "Кнопки у источника: ON/OFF и разовый ingestion.",
        ]
        keyboard_rows: list[list[InlineKeyboardButton]] = []
        if not rows:
            lines.append("Источники не настроены.")
        for source in rows:
            state = "ON" if source.enabled else "OFF"
            trust = float(source.trust_score or 0.0)
            lines.append(f"#{source.id} [{state}] trust={trust:.2f} {source.name}")
            lines.append(source.url)
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{state} #{source.id}",
                        callback_data=ops_data(f"src:tgl:{source.id}:{current_page}"),
                    ),
                    InlineKeyboardButton(
                        text=f"Ingest #{source.id}",
                        callback_data=ops_data(f"src:ing:{source.id}:{current_page}"),
                    ),
                ]
            )

        nav_row: list[InlineKeyboardButton] = []
        if current_page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="←",
                    callback_data=ops_data(f"page:sources:{current_page - 1}"),
                )
            )
        if current_page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text="→",
                    callback_data=ops_data(f"page:sources:{current_page + 1}"),
                )
            )
        if nav_row:
            keyboard_rows.append(nav_row)
        keyboard_rows.append(
            [
                InlineKeyboardButton(text="Source health", callback_data=ops_data("act:source_health")),
            ]
        )
        keyboard_rows.append(
            [
                InlineKeyboardButton(text="Назад", callback_data=ops_data("menu")),
            ]
        )

        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    def build_setup_ui_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Сохранить группу", callback_data=ops_data("cfg:group")),
                ],
                [
                    InlineKeyboardButton(text="INBOX", callback_data=ops_data("cfg:inbox")),
                    InlineKeyboardButton(text="EDITING", callback_data=ops_data("cfg:editing")),
                    InlineKeyboardButton(text="READY", callback_data=ops_data("cfg:ready")),
                ],
                [
                    InlineKeyboardButton(text="SCHEDULED", callback_data=ops_data("cfg:scheduled")),
                    InlineKeyboardButton(text="PUBLISHED", callback_data=ops_data("cfg:published")),
                    InlineKeyboardButton(text="ARCHIVE", callback_data=ops_data("cfg:archive")),
                ],
                [
                    InlineKeyboardButton(text="TREND", callback_data=ops_data("cfg:trend")),
                    InlineKeyboardButton(text="Показать статус", callback_data=ops_data("act:status")),
                ],
                [
                    InlineKeyboardButton(text="Назад", callback_data=ops_data("page:system")),
                ],
            ]
        )

    async def render_setup_ui_text(
        *,
        chat_id: int,
        topic_id: int | None,
        info: str | None = None,
    ) -> str:
        async with context.session_factory() as session:
            async with session.begin():
                bot_settings = await context.repository.get_or_create(session)

        def mark(value: int | None) -> str:
            if value is None:
                return "-"
            if topic_id is not None and value == topic_id:
                return f"{value} <= current topic"
            return str(value)

        lines = [
            "Setup wizard группы и топиков",
            f"Текущая группа: {chat_id}",
            f"Текущий topic: {topic_id if topic_id is not None else 'нет'}",
            "",
            f"group_chat_id: {bot_settings.group_chat_id}",
            f"inbox_topic_id: {mark(bot_settings.inbox_topic_id)}",
            f"editing_topic_id: {mark(bot_settings.editing_topic_id)}",
            f"ready_topic_id: {mark(bot_settings.ready_topic_id)}",
            f"scheduled_topic_id: {mark(bot_settings.scheduled_topic_id)}",
            f"published_topic_id: {mark(bot_settings.published_topic_id)}",
            f"archive_topic_id: {mark(bot_settings.archive_topic_id)}",
            f"trend_candidates_topic_id: {mark(bot_settings.trend_candidates_topic_id)}",
            "",
            "Кнопки применяют настройку к текущей группе/топику.",
        ]
        if info:
            lines.append(f"Последнее действие: {info}")
        return "\n".join(lines)

    async def render_ops_trend_topics(
        *,
        hours: int = 24,
        limit: int = 8,
    ) -> tuple[str, InlineKeyboardMarkup]:
        lines = [f"Трендовые темы за {hours}ч:"]
        keyboard_rows: list[list[InlineKeyboardButton]] = []
        if context.trend_discovery is None:
            lines.append("Модуль trend discovery недоступен.")
        else:
            rows = await context.trend_discovery.list_topics(hours=hours, limit=limit)
            if not rows:
                lines.append("Темы не найдены.")
            else:
                for row in rows:
                    lines.append(
                        f"#{row.id} score={float(row.trend_score):.2f} "
                        f"conf={float(row.confidence):.2f} {row.topic_name}"
                    )
                    keyboard_rows.append(
                        [
                            InlineKeyboardButton(
                                text=f"Открыть тему #{row.id}",
                                callback_data=ops_data(f"tr:open:{row.id}"),
                            )
                        ]
                    )
        keyboard_rows.append(
            [
                InlineKeyboardButton(text="Обновить", callback_data=ops_data("tr:topics")),
                InlineKeyboardButton(text="Назад", callback_data=ops_data("page:trends")),
            ]
        )
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    async def render_ops_trend_topic_detail(topic_id: int) -> tuple[str, InlineKeyboardMarkup]:
        lines = [f"Тема #{topic_id}:"]
        keyboard_rows: list[list[InlineKeyboardButton]] = []
        if context.trend_discovery is None:
            lines.append("Модуль trend discovery недоступен.")
        else:
            articles = await context.trend_discovery.list_articles(topic_id=topic_id, limit=5)
            sources = await context.trend_discovery.list_sources(topic_id=topic_id, limit=5)

            lines.append(f"Кандидаты статей: {len(articles)}")
            for row in articles:
                status_obj = getattr(row, "status", None)
                status_value = getattr(status_obj, "value", status_obj)
                status = str(status_value).upper()
                lines.append(
                    f"A#{row.id} [{status.lower()}] score={float(row.score):.2f} "
                    f"{_trim_text(row.title or row.url, 80)}"
                )
                if status not in {"INGESTED", "REJECTED"}:
                    keyboard_rows.append(
                        [
                            InlineKeyboardButton(
                                text=f"Во входящие A#{row.id}",
                                callback_data=ops_data(f"tr:ing:{row.id}:{topic_id}"),
                            )
                        ]
                    )

            lines.append("")
            lines.append(f"Кандидаты источников: {len(sources)}")
            for row in sources:
                status_obj = getattr(row, "status", None)
                status_value = getattr(status_obj, "value", status_obj)
                status = str(status_value).upper()
                lines.append(
                    f"S#{row.id} [{status.lower()}] score={float(row.score):.2f} {row.domain}"
                )
                if status not in {"APPROVED", "REJECTED"}:
                    keyboard_rows.append(
                        [
                            InlineKeyboardButton(
                                text=f"Добавить источник S#{row.id}",
                                callback_data=ops_data(f"tr:add:{row.id}:{topic_id}"),
                            )
                        ]
                    )

        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text="Обновить тему",
                    callback_data=ops_data(f"tr:open:{topic_id}"),
                ),
                InlineKeyboardButton(text="К темам", callback_data=ops_data("tr:topics")),
            ]
        )
        keyboard_rows.append(
            [
                InlineKeyboardButton(text="Назад", callback_data=ops_data("page:trends")),
            ]
        )
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    async def render_ops_trend_queue_page(
        *,
        page: int,
        page_size: int = 4,
    ) -> tuple[str, InlineKeyboardMarkup]:
        requested_page = max(page, 1)
        async with context.session_factory() as session:
            async with session.begin():
                total_articles = await trend_candidates_repo.count_pending_article_candidates(session)
                total_sources = await trend_candidates_repo.count_pending_source_candidates(session)

                total_pages_articles = max((total_articles + page_size - 1) // page_size, 1)
                total_pages_sources = max((total_sources + page_size - 1) // page_size, 1)
                total_pages = max(total_pages_articles, total_pages_sources, 1)
                current_page = min(requested_page, total_pages)
                offset = (current_page - 1) * page_size

                article_rows = await trend_candidates_repo.list_pending_article_candidates(
                    session,
                    limit=page_size,
                    offset=offset,
                )
                source_rows = await trend_candidates_repo.list_pending_source_candidates(
                    session,
                    limit=page_size,
                    offset=offset,
                )

        lines = [
            "Очередь trend-кандидатов (PENDING):",
            f"Страница: {current_page}/{total_pages}",
            f"Статей: {total_articles}, источников: {total_sources}",
        ]
        keyboard_rows: list[list[InlineKeyboardButton]] = []

        lines.append("")
        lines.append("Статьи:")
        if not article_rows:
            lines.append("- нет")
        for row in article_rows:
            lines.append(
                f"A#{row.id} score={float(row.score):.2f} "
                f"{_trim_text(row.title or row.url, 80)}"
            )
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"A#{row.id} Во входящие",
                        callback_data=ops_data(f"tr:qing:{row.id}:{current_page}"),
                    ),
                    InlineKeyboardButton(
                        text="Отклонить",
                        callback_data=ops_data(f"tr:qrej:{row.id}:{current_page}"),
                    ),
                ]
            )

        lines.append("")
        lines.append("Источники:")
        if not source_rows:
            lines.append("- нет")
        for row in source_rows:
            lines.append(
                f"S#{row.id} score={float(row.score):.2f} {row.domain}"
            )
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"S#{row.id} Добавить",
                        callback_data=ops_data(f"tr:qadd:{row.id}:{current_page}"),
                    ),
                    InlineKeyboardButton(
                        text="Отклонить",
                        callback_data=ops_data(f"tr:qsrej:{row.id}:{current_page}"),
                    ),
                ]
            )

        nav_row: list[InlineKeyboardButton] = []
        if current_page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="←",
                    callback_data=ops_data(f"page:trend_queue:{current_page - 1}"),
                )
            )
        if current_page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text="→",
                    callback_data=ops_data(f"page:trend_queue:{current_page + 1}"),
                )
            )
        if nav_row:
            keyboard_rows.append(nav_row)
        keyboard_rows.append(
            [
                InlineKeyboardButton(text="К разделу Тренды", callback_data=ops_data("page:trends")),
            ]
        )
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    async def render_ops_trend_profiles_page(
        *,
        page: int,
        page_size: int = 6,
    ) -> tuple[str, InlineKeyboardMarkup]:
        requested_page = max(page, 1)
        async with context.session_factory() as session:
            async with session.begin():
                profiles = await trend_profiles_repo.list_all(session)

        total = len(profiles)
        total_pages = max((total + page_size - 1) // page_size, 1)
        current_page = min(requested_page, total_pages)
        offset = (current_page - 1) * page_size
        rows = profiles[offset : offset + page_size]

        lines = [
            f"Профили трендов: {total}",
            f"Страница: {current_page}/{total_pages}",
            "Кнопка у профиля переключает его участие в /trend_scan.",
        ]
        keyboard_rows: list[list[InlineKeyboardButton]] = []
        if not rows:
            lines.append("Профили не настроены.")
        for profile in rows:
            state = "ON" if profile.enabled else "OFF"
            lines.append(
                f"#{profile.id} [{state}] {profile.name} "
                f"seed={len(profile.seed_keywords or [])} "
                f"min_score={float(profile.min_article_score):.2f}"
            )
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{state} #{profile.id}",
                        callback_data=ops_data(f"prf:tgl:{profile.id}:{current_page}"),
                    )
                ]
            )

        nav_row: list[InlineKeyboardButton] = []
        if current_page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="←",
                    callback_data=ops_data(f"page:trend_profiles:{current_page - 1}"),
                )
            )
        if current_page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text="→",
                    callback_data=ops_data(f"page:trend_profiles:{current_page + 1}"),
                )
            )
        if nav_row:
            keyboard_rows.append(nav_row)
        keyboard_rows.append(
            [
                InlineKeyboardButton(text="Назад", callback_data=ops_data("page:trends")),
            ]
        )
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    async def send_or_edit_ops_page(
        *,
        query: CallbackQuery,
        text: str,
        keyboard: InlineKeyboardMarkup,
    ) -> None:
        if query.message is None:
            return

        chat_id = query.message.chat.id
        topic_id = query.message.message_thread_id
        current_message_id = query.message.message_id
        key = menu_scope_key(chat_id, topic_id)
        tracked_message_id = ops_menu_messages.get(key)

        candidate_ids: list[int] = []
        if isinstance(tracked_message_id, int):
            candidate_ids.append(tracked_message_id)
        if current_message_id not in candidate_ids:
            candidate_ids.append(current_message_id)

        for candidate_message_id in candidate_ids:
            try:
                await context.publisher.edit_text(
                    chat_id=chat_id,
                    message_id=candidate_message_id,
                    text=text,
                    keyboard=keyboard,
                    disable_web_page_preview=True,
                )
                ops_menu_messages[key] = candidate_message_id
                if (
                    current_message_id != candidate_message_id
                    and current_message_id != tracked_message_id
                ):
                    await safe_delete_message(chat_id=chat_id, message_id=current_message_id)
                return
            except PublisherNotModified:
                ops_menu_messages[key] = candidate_message_id
                return
            except (PublisherNotFound, PublisherEditNotAllowed):
                continue
            except Exception:
                log.exception("settings.ops_menu_edit_failed")

        send_result = await context.publisher.send_text(
            chat_id=chat_id,
            topic_id=topic_id,
            text=text,
            keyboard=keyboard,
        )
        new_message_id = getattr(send_result, "message_id", None)
        if isinstance(new_message_id, int):
            previous_id = ops_menu_messages.get(key)
            ops_menu_messages[key] = new_message_id
            if previous_id is not None and previous_id != new_message_id:
                await safe_delete_message(chat_id=chat_id, message_id=previous_id)
            if current_message_id != new_message_id:
                await safe_delete_message(chat_id=chat_id, message_id=current_message_id)

    def is_admin(message: Message) -> bool:
        return bool(message.from_user and message.from_user.id == context.settings.admin_user_id)

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

    def parse_source_ids(raw: str | None) -> list[int] | None:
        if raw is None:
            return None
        text = raw.strip()
        if not text:
            return None
        source_ids: list[int] = []
        seen: set[int] = set()
        for token in re.split(r"[\s,;]+", text):
            candidate = token.strip()
            if not candidate:
                continue
            if not candidate.isdigit():
                return None
            value = int(candidate)
            if value <= 0:
                return None
            if value not in seen:
                seen.add(value)
                source_ids.append(value)
        return source_ids if source_ids else None

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

    def _trim_text(value: str, limit: int) -> str:
        compact = re.sub(r"\s+", " ", (value or "").strip())
        if len(compact) <= limit:
            return compact
        return f"{compact[: max(limit - 1, 1)].rstrip()}..."

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

    def parse_hour(raw: str | None) -> int | None:
        if raw is None:
            return None
        text = raw.strip()
        if not text:
            return None
        if not text.isdigit():
            return None
        value = int(text)
        if value < 0 or value > 23:
            return None
        return value

    def parse_optional_hours_limit(raw: str | None) -> tuple[int | None, int | None] | None:
        if raw is None:
            return None, None
        parts = raw.strip().split()
        if not parts:
            return None, None
        if len(parts) > 2:
            return None
        hours: int | None = None
        limit: int | None = None
        if len(parts) >= 1:
            parsed_hours = parse_positive_int(parts[0])
            if parsed_hours is None:
                return None
            hours = parsed_hours
        if len(parts) == 2:
            parsed_limit = parse_positive_int(parts[1])
            if parsed_limit is None:
                return None
            limit = parsed_limit
        return hours, limit

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
        page_limit = 3400

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

        def split_long_block(block: str) -> list[str]:
            compact = block.strip()
            if len(compact) <= page_limit:
                return [compact] if compact else []

            paragraphs = [item.strip() for item in compact.split("\n\n") if item.strip()]
            chunks: list[str] = []
            current_chunk = ""

            def flush_chunk() -> None:
                nonlocal current_chunk
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""

            for paragraph in paragraphs:
                candidate = paragraph if not current_chunk else f"{current_chunk}\n\n{paragraph}"
                if len(candidate) <= page_limit:
                    current_chunk = candidate
                    continue

                flush_chunk()
                if len(paragraph) <= page_limit:
                    current_chunk = paragraph
                    continue

                # Extremely large paragraph: split by lines.
                lines = [item.rstrip() for item in paragraph.splitlines() if item.strip()]
                for line in lines:
                    line_candidate = line if not current_chunk else f"{current_chunk}\n{line}"
                    if len(line_candidate) <= page_limit:
                        current_chunk = line_candidate
                        continue
                    flush_chunk()
                    if len(line) <= page_limit:
                        current_chunk = line
                    else:
                        # Hard split as last resort for single overlong line.
                        start = 0
                        while start < len(line):
                            end = start + page_limit
                            chunks.append(line[start:end])
                            start = end
                        current_chunk = ""

            flush_chunk()
            return chunks

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

        expanded_blocks: list[str] = []
        for block in blocks:
            expanded_blocks.extend(split_long_block(block))

        pages: list[str] = []
        current = (
            "У каждой команды указан правильный синтаксис и назначение.\n"
            "Справка может приходить несколькими сообщениями."
        )
        for block in expanded_blocks:
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
        if stats.entries_total == 0:
            lines.append("")
            lines.append("Новых RSS entries не найдено.")
            if stats.rss_fetch_errors > 0:
                lines.append(
                    "Часть источников недоступна (HTTP/SSL/блокировки). Проверьте /source_health."
                )
            else:
                lines.append(
                    "Проверьте, что у источников указан именно RSS URL, а не главная страница сайта."
                )
                lines.append("Команды: /list_sources, /source_health, /add_source <rss_url> [name]")
        return "\n".join(lines)

    def render_autoplan_preview(*, title: str, result) -> str:  # noqa: ANN001
        lines = [
            title,
            f"Окно планирования: {result.window_hours}ч",
            (
                "Правила: "
                f"gap={result.rules.min_gap_minutes}м, "
                f"max/day={result.rules.max_posts_per_day}, "
                f"quiet={result.rules.quiet_start_hour:02d}:00-{result.rules.quiet_end_hour:02d}:00, "
                f"step={result.rules.slot_step_minutes}м, "
                f"tz={result.rules.timezone_name}"
            ),
            f"Рассмотрено READY: {result.considered_count}",
            f"Назначено слотов: {len(result.scheduled)}",
            f"Без слота: {len(result.unscheduled)}",
        ]
        if result.scheduled:
            lines.append("")
            lines.append("План:")
            local_tz = ZoneInfo(result.rules.timezone_name)
            for item in result.scheduled:
                local_time = item.schedule_at.astimezone(local_tz)
                lines.append(
                    f"Draft #{item.draft_id} -> {local_time:%d.%m %H:%M} "
                    f"({result.rules.timezone_name}) priority={item.priority:.2f}"
                )
        if result.unscheduled:
            lines.append("")
            lines.append(
                "Не удалось назначить слот: "
                + ", ".join(f"#{draft_id}" for draft_id in result.unscheduled[:20])
            )
            if len(result.unscheduled) > 20:
                lines.append(f"... ещё {len(result.unscheduled) - 20}")
        return "\n".join(lines)

    def split_text_pages(text: str, *, page_limit: int = telegram_page_limit) -> list[str]:
        compact = text.strip()
        if not compact:
            return []
        if len(compact) <= page_limit:
            return [compact]

        pages: list[str] = []
        current = ""
        for raw_line in compact.splitlines():
            line = raw_line.rstrip()
            if len(line) > page_limit:
                if current:
                    pages.append(current)
                    current = ""
                start = 0
                while start < len(line):
                    end = start + page_limit
                    pages.append(line[start:end])
                    start = end
                continue

            candidate = line if not current else f"{current}\n{line}"
            if len(candidate) <= page_limit:
                current = candidate
            else:
                pages.append(current)
                current = line

        if current:
            pages.append(current)
        return pages

    async def send_paged_text(*, chat_id: int, topic_id: int | None, text: str) -> None:
        pages = split_text_pages(text)
        if not pages:
            return
        for page in pages:
            await context.publisher.send_text(
                chat_id=chat_id,
                topic_id=topic_id,
                text=page,
            )

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
        autoplan_payload: dict | None = None
        async with context.session_factory() as session:
            async with session.begin():
                bot_settings = await context.repository.get_or_create(session)
                autoplan_payload = (
                    bot_settings.autoplan_rules
                    if isinstance(bot_settings.autoplan_rules, dict)
                    else None
                )
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
        if autoplan_payload:
            lines.append(f"autoplan_rules: {autoplan_payload}")
        else:
            lines.append("autoplan_rules: <default>")
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

    @router.message(Command("menu"))
    async def menu(message: Message) -> None:
        if not is_admin(message):
            return
        await open_ops_menu(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
        )

    @router.message(Command("setup_ui"))
    async def setup_ui(message: Message) -> None:
        if not is_admin(message):
            return
        text = await render_setup_ui_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
        )
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=text,
            keyboard=build_setup_ui_keyboard(),
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
        entries = parse_source_batch_args(command.args)
        if not entries:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=(
                    "Формат: /add_source <rss_url> [имя]\n"
                    "или: /add_source <rss_url> | <имя>"
                ),
            )
            return

        if len(entries) == 1:
            source_url, source_name = entries[0]
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
            return

        success_lines: list[str] = []
        error_lines: list[str] = []
        added = 0
        updated = 0

        for idx, (source_url, source_name) in enumerate(entries, start=1):
            if not valid_source_url(source_url):
                error_lines.append(f"{idx}. {source_url or '<empty>'} -> некорректный URL")
                continue

            ok, validation_message = await validate_rss_url(source_url)
            if not ok:
                error_lines.append(f"{idx}. {source_url} -> {validation_message}")
                continue

            resolved_name = source_name or urlparse(source_url).netloc
            async with context.session_factory() as session:
                async with session.begin():
                    existing = await context.source_repository.get_by_url(session, source_url)
                    if existing:
                        existing.name = resolved_name or existing.name
                        existing.enabled = True
                        await session.flush()
                        source = existing
                        action = "обновлён"
                        updated += 1
                    else:
                        source = await context.source_repository.create(
                            session,
                            name=resolved_name,
                            url=source_url,
                            enabled=True,
                        )
                        action = "добавлен"
                        added += 1
            success_lines.append(
                f"{idx}. #{source.id} {action}: {source.name} ({validation_message})"
            )

        lines = [
            f"Обработано строк: {len(entries)}",
            f"Успешно: {len(success_lines)} (добавлено: {added}, обновлено: {updated})",
            f"Ошибки: {len(error_lines)}",
        ]
        if success_lines:
            lines.append("")
            lines.append("Успешные:")
            lines.extend(success_lines[:20])
            if len(success_lines) > 20:
                lines.append(f"... ещё {len(success_lines) - 20}")
        if error_lines:
            lines.append("")
            lines.append("С ошибками:")
            lines.extend(error_lines[:20])
            if len(error_lines) > 20:
                lines.append(f"... ещё {len(error_lines) - 20}")

        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(lines),
        )

    @router.message(Command("list_sources"))
    async def list_sources(message: Message) -> None:
        if not is_admin(message):
            return
        try:
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
            await send_paged_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="\n".join(lines),
            )
        finally:
            await maybe_reopen_ops_menu(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
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
                text="Формат: /enable_source <source_id[,source_id...]>",
            )
            return
        source_ids = parse_source_ids(command.args)
        if not source_ids:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="source_id должны быть числами (через запятую или пробел).",
            )
            return

        enabled_ids: list[int] = []
        missing_ids: list[int] = []
        async with context.session_factory() as session:
            async with session.begin():
                for source_id in source_ids:
                    source = await context.source_repository.get_by_id(session, source_id)
                    if not source:
                        missing_ids.append(source_id)
                        continue
                    source.enabled = True
                    enabled_ids.append(source_id)
                await session.flush()

        result_lines: list[str] = []
        if enabled_ids:
            result_lines.append(f"Включено источников: {len(enabled_ids)}")
            result_lines.append(
                "ID: " + ", ".join(f"#{source_id}" for source_id in enabled_ids)
            )
        if missing_ids:
            result_lines.append(
                "Не найдены: " + ", ".join(f"#{source_id}" for source_id in missing_ids)
            )
        if not result_lines:
            result_lines.append("Изменений нет.")

        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(result_lines),
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
        try:
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
        finally:
            await maybe_reopen_ops_menu(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
            )

    @router.message(Command("ingest_now"))
    async def ingest_now(message: Message) -> None:
        if not is_admin(message):
            return
        try:
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
        finally:
            await maybe_reopen_ops_menu(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
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

        def quality_health(tags: dict | None) -> tuple[dict, dict]:
            payload = tags if isinstance(tags, dict) else {}
            quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
            health = quality.get("health") if isinstance(quality.get("health"), dict) else {}
            return quality, health

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
                    quality, health = quality_health(source.tags)
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text=(
                            f"Источник #{source.id}: {source.name}\n"
                            f"enabled: {source.enabled}\n"
                            f"trust_score: {float(source.trust_score):.2f}\n"
                            f"events_total: {quality.get('events_total', 0)}\n"
                            f"last_event: {quality.get('last_event', '-')}\n"
                            f"consecutive_failures: {health.get('consecutive_failures', 0)}"
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

    @router.message(Command("source_health"))
    async def source_health(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return

        try:
            source_id = parse_positive_int(command.args if command and command.args else None)

            def quality_health(tags: dict | None) -> tuple[dict, dict]:
                payload = tags if isinstance(tags, dict) else {}
                quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
                health = quality.get("health") if isinstance(quality.get("health"), dict) else {}
                return quality, health

            async with context.session_factory() as session:
                async with session.begin():
                    if source_id is not None:
                        source = await context.source_repository.get_by_id(session, source_id)
                        if source is None:
                            await context.publisher.send_text(
                                chat_id=message.chat.id,
                                topic_id=message.message_thread_id,
                                text=f"Источник #{source_id} не найден.",
                            )
                            return
                        quality, health = quality_health(source.tags)
                        await context.publisher.send_text(
                            chat_id=message.chat.id,
                            topic_id=message.message_thread_id,
                            text=(
                                f"Source health #{source.id}: {source.name}\n"
                                f"enabled: {source.enabled}\n"
                                f"trust_score: {float(source.trust_score or 0.0):.2f}\n"
                                f"events_total: {quality.get('events_total', 0)}\n"
                                f"last_event: {quality.get('last_event', '-')}\n"
                                f"consecutive_failures: {health.get('consecutive_failures', 0)}\n"
                                f"rss_http_errors: {health.get('rss_http_errors', 0)}\n"
                                f"rss_http_403: {health.get('rss_http_403', 0)}\n"
                                f"rss_empty: {health.get('rss_empty', 0)}\n"
                                f"duplicates_total: {health.get('duplicates_total', 0)}\n"
                                f"high_duplicate_rate_hits: {health.get('high_duplicate_rate_hits', 0)}"
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

            scored_rows: list[tuple[float, object, dict, dict]] = []
            for source in sources:
                quality, health = quality_health(source.tags)
                failures = float(health.get("consecutive_failures", 0))
                events = float(quality.get("events_total", 0))
                trust = float(source.trust_score or 0.0)
                risk = failures * 2.0 + max(-trust, 0.0) + min(events / 25.0, 2.0)
                scored_rows.append((risk, source, quality, health))

            scored_rows.sort(key=lambda item: item[0], reverse=True)
            lines = [f"Source health: {len(scored_rows)} (top risk)"]
            for risk, source, quality, health in scored_rows[:25]:
                lines.append(
                    f"#{source.id} risk={risk:.2f} enabled={source.enabled} "
                    f"trust={float(source.trust_score or 0.0):.2f} "
                    f"fails={health.get('consecutive_failures', 0)} "
                    f"event={quality.get('last_event', '-')} {source.name}"
                )

            await send_paged_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="\n".join(lines),
            )
        finally:
            await maybe_reopen_ops_menu(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
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

    @router.message(Command("schedule_map"))
    async def schedule_map(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        parsed = parse_optional_hours_limit(command.args if command else None)
        if parsed is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /schedule_map [hours] [limit]",
            )
            return
        hours_raw, limit_raw = parsed
        hours = min(hours_raw or 48, 336)
        limit = min(limit_raw or 30, 120)
        now_utc = datetime.now(timezone.utc)
        until_utc = now_utc + timedelta(hours=hours)
        local_tz = ZoneInfo(scheduler_timezone)

        async with context.session_factory() as session:
            async with session.begin():
                rows = await scheduled_repo.list_upcoming(
                    session,
                    now=now_utc,
                    until=until_utc,
                    limit=limit,
                )
                if not rows:
                    await context.publisher.send_text(
                        chat_id=message.chat.id,
                        topic_id=message.message_thread_id,
                        text=f"Нет отложенных публикаций на ближайшие {hours}ч.",
                    )
                    return

                lines = [
                    f"Карта публикаций на {hours}ч (записей: {len(rows)}):",
                    f"Таймзона: {scheduler_timezone}",
                ]
                current_day = ""
                for item in rows:
                    draft = await draft_repo.get(session, item.draft_id)
                    schedule_at = (
                        item.schedule_at
                        if item.schedule_at.tzinfo is not None
                        else item.schedule_at.replace(tzinfo=timezone.utc)
                    )
                    local_dt = schedule_at.astimezone(local_tz)
                    day_label = f"{local_dt:%d.%m.%Y}"
                    if day_label != current_day:
                        lines.append("")
                        lines.append(day_label)
                        current_day = day_label
                    draft_state = draft.state.value if draft else "N/A"
                    draft_score = float(draft.score or 0.0) if draft else 0.0
                    title = (draft.title_en or "").strip() if draft else ""
                    if len(title) > 120:
                        title = f"{title[:117]}..."
                    lines.append(
                        f"{local_dt:%H:%M} | Draft #{item.draft_id} | {draft_state} | score={draft_score:.2f}"
                    )
                    if title:
                        lines.append(f"{title}")

        await send_paged_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(lines),
        )

    @router.message(Command("autoplan_rules"))
    async def autoplan_rules(message: Message) -> None:
        if not is_admin(message):
            return
        if autoplan_service is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Smart Scheduler недоступен: workflow не инициализирован.",
            )
            return
        rules = await autoplan_service.get_rules()
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="Текущие правила Smart Scheduler:\n" + render_rules(rules),
        )

    @router.message(Command("set_autoplan_rules"))
    async def set_autoplan_rules(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if autoplan_service is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Smart Scheduler недоступен: workflow не инициализирован.",
            )
            return
        raw_parts = command.args.strip().split() if command and command.args else []
        if len(raw_parts) not in {4, 5, 6}:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=(
                    "Формат: /set_autoplan_rules "
                    "<min_gap_minutes> <max_posts_per_day> "
                    "<quiet_start_hour> <quiet_end_hour> "
                    "[slot_step_minutes] [horizon_hours]"
                ),
            )
            return

        min_gap = parse_positive_int(raw_parts[0])
        max_per_day = parse_positive_int(raw_parts[1])
        quiet_start = parse_hour(raw_parts[2])
        quiet_end = parse_hour(raw_parts[3])
        step_minutes = parse_positive_int(raw_parts[4]) if len(raw_parts) >= 5 else None
        horizon_hours = parse_positive_int(raw_parts[5]) if len(raw_parts) >= 6 else None

        if (
            min_gap is None
            or max_per_day is None
            or quiet_start is None
            or quiet_end is None
            or (len(raw_parts) >= 5 and step_minutes is None)
            or (len(raw_parts) >= 6 and horizon_hours is None)
        ):
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text=(
                    "Неверные параметры. "
                    "Пример: /set_autoplan_rules 120 6 23 8 30 24"
                ),
            )
            return

        current = await autoplan_service.get_rules()
        updated = AutoPlanRules(
            timezone_name=current.timezone_name,
            min_gap_minutes=min(max(min_gap, 10), 24 * 60),
            max_posts_per_day=min(max(max_per_day, 1), 24),
            quiet_start_hour=quiet_start,
            quiet_end_hour=quiet_end,
            slot_step_minutes=(
                min(max(step_minutes, 5), 180)
                if step_minutes is not None
                else current.slot_step_minutes
            ),
            horizon_hours=(
                min(max(horizon_hours, 1), 168)
                if horizon_hours is not None
                else current.horizon_hours
            ),
        )
        await autoplan_service.set_rules(updated)
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="Правила Smart Scheduler обновлены:\n" + render_rules(updated),
        )

    @router.message(Command("autoplan_preview"))
    async def autoplan_preview(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if autoplan_service is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Smart Scheduler недоступен: workflow не инициализирован.",
            )
            return
        parsed = parse_optional_hours_limit(command.args if command else None)
        if parsed is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /autoplan_preview [hours] [limit]",
            )
            return
        hours, limit = parsed
        try:
            preview = await autoplan_service.preview(
                hours=(min(hours, 168) if hours is not None else None),
                limit=(min(limit, 50) if limit is not None else 10),
            )
        except Exception:
            log.exception("settings.autoplan_preview_failed")
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Ошибка построения Smart Scheduler preview. Смотри логи.",
            )
            return
        await send_paged_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=render_autoplan_preview(
                title="Smart Scheduler preview",
                result=preview,
            ),
        )

    @router.message(Command("autoplan_apply"))
    async def autoplan_apply(message: Message, command: CommandObject) -> None:
        if not is_admin(message):
            return
        if autoplan_service is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Smart Scheduler недоступен: workflow не инициализирован.",
            )
            return
        parsed = parse_optional_hours_limit(command.args if command else None)
        if parsed is None:
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Формат: /autoplan_apply [hours] [limit]",
            )
            return
        hours, limit = parsed
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="Запускаю Smart Scheduler apply...",
        )
        try:
            applied = await autoplan_service.apply(
                user_id=message.from_user.id if message.from_user else 0,
                hours=(min(hours, 168) if hours is not None else None),
                limit=(min(limit, 50) if limit is not None else 10),
            )
        except Exception:
            log.exception("settings.autoplan_apply_failed")
            await context.publisher.send_text(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                text="Ошибка применения Smart Scheduler. Смотри логи.",
            )
            return

        summary_lines = [
            "Smart Scheduler apply завершён.",
            f"Рассмотрено READY: {applied.preview.considered_count}",
            f"Назначено слотов: {len(applied.preview.scheduled)}",
            f"Успешно переведено в SCHEDULED: {applied.scheduled_count}",
            f"Ошибок перехода: {len(applied.failed_drafts)}",
            f"Без слота: {len(applied.preview.unscheduled)}",
        ]
        if applied.failed_drafts:
            summary_lines.append(
                "Ошибки Draft: "
                + ", ".join(f"#{draft_id}" for draft_id in applied.failed_drafts[:20])
            )
        await context.publisher.send_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text="\n".join(summary_lines),
        )
        await send_paged_text(
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            text=render_autoplan_preview(
                title="Smart Scheduler итоговый план",
                result=applied.preview,
            ),
        )

    @router.message(Command("collect_trends"))
    async def collect_trends(message: Message) -> None:
        if not is_admin(message):
            return
        try:
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
        finally:
            await maybe_reopen_ops_menu(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
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
        try:
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
        finally:
            await maybe_reopen_ops_menu(
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
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

    async def safe_callback_answer(
        query: CallbackQuery,
        *,
        text: str | None = None,
    ) -> None:
        try:
            await query.answer(text=text)
        except TelegramBadRequest:
            return

    def query_to_message(query: CallbackQuery) -> Message | SimpleNamespace | None:
        if query.message is None:
            return None
        return SimpleNamespace(
            from_user=query.from_user,
            chat=query.message.chat,
            message_thread_id=query.message.message_thread_id,
        )

    @router.callback_query(F.data.startswith(ops_callback_prefix))
    async def ops_menu_action(query: CallbackQuery) -> None:
        if not query.from_user or query.from_user.id != context.settings.admin_user_id:
            await safe_callback_answer(query)
            return
        action = (query.data or "").removeprefix(ops_callback_prefix).strip().lower()
        proxy_message = query_to_message(query)
        if proxy_message is None:
            await safe_callback_answer(query, text="Откройте меню в топике группы")
            return

        await safe_callback_answer(query)
        legacy_map = {
            "status": "act:status",
            "commands": "act:commands",
            "ingest_now": "act:ingest_now",
            "list_sources": "page:sources:1",
            "trend_scan": "tr:scan",
            "trend_topics": "tr:topics",
            "trends": "tr:signals",
            "schedule_map": "act:schedule_map",
            "autoplan_preview": "act:autoplan_preview",
            "autoplan_apply": "act:autoplan_apply",
            "autoplan_rules": "act:autoplan_rules",
        }
        action = legacy_map.get(action, action)
        tokens = [token for token in action.split(":") if token]

        try:
            if action == "menu":
                await send_or_edit_ops_page(
                    query=query,
                    text=ops_menu_text,
                    keyboard=build_ops_menu_keyboard(),
                )
                return

            if not tokens:
                await safe_callback_answer(query, text="Действие не поддерживается")
                return

            if tokens[0] == "page":
                page_name = tokens[1] if len(tokens) >= 2 else ""
                if page_name == "system":
                    await send_or_edit_ops_page(
                        query=query,
                        text="Раздел: Система",
                        keyboard=build_ops_system_keyboard(),
                    )
                    return
                if page_name == "setup":
                    text = await render_setup_ui_text(
                        chat_id=proxy_message.chat.id,
                        topic_id=proxy_message.message_thread_id,
                    )
                    await send_or_edit_ops_page(
                        query=query,
                        text=text,
                        keyboard=build_setup_ui_keyboard(),
                    )
                    return
                if page_name == "sources":
                    page = parse_positive_int(tokens[2] if len(tokens) >= 3 else "1") or 1
                    text, keyboard = await render_ops_sources_page(page=page)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return
                if page_name == "trends":
                    await send_or_edit_ops_page(
                        query=query,
                        text="Раздел: Тренды",
                        keyboard=build_ops_trends_keyboard(),
                    )
                    return
                if page_name == "trend_queue":
                    page = parse_positive_int(tokens[2] if len(tokens) >= 3 else "1") or 1
                    text, keyboard = await render_ops_trend_queue_page(page=page)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return
                if page_name == "trend_profiles":
                    page = parse_positive_int(tokens[2] if len(tokens) >= 3 else "1") or 1
                    text, keyboard = await render_ops_trend_profiles_page(page=page)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return
                if page_name == "scheduler":
                    await send_or_edit_ops_page(
                        query=query,
                        text="Раздел: Планировщик публикаций",
                        keyboard=build_ops_scheduler_keyboard(),
                    )
                    return

            if tokens[0] == "act":
                act = tokens[1] if len(tokens) >= 2 else ""
                if act == "status":
                    await status(proxy_message)
                    return
                if act == "commands":
                    await commands_help(proxy_message)
                    return
                if act == "ingest_now":
                    await run_background_with_menu(
                        job_name="ops_ingest_now",
                        proxy_message=proxy_message,
                        action_coro=ingest_now(proxy_message),
                    )
                    await send_or_edit_ops_page(
                        query=query,
                        text="Раздел: Система\nIngest запущен в фоне.",
                        keyboard=build_ops_system_keyboard(),
                    )
                    return
                if act == "analytics24":
                    await analytics(proxy_message, SimpleNamespace(args="24"))
                    return
                if act == "source_health":
                    await source_health(proxy_message, SimpleNamespace(args=None))
                    return
                if act == "schedule_map":
                    await schedule_map(proxy_message, SimpleNamespace(args="48 30"))
                    return
                if act == "autoplan_preview":
                    await autoplan_preview(proxy_message, SimpleNamespace(args="24 10"))
                    return
                if act == "autoplan_apply":
                    await autoplan_apply(proxy_message, SimpleNamespace(args="24 10"))
                    return
                if act == "autoplan_rules":
                    await autoplan_rules(proxy_message)
                    return

            if tokens[0] == "cfg":
                action_name = tokens[1] if len(tokens) >= 2 else ""
                info = ""
                topic_id = proxy_message.message_thread_id

                if action_name == "group":
                    def updater(bs: BotSettings) -> None:
                        bs.group_chat_id = proxy_message.chat.id

                    await update_settings(updater)
                    info = f"group_chat_id={proxy_message.chat.id}"
                else:
                    if topic_id is None:
                        await safe_callback_answer(query, text="Действие доступно только в topic")
                        return
                    field_map = {
                        "inbox": "inbox_topic_id",
                        "editing": "editing_topic_id",
                        "ready": "ready_topic_id",
                        "scheduled": "scheduled_topic_id",
                        "published": "published_topic_id",
                        "archive": "archive_topic_id",
                        "trend": "trend_candidates_topic_id",
                    }
                    target_field = field_map.get(action_name)
                    if target_field is None:
                        await safe_callback_answer(query, text="Неизвестное действие setup")
                        return

                    def updater(bs: BotSettings) -> None:
                        bs.group_chat_id = proxy_message.chat.id
                        setattr(bs, target_field, topic_id)

                    await update_settings(updater)
                    info = f"{target_field}={topic_id}"

                text = await render_setup_ui_text(
                    chat_id=proxy_message.chat.id,
                    topic_id=proxy_message.message_thread_id,
                    info=info,
                )
                await send_or_edit_ops_page(
                    query=query,
                    text=text,
                    keyboard=build_setup_ui_keyboard(),
                )
                return

            if tokens[0] == "src":
                src_action = tokens[1] if len(tokens) >= 2 else ""
                source_id = parse_positive_int(tokens[2] if len(tokens) >= 3 else None)
                page = parse_positive_int(tokens[3] if len(tokens) >= 4 else "1") or 1
                if source_id is None:
                    await safe_callback_answer(query, text="Некорректный source_id")
                    return
                if src_action == "tgl":
                    async with context.session_factory() as session:
                        async with session.begin():
                            source = await context.source_repository.get_by_id(session, source_id)
                            if source is None:
                                await context.publisher.send_text(
                                    chat_id=proxy_message.chat.id,
                                    topic_id=proxy_message.message_thread_id,
                                    text=f"Источник #{source_id} не найден.",
                                )
                            else:
                                source.enabled = not bool(source.enabled)
                                await session.flush()
                                state = "ON" if source.enabled else "OFF"
                                await context.publisher.send_text(
                                    chat_id=proxy_message.chat.id,
                                    topic_id=proxy_message.message_thread_id,
                                    text=f"Источник #{source_id} переключен: {state}",
                                )
                    text, keyboard = await render_ops_sources_page(page=page)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return
                if src_action == "ing":
                    await run_background_with_menu(
                        job_name=f"ops_ingest_source_{source_id}",
                        proxy_message=proxy_message,
                        action_coro=ingest_source(proxy_message, SimpleNamespace(args=str(source_id))),
                    )
                    text, keyboard = await render_ops_sources_page(page=page)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return

            if tokens[0] == "prf":
                prf_action = tokens[1] if len(tokens) >= 2 else ""
                profile_id = parse_positive_int(tokens[2] if len(tokens) >= 3 else None)
                page = parse_positive_int(tokens[3] if len(tokens) >= 4 else "1") or 1
                if profile_id is None:
                    await safe_callback_answer(query, text="Некорректный profile_id")
                    return
                if prf_action == "tgl":
                    async with context.session_factory() as session:
                        async with session.begin():
                            profile = await trend_profiles_repo.get_by_id(session, profile_id)
                            if profile is None:
                                await context.publisher.send_text(
                                    chat_id=proxy_message.chat.id,
                                    topic_id=proxy_message.message_thread_id,
                                    text=f"Профиль #{profile_id} не найден.",
                                )
                                return
                            profile = await trend_profiles_repo.set_enabled(
                                session,
                                profile_id=profile_id,
                                enabled=not bool(profile.enabled),
                            )

                    state = "ON" if profile and profile.enabled else "OFF"
                    await context.publisher.send_text(
                        chat_id=proxy_message.chat.id,
                        topic_id=proxy_message.message_thread_id,
                        text=f"Профиль #{profile_id} переключен: {state}",
                    )
                    text, keyboard = await render_ops_trend_profiles_page(page=page)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return

            if tokens[0] == "tr":
                tr_action = tokens[1] if len(tokens) >= 2 else ""
                if tr_action == "collect":
                    await run_background_with_menu(
                        job_name="ops_collect_trends",
                        proxy_message=proxy_message,
                        action_coro=collect_trends(proxy_message),
                    )
                    await send_or_edit_ops_page(
                        query=query,
                        text="Раздел: Тренды\nCollect trends запущен в фоне.",
                        keyboard=build_ops_trends_keyboard(),
                    )
                    return
                if tr_action == "scan":
                    await run_background_with_menu(
                        job_name="ops_trend_scan",
                        proxy_message=proxy_message,
                        action_coro=trend_scan(proxy_message, SimpleNamespace(args="24 6")),
                    )
                    await send_or_edit_ops_page(
                        query=query,
                        text="Раздел: Тренды\nTrend scan запущен в фоне.",
                        keyboard=build_ops_trends_keyboard(),
                    )
                    return
                if tr_action == "signals":
                    await trends(proxy_message, SimpleNamespace(args="24 20"))
                    return
                if tr_action == "topics":
                    text, keyboard = await render_ops_trend_topics(hours=24, limit=8)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return
                if tr_action == "queue":
                    page = parse_positive_int(tokens[2] if len(tokens) >= 3 else "1") or 1
                    text, keyboard = await render_ops_trend_queue_page(page=page)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return
                if tr_action == "open":
                    topic_id = parse_positive_int(tokens[2] if len(tokens) >= 3 else None)
                    if topic_id is None:
                        await safe_callback_answer(query, text="Некорректный topic_id")
                        return
                    text, keyboard = await render_ops_trend_topic_detail(topic_id)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return
                if tr_action == "ing":
                    candidate_id = parse_positive_int(tokens[2] if len(tokens) >= 3 else None)
                    topic_id = parse_positive_int(tokens[3] if len(tokens) >= 4 else None)
                    if candidate_id is None or topic_id is None:
                        await safe_callback_answer(query, text="Некорректный candidate_id/topic_id")
                        return
                    if context.trend_discovery is None:
                        await context.publisher.send_text(
                            chat_id=proxy_message.chat.id,
                            topic_id=proxy_message.message_thread_id,
                            text="Модуль trend discovery недоступен.",
                        )
                        return
                    result = await context.trend_discovery.ingest_article_candidate(
                        candidate_id=candidate_id,
                        user_id=proxy_message.from_user.id if proxy_message.from_user else 0,
                    )
                    await context.publisher.send_text(
                        chat_id=proxy_message.chat.id,
                        topic_id=proxy_message.message_thread_id,
                        text=result.message,
                    )
                    text, keyboard = await render_ops_trend_topic_detail(topic_id)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return
                if tr_action == "add":
                    candidate_id = parse_positive_int(tokens[2] if len(tokens) >= 3 else None)
                    topic_id = parse_positive_int(tokens[3] if len(tokens) >= 4 else None)
                    if candidate_id is None or topic_id is None:
                        await safe_callback_answer(query, text="Некорректный candidate_id/topic_id")
                        return
                    if context.trend_discovery is None:
                        await context.publisher.send_text(
                            chat_id=proxy_message.chat.id,
                            topic_id=proxy_message.message_thread_id,
                            text="Модуль trend discovery недоступен.",
                        )
                        return
                    result = await context.trend_discovery.add_source_candidate(
                        candidate_id=candidate_id,
                        user_id=proxy_message.from_user.id if proxy_message.from_user else 0,
                    )
                    await context.publisher.send_text(
                        chat_id=proxy_message.chat.id,
                        topic_id=proxy_message.message_thread_id,
                        text=result.message,
                    )
                    text, keyboard = await render_ops_trend_topic_detail(topic_id)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return
                if tr_action == "qing":
                    candidate_id = parse_positive_int(tokens[2] if len(tokens) >= 3 else None)
                    page = parse_positive_int(tokens[3] if len(tokens) >= 4 else "1") or 1
                    if candidate_id is None:
                        await safe_callback_answer(query, text="Некорректный candidate_id")
                        return
                    if context.trend_discovery is None:
                        await context.publisher.send_text(
                            chat_id=proxy_message.chat.id,
                            topic_id=proxy_message.message_thread_id,
                            text="Модуль trend discovery недоступен.",
                        )
                        return
                    result = await context.trend_discovery.ingest_article_candidate(
                        candidate_id=candidate_id,
                        user_id=proxy_message.from_user.id if proxy_message.from_user else 0,
                    )
                    await context.publisher.send_text(
                        chat_id=proxy_message.chat.id,
                        topic_id=proxy_message.message_thread_id,
                        text=result.message,
                    )
                    text, keyboard = await render_ops_trend_queue_page(page=page)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return
                if tr_action == "qrej":
                    candidate_id = parse_positive_int(tokens[2] if len(tokens) >= 3 else None)
                    page = parse_positive_int(tokens[3] if len(tokens) >= 4 else "1") or 1
                    if candidate_id is None:
                        await safe_callback_answer(query, text="Некорректный candidate_id")
                        return
                    if context.trend_discovery is None:
                        await context.publisher.send_text(
                            chat_id=proxy_message.chat.id,
                            topic_id=proxy_message.message_thread_id,
                            text="Модуль trend discovery недоступен.",
                        )
                        return
                    result = await context.trend_discovery.reject_article_candidate(
                        candidate_id=candidate_id,
                        user_id=proxy_message.from_user.id if proxy_message.from_user else 0,
                    )
                    await context.publisher.send_text(
                        chat_id=proxy_message.chat.id,
                        topic_id=proxy_message.message_thread_id,
                        text=result.message,
                    )
                    text, keyboard = await render_ops_trend_queue_page(page=page)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return
                if tr_action == "qadd":
                    candidate_id = parse_positive_int(tokens[2] if len(tokens) >= 3 else None)
                    page = parse_positive_int(tokens[3] if len(tokens) >= 4 else "1") or 1
                    if candidate_id is None:
                        await safe_callback_answer(query, text="Некорректный candidate_id")
                        return
                    if context.trend_discovery is None:
                        await context.publisher.send_text(
                            chat_id=proxy_message.chat.id,
                            topic_id=proxy_message.message_thread_id,
                            text="Модуль trend discovery недоступен.",
                        )
                        return
                    result = await context.trend_discovery.add_source_candidate(
                        candidate_id=candidate_id,
                        user_id=proxy_message.from_user.id if proxy_message.from_user else 0,
                    )
                    await context.publisher.send_text(
                        chat_id=proxy_message.chat.id,
                        topic_id=proxy_message.message_thread_id,
                        text=result.message,
                    )
                    text, keyboard = await render_ops_trend_queue_page(page=page)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return
                if tr_action == "qsrej":
                    candidate_id = parse_positive_int(tokens[2] if len(tokens) >= 3 else None)
                    page = parse_positive_int(tokens[3] if len(tokens) >= 4 else "1") or 1
                    if candidate_id is None:
                        await safe_callback_answer(query, text="Некорректный candidate_id")
                        return
                    if context.trend_discovery is None:
                        await context.publisher.send_text(
                            chat_id=proxy_message.chat.id,
                            topic_id=proxy_message.message_thread_id,
                            text="Модуль trend discovery недоступен.",
                        )
                        return
                    result = await context.trend_discovery.reject_source_candidate(
                        candidate_id=candidate_id,
                        user_id=proxy_message.from_user.id if proxy_message.from_user else 0,
                    )
                    await context.publisher.send_text(
                        chat_id=proxy_message.chat.id,
                        topic_id=proxy_message.message_thread_id,
                        text=result.message,
                    )
                    text, keyboard = await render_ops_trend_queue_page(page=page)
                    await send_or_edit_ops_page(query=query, text=text, keyboard=keyboard)
                    return
        except Exception:
            log.exception("settings.ops_menu_action_failed", action=action)
            await context.publisher.send_text(
                chat_id=proxy_message.chat.id,
                topic_id=proxy_message.message_thread_id,
                text="Ошибка операции из операционного центра. Смотри логи.",
            )
            return

        await safe_callback_answer(query, text="Действие не поддерживается")

    return router
