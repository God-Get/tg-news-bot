from __future__ import annotations

import asyncio
import sys
from contextlib import suppress

from pydantic import ValidationError
from aiogram import Bot, Dispatcher

from telegram_publisher import TelegramPublisher
from tg_news_bot.adapters import PublisherAdapter
from tg_news_bot.config import Settings
from tg_news_bot.db.session import create_session_factory
from tg_news_bot.logging import configure_logging, get_logger
from tg_news_bot.monitoring import configure_sentry
from tg_news_bot.repositories.bot_settings import BotSettingsRepository
from tg_news_bot.repositories.drafts import DraftRepository
from tg_news_bot.repositories.scheduled_posts import ScheduledPostRepository
from tg_news_bot.repositories.sources import SourceRepository
from tg_news_bot.services.analytics import AnalyticsService
from tg_news_bot.services.edit_sessions import EditSessionService
from tg_news_bot.services.health import HealthServer
from tg_news_bot.services.ingestion import IngestionConfig, IngestionRunner
from tg_news_bot.services.quality_gate import QualityGateService
from tg_news_bot.services.schedule_input import ScheduleInputService
from tg_news_bot.services.scheduler import SchedulerConfig, SchedulerRunner
from tg_news_bot.services.text_generation import build_text_pipeline
from tg_news_bot.services.trend_discovery import TrendDiscoveryService
from tg_news_bot.services.trends import TrendCollector
from tg_news_bot.services.workflow import DraftWorkflowService
from tg_news_bot.telegram.handlers.callbacks import CallbackContext, create_callback_router
from tg_news_bot.telegram.handlers.editing import EditContext, create_edit_router
from tg_news_bot.telegram.handlers.schedule_input import (
    ScheduleInputContext,
    create_schedule_input_router,
)
from tg_news_bot.telegram.handlers.settings import SettingsContext, create_settings_router


async def _run() -> int:
    try:
        settings = Settings()
    except ValidationError as exc:
        print("Invalid configuration:", file=sys.stderr)
        print(exc, file=sys.stderr)
        return 2

    configure_logging(settings.log_level)
    configure_sentry(dsn=settings.sentry_dsn)
    log = get_logger(__name__)
    log.info("boot", settings=settings.public_dict())

    session_factory = create_session_factory(settings.database_url)

    bot = Bot(token=settings.bot_token)
    publisher = PublisherAdapter(TelegramPublisher(bot))
    dispatcher = Dispatcher()

    trend_collector = TrendCollector(
        settings=settings.trends,
        session_factory=session_factory,
    )

    ingestion = IngestionRunner(
        settings=settings,
        session_factory=session_factory,
        publisher=publisher,
        config=IngestionConfig(
            poll_interval_seconds=settings.rss.poll_interval_seconds,
            max_items_per_source=settings.rss.max_items_per_source,
        ),
        trend_collector=trend_collector,
    )

    workflow_text_pipeline = build_text_pipeline(
        settings.text_generation,
        settings.llm,
    )
    workflow = DraftWorkflowService(
        session_factory,
        publisher,
        post_formatting=settings.post_formatting,
        text_pipeline=workflow_text_pipeline,
        quality_gate=QualityGateService(settings.quality_gate),
    )
    trend_discovery = TrendDiscoveryService(
        settings=settings,
        session_factory=session_factory,
        publisher=publisher,
        ingestion_runner=ingestion,
    )

    settings_context = SettingsContext(
        settings=settings,
        session_factory=session_factory,
        repository=BotSettingsRepository(),
        source_repository=SourceRepository(),
        publisher=publisher,
        ingestion_runner=ingestion,
        workflow=workflow,
        trend_collector=trend_collector,
        trend_discovery=trend_discovery,
        scheduled_repo=ScheduledPostRepository(),
        draft_repo=DraftRepository(),
        analytics=AnalyticsService(session_factory),
    )
    dispatcher.include_router(create_settings_router(settings_context))

    edit_service = EditSessionService(
        publisher,
        post_formatting=settings.post_formatting,
    )
    edit_context = EditContext(
        settings=settings,
        session_factory=session_factory,
        edit_sessions=edit_service,
        publisher=publisher,
    )
    dispatcher.include_router(create_edit_router(edit_context))

    schedule_input_service = ScheduleInputService(
        session_factory=session_factory,
        workflow=workflow,
        timezone_name=settings.scheduler.timezone,
    )
    schedule_input_context = ScheduleInputContext(
        settings=settings,
        schedule_input=schedule_input_service,
        publisher=publisher,
    )
    dispatcher.include_router(create_schedule_input_router(schedule_input_context))

    callback_context = CallbackContext(
        settings=settings,
        session_factory=session_factory,
        workflow=workflow,
        edit_sessions=edit_service,
        schedule_input=schedule_input_service,
        trend_discovery=trend_discovery,
    )
    dispatcher.include_router(create_callback_router(callback_context))

    scheduler_task = None
    ingestion_task = None
    trend_task = None
    health_server = HealthServer(settings.health)
    await health_server.start()
    if settings.scheduler.enabled:
        scheduler = SchedulerRunner(
            session_factory=session_factory,
            workflow=workflow,
            config=SchedulerConfig(
                poll_interval_seconds=settings.scheduler.poll_interval_seconds,
                batch_size=settings.scheduler.batch_size,
                max_publish_attempts=settings.scheduler.max_publish_attempts,
                retry_backoff_seconds=settings.scheduler.retry_backoff_seconds,
                recover_failed_after_seconds=settings.scheduler.recover_failed_after_seconds,
            ),
        )
        scheduler_task = asyncio.create_task(scheduler.run())

    ingestion_task = asyncio.create_task(ingestion.run())
    if settings.trends.enabled:
        trend_task = asyncio.create_task(trend_collector.run())

    try:
        await dispatcher.start_polling(bot)
    finally:
        if ingestion_task:
            ingestion_task.cancel()
            with suppress(asyncio.CancelledError):
                await ingestion_task
        if scheduler_task:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task
        if trend_task:
            trend_task.cancel()
            with suppress(asyncio.CancelledError):
                await trend_task
        await health_server.stop()
        await bot.session.close()

    return 0


def main() -> int:
    return asyncio.run(_run())
