"""Scheduled publishing loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tg_news_bot.db.models import DraftState, PublishFailureContext, ScheduledPostStatus
from tg_news_bot.logging import get_logger
from tg_news_bot.repositories.bot_settings import BotSettingsRepository
from tg_news_bot.repositories.drafts import DraftRepository
from tg_news_bot.repositories.publish_failures import PublishFailureRepository
from tg_news_bot.repositories.scheduled_posts import ScheduledPostRepository
from tg_news_bot.services.metrics import metrics
from tg_news_bot.services.workflow import DraftWorkflowService


@dataclass(slots=True)
class SchedulerConfig:
    poll_interval_seconds: int = 10
    batch_size: int = 20
    max_publish_attempts: int = 3
    retry_backoff_seconds: int = 60
    recover_failed_after_seconds: int = 300


class SchedulerRunner:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        workflow: DraftWorkflowService,
        config: SchedulerConfig,
        scheduled_repo: ScheduledPostRepository | None = None,
        draft_repo: DraftRepository | None = None,
        settings_repo: BotSettingsRepository | None = None,
        publish_failure_repo: PublishFailureRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._workflow = workflow
        self._config = config
        self._scheduled_repo = scheduled_repo or ScheduledPostRepository()
        self._draft_repo = draft_repo or DraftRepository()
        self._settings_repo = settings_repo or BotSettingsRepository()
        self._publish_failure_repo = publish_failure_repo or PublishFailureRepository()
        self._log = get_logger(__name__)

    async def run(self) -> None:
        while True:
            try:
                await self._process_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.exception("scheduler.loop_error")
            await asyncio.sleep(self._config.poll_interval_seconds)

    async def _process_due(self) -> None:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            async with session.begin():
                await self._recover_failed_jobs(session, now=now)
                pending = await self._scheduled_repo.count_pending(session)
                metrics.set_gauge("scheduler_jobs_pending", pending)
                due = await self._scheduled_repo.list_due_for_update(
                    session,
                    now=now,
                    limit=self._config.batch_size,
                )
                if not due:
                    return
                settings = await self._settings_repo.get_or_create(session)

                for scheduled in due:
                    draft = await self._draft_repo.get_for_update(session, scheduled.draft_id)
                    if draft.state != DraftState.SCHEDULED:
                        scheduled.status = ScheduledPostStatus.CANCELLED
                        scheduled.next_retry_at = None
                        continue

                    try:
                        if not draft.published_message_id:
                            await self._workflow._publish_now(session, draft, settings)
                        await self._workflow._move_in_group(
                            session=session,
                            draft=draft,
                            settings=settings,
                            target_state=DraftState.PUBLISHED,
                        )
                        draft.state = DraftState.PUBLISHED
                        scheduled.status = ScheduledPostStatus.PUBLISHED
                        scheduled.last_error = None
                        scheduled.next_retry_at = None
                        await self._publish_failure_repo.mark_resolved_for_draft(
                            session,
                            draft_id=draft.id,
                        )
                        metrics.inc_counter("scheduler_publish_success_total")
                    except Exception:
                        scheduled.attempts = int(scheduled.attempts or 0) + 1
                        scheduled.last_error = "publish_failed"
                        if scheduled.attempts < self._config.max_publish_attempts:
                            scheduled.status = ScheduledPostStatus.FAILED
                            scheduled.next_retry_at = now + timedelta(
                                seconds=self._config.retry_backoff_seconds
                                * (2 ** (scheduled.attempts - 1))
                            )
                            metrics.inc_counter("scheduler_retries_total")
                        else:
                            scheduled.status = ScheduledPostStatus.FAILED
                            scheduled.next_retry_at = None
                            metrics.inc_counter("scheduler_dlq_total")
                        await self._publish_failure_repo.create(
                            session,
                            draft_id=draft.id,
                            scheduled_post_id=scheduled.id,
                            context=PublishFailureContext.SCHEDULED,
                            error_message="publish_failed",
                            attempt_no=scheduled.attempts,
                            details={
                                "schedule_at": scheduled.schedule_at.isoformat(),
                                "next_retry_at": (
                                    scheduled.next_retry_at.isoformat()
                                    if scheduled.next_retry_at
                                    else None
                                ),
                            },
                        )
                        metrics.inc_counter("publish_fail_total")
                        self._log.exception(
                            "scheduler.publish_failed",
                            draft_id=draft.id,
                            attempt=scheduled.attempts,
                            next_retry_at=(
                                scheduled.next_retry_at.isoformat()
                                if scheduled.next_retry_at
                                else None
                            ),
                        )

    async def _recover_failed_jobs(self, session: AsyncSession, *, now: datetime) -> None:
        recover_from = now - timedelta(seconds=self._config.recover_failed_after_seconds)
        failed = await self._scheduled_repo.list_failed_without_retry_for_update(
            session,
            now=recover_from,
            limit=self._config.batch_size,
        )
        for item in failed:
            if int(item.attempts or 0) >= self._config.max_publish_attempts:
                continue
            item.next_retry_at = now
            metrics.inc_counter("scheduler_recovered_total")
