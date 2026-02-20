from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tg_news_bot.telegram.handlers.settings import SettingsContext, create_settings_router


@dataclass
class _PublisherSpy:
    sent: list[dict] = field(default_factory=list)

    async def send_text(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        text: str,
        parse_mode=None,  # noqa: ANN001
        keyboard=None,  # noqa: ANN001
    ) -> None:
        self.sent.append(
            {
                "chat_id": chat_id,
                "topic_id": topic_id,
                "text": text,
            }
        )


@dataclass
class _IngestionRunnerSpy:
    result: object | None = None
    calls: list[dict] = field(default_factory=list)

    async def ingest_url(self, *, url: str, source_id=None, topic_hints=None):  # noqa: ANN001
        self.calls.append(
            {
                "url": url,
                "source_id": source_id,
                "topic_hints": topic_hints,
            }
        )
        return self.result


@dataclass
class _TrendDiscoverySpy:
    scan_result: object | None = None
    topic_rows: list[object] = field(default_factory=list)
    article_rows: list[object] = field(default_factory=list)
    source_rows: list[object] = field(default_factory=list)
    scan_calls: list[dict] = field(default_factory=list)
    ingest_calls: list[int] = field(default_factory=list)
    add_source_calls: list[int] = field(default_factory=list)

    async def scan(self, *, hours=None, limit=None):  # noqa: ANN001
        self.scan_calls.append({"hours": hours, "limit": limit})
        return self.scan_result

    async def list_topics(self, *, hours: int, limit: int):  # noqa: ANN001
        return list(self.topic_rows)

    async def list_articles(self, *, topic_id: int, limit: int):  # noqa: ANN001
        return list(self.article_rows[:limit])

    async def list_sources(self, *, topic_id: int, limit: int):  # noqa: ANN001
        return list(self.source_rows[:limit])

    async def ingest_article_candidate(self, *, candidate_id: int, user_id: int):  # noqa: ANN001
        self.ingest_calls.append(candidate_id)
        return SimpleNamespace(ok=True, message=f"ingested {candidate_id}")

    async def add_source_candidate(self, *, candidate_id: int, user_id: int):  # noqa: ANN001
        self.add_source_calls.append(candidate_id)
        return SimpleNamespace(ok=True, message=f"source {candidate_id}")


@dataclass
class _AutoPlanSpy:
    rules: object = field(
        default_factory=lambda: SimpleNamespace(
            timezone_name="UTC",
            min_gap_minutes=90,
            max_posts_per_day=6,
            quiet_start_hour=23,
            quiet_end_hour=8,
            slot_step_minutes=30,
            horizon_hours=24,
        )
    )
    preview_result: object | None = None
    apply_result: object | None = None
    preview_calls: list[dict] = field(default_factory=list)
    apply_calls: list[dict] = field(default_factory=list)
    set_rules_calls: list[object] = field(default_factory=list)

    async def get_rules(self):  # noqa: ANN001
        return self.rules

    async def set_rules(self, rules):  # noqa: ANN001
        self.rules = rules
        self.set_rules_calls.append(rules)
        return rules

    async def preview(self, *, hours=None, limit=None):  # noqa: ANN001
        self.preview_calls.append({"hours": hours, "limit": limit})
        return self.preview_result

    async def apply(self, *, user_id: int, hours=None, limit=None):  # noqa: ANN001
        self.apply_calls.append(
            {
                "user_id": user_id,
                "hours": hours,
                "limit": limit,
            }
        )
        return self.apply_result


@dataclass
class _Message:
    user_id: int = 10
    chat_id: int = -1001
    topic_id: int | None = 7

    @property
    def from_user(self):
        return SimpleNamespace(id=self.user_id)

    @property
    def chat(self):
        return SimpleNamespace(id=self.chat_id)

    @property
    def message_thread_id(self):
        return self.topic_id


class _DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False

    def begin(self):
        return self

    async def flush(self) -> None:
        return None


def _dummy_session_factory():
    return _DummySession()


@dataclass
class _SourceRepositorySpy:
    rows: list[object] = field(default_factory=list)
    by_id: dict[int, object] = field(default_factory=dict)

    async def list_all(self, session):  # noqa: ANN001
        return list(self.rows)

    async def get_by_id(self, session, source_id: int):  # noqa: ANN001
        return self.by_id.get(source_id)


@dataclass
class _ScheduledRepoSpy:
    rows: list[object] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    async def list_upcoming(self, session, *, now, until=None, limit=50):  # noqa: ANN001
        self.calls.append({"now": now, "until": until, "limit": limit})
        return list(self.rows[:limit])


@dataclass
class _DraftRepoSpy:
    by_id: dict[int, object] = field(default_factory=dict)

    async def get(self, session, draft_id: int):  # noqa: ANN001
        return self.by_id.get(draft_id)


def _router_and_handler_by_name(
    name: str,
    *,
    publisher: _PublisherSpy,
    ingestion: _IngestionRunnerSpy,
    trend_discovery: _TrendDiscoverySpy | None = None,
    autoplan: _AutoPlanSpy | None = None,
    scheduled_repo=None,  # noqa: ANN001
    draft_repo=None,  # noqa: ANN001
    session_factory=None,  # noqa: ANN001
):
    context = SettingsContext(
        settings=SimpleNamespace(
            admin_user_id=10,
            trend_discovery=SimpleNamespace(default_window_hours=24, mode="suggest"),
            analytics=SimpleNamespace(default_window_hours=24, max_window_hours=240),
            post_formatting=SimpleNamespace(hashtag_mode="both"),
            scheduler=SimpleNamespace(timezone="UTC"),
            internet_scoring=SimpleNamespace(enabled=True),
        ),
        session_factory=session_factory or SimpleNamespace(),
        repository=SimpleNamespace(),
        source_repository=SimpleNamespace(),
        publisher=publisher,
        ingestion_runner=ingestion,
        workflow=SimpleNamespace(),
        trend_discovery=trend_discovery,
        autoplan=autoplan,
        scheduled_repo=scheduled_repo,
        draft_repo=draft_repo,
    )
    router = create_settings_router(context)
    for handler in router.message.handlers:
        if handler.callback.__name__ == name:
            return router, handler.callback
    raise AssertionError(f"handler not found: {name}")


@pytest.mark.asyncio
async def test_commands_help_contains_syntax_lines() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    _, handler = _router_and_handler_by_name(
        "commands_help",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message())

    assert publisher.sent
    text = "\n".join(item["text"] for item in publisher.sent)
    assert "/commands" in text
    assert "/ingest_url <article_url> [source_id]" in text
    assert "/process_range <from_id> <to_id>" in text
    assert "/scheduled_failed_list [limit]" in text
    assert "/schedule_map [hours] [limit]" in text
    assert "/autoplan_rules" in text
    assert "/set_autoplan_rules <min_gap_minutes> <max_posts_per_day>" in text
    assert "/autoplan_preview [hours] [limit]" in text
    assert "/autoplan_apply [hours] [limit]" in text
    assert "/analytics [hours]" in text
    assert "/trend_scan [hours] [limit]" in text
    assert "/trend_profile_add <name>|<seed_csv>" in text
    assert "/trend_theme_add <name>|<seed_csv>" in text
    assert "/trend_theme_list [all]" in text
    assert "/cancel" in text
    assert "Синтаксис:" in text
    assert "Что делает:" in text


@pytest.mark.asyncio
async def test_commands_help_pages_are_within_safe_telegram_size() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    _, handler = _router_and_handler_by_name(
        "commands_help",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message())

    assert publisher.sent
    for item in publisher.sent:
        assert len(item["text"]) <= 3900


@pytest.mark.asyncio
async def test_commands_help_lists_all_router_commands() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    router, handler = _router_and_handler_by_name(
        "commands_help",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message())

    assert publisher.sent
    text = "\n".join(item["text"] for item in publisher.sent)
    command_names: set[str] = set()
    for handler_obj in router.message.handlers:
        for filter_obj in handler_obj.filters:
            command_filter = getattr(filter_obj, "callback", None)
            names = getattr(command_filter, "commands", None)
            if not names:
                continue
            for name in names:
                command_names.add(str(name).strip().lstrip("/").lower())

    for command_name in command_names:
        assert f"/{command_name}" in text


@pytest.mark.asyncio
async def test_ingest_url_creates_draft_message() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy(
        result=SimpleNamespace(
            created=True,
            draft_id=321,
            normalized_url="https://example.com/news/1",
            reason=None,
        )
    )
    _, handler = _router_and_handler_by_name(
        "ingest_url",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message(), SimpleNamespace(args="https://example.com/news/1"))

    assert ingestion.calls == [
        {
            "url": "https://example.com/news/1",
            "source_id": None,
            "topic_hints": None,
        }
    ]
    assert len(publisher.sent) == 2
    assert "ссылке" in publisher.sent[0]["text"].lower()
    assert "Draft #321" in publisher.sent[1]["text"]


@pytest.mark.asyncio
async def test_ingest_url_reports_duplicate() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy(
        result=SimpleNamespace(
            created=False,
            draft_id=None,
            normalized_url="https://example.com/news/1",
            reason="duplicate",
        )
    )
    _, handler = _router_and_handler_by_name(
        "ingest_url",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message(), SimpleNamespace(args="https://example.com/news/1"))

    assert len(publisher.sent) == 2
    assert "дубликат" in publisher.sent[1]["text"].lower()


@pytest.mark.asyncio
async def test_ingest_url_rejects_invalid_url() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    _, handler = _router_and_handler_by_name(
        "ingest_url",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message(), SimpleNamespace(args="not-a-url"))

    assert ingestion.calls == []
    assert len(publisher.sent) == 1
    assert "url" in publisher.sent[0]["text"].lower()


@pytest.mark.asyncio
async def test_ingest_url_accepts_optional_source_id() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy(
        result=SimpleNamespace(
            created=True,
            draft_id=222,
            normalized_url="https://example.com/news/2",
            reason=None,
        )
    )
    _, handler = _router_and_handler_by_name(
        "ingest_url",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message(), SimpleNamespace(args="https://example.com/news/2 3"))

    assert ingestion.calls == [
        {
            "url": "https://example.com/news/2",
            "source_id": 3,
            "topic_hints": None,
        }
    ]
    assert "source #3" in publisher.sent[0]["text"]


@pytest.mark.asyncio
async def test_trend_scan_invokes_service_and_renders_summary() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    trend_discovery = _TrendDiscoverySpy(
        scan_result=SimpleNamespace(
            mode="suggest",
            scanned_items=20,
            topics_created=3,
            article_candidates=7,
            source_candidates=4,
            announced_messages=11,
            auto_ingested=0,
            auto_sources_added=0,
        )
    )
    _, handler = _router_and_handler_by_name(
        "trend_scan",
        publisher=publisher,
        ingestion=ingestion,
        trend_discovery=trend_discovery,
    )

    await handler(_Message(), SimpleNamespace(args="12 5"))

    assert trend_discovery.scan_calls == [{"hours": 12, "limit": 5}]
    assert len(publisher.sent) == 2
    assert "сканирование трендов" in publisher.sent[0]["text"].lower()
    assert "создано тем: 3" in publisher.sent[1]["text"]
    assert "кандидатов статей: 7" in publisher.sent[1]["text"]


@pytest.mark.asyncio
async def test_set_hashtag_mode_updates_runtime_formatting() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    _, handler = _router_and_handler_by_name(
        "set_hashtag_mode",
        publisher=publisher,
        ingestion=ingestion,
    )

    message = _Message()
    await handler(message, SimpleNamespace(args="ru"))

    assert publisher.sent
    assert publisher.sent[-1]["text"] == "Режим хэштегов обновлён: ru"


@pytest.mark.asyncio
async def test_trend_ingest_forwards_candidate_to_service() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    trend_discovery = _TrendDiscoverySpy()
    _, handler = _router_and_handler_by_name(
        "trend_ingest",
        publisher=publisher,
        ingestion=ingestion,
        trend_discovery=trend_discovery,
    )

    await handler(_Message(), SimpleNamespace(args="42"))

    assert trend_discovery.ingest_calls == [42]
    assert publisher.sent[-1]["text"] == "ingested 42"


@pytest.mark.asyncio
async def test_list_sources_splits_large_response_into_pages() -> None:
    publisher = _PublisherSpy()
    rows = []
    for idx in range(1, 180):
        rows.append(
            SimpleNamespace(
                id=idx,
                enabled=bool(idx % 2),
                trust_score=idx / 100.0,
                name=f"Source {idx} {'x' * 30}",
                url=f"https://example.com/feeds/{idx}/{'y' * 40}",
                tags={
                    "topics": ["ai", "science", "space"],
                    "allow_insecure_ssl": bool(idx % 3 == 0),
                    "quality": {"events_total": idx * 2},
                },
            )
        )

    context = SettingsContext(
        settings=SimpleNamespace(
            admin_user_id=10,
            trend_discovery=SimpleNamespace(default_window_hours=24, mode="suggest"),
            analytics=SimpleNamespace(default_window_hours=24, max_window_hours=240),
            post_formatting=SimpleNamespace(hashtag_mode="both"),
        ),
        session_factory=_dummy_session_factory,
        repository=SimpleNamespace(),
        source_repository=_SourceRepositorySpy(rows),
        publisher=publisher,
        ingestion_runner=_IngestionRunnerSpy(),
        workflow=SimpleNamespace(),
    )
    router = create_settings_router(context)
    handler = None
    for item in router.message.handlers:
        if item.callback.__name__ == "list_sources":
            handler = item.callback
            break
    assert handler is not None

    await handler(_Message())

    assert len(publisher.sent) >= 2
    assert publisher.sent[0]["text"].startswith("Источники:")
    for sent in publisher.sent:
        assert len(sent["text"]) <= 3500


@pytest.mark.asyncio
async def test_enable_source_accepts_comma_and_space_separated_ids() -> None:
    publisher = _PublisherSpy()
    source_1 = SimpleNamespace(id=1, enabled=False)
    source_2 = SimpleNamespace(id=2, enabled=False)
    source_5 = SimpleNamespace(id=5, enabled=False)

    context = SettingsContext(
        settings=SimpleNamespace(
            admin_user_id=10,
            trend_discovery=SimpleNamespace(default_window_hours=24, mode="suggest"),
            analytics=SimpleNamespace(default_window_hours=24, max_window_hours=240),
            post_formatting=SimpleNamespace(hashtag_mode="both"),
        ),
        session_factory=_dummy_session_factory,
        repository=SimpleNamespace(),
        source_repository=_SourceRepositorySpy(
            by_id={1: source_1, 2: source_2, 5: source_5}
        ),
        publisher=publisher,
        ingestion_runner=_IngestionRunnerSpy(),
        workflow=SimpleNamespace(),
    )
    router = create_settings_router(context)
    handler = None
    for item in router.message.handlers:
        if item.callback.__name__ == "enable_source":
            handler = item.callback
            break
    assert handler is not None

    await handler(_Message(), SimpleNamespace(args="1, 2 5,999"))

    assert source_1.enabled is True
    assert source_2.enabled is True
    assert source_5.enabled is True
    assert publisher.sent
    assert "Включено источников: 3" in publisher.sent[-1]["text"]
    assert "ID: #1, #2, #5" in publisher.sent[-1]["text"]
    assert "Не найдены: #999" in publisher.sent[-1]["text"]


@pytest.mark.asyncio
async def test_enable_source_rejects_invalid_list() -> None:
    publisher = _PublisherSpy()
    context = SettingsContext(
        settings=SimpleNamespace(
            admin_user_id=10,
            trend_discovery=SimpleNamespace(default_window_hours=24, mode="suggest"),
            analytics=SimpleNamespace(default_window_hours=24, max_window_hours=240),
            post_formatting=SimpleNamespace(hashtag_mode="both"),
        ),
        session_factory=_dummy_session_factory,
        repository=SimpleNamespace(),
        source_repository=_SourceRepositorySpy(),
        publisher=publisher,
        ingestion_runner=_IngestionRunnerSpy(),
        workflow=SimpleNamespace(),
    )
    router = create_settings_router(context)
    handler = None
    for item in router.message.handlers:
        if item.callback.__name__ == "enable_source":
            handler = item.callback
            break
    assert handler is not None

    await handler(_Message(), SimpleNamespace(args="1,abc"))

    assert publisher.sent
    assert "source_id должны быть числами" in publisher.sent[-1]["text"]


@pytest.mark.asyncio
async def test_schedule_map_renders_upcoming_publications() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    scheduled_repo = _ScheduledRepoSpy(
        rows=[
            SimpleNamespace(
                draft_id=205,
                schedule_at=datetime(2026, 2, 20, 20, 0, tzinfo=timezone.utc),
            ),
            SimpleNamespace(
                draft_id=175,
                schedule_at=datetime(2026, 2, 20, 21, 30, tzinfo=timezone.utc),
            ),
        ]
    )
    draft_repo = _DraftRepoSpy(
        by_id={
            205: SimpleNamespace(
                id=205,
                state=SimpleNamespace(value="SCHEDULED"),
                score=10.48,
                title_en="NVIDIA launches new AI infra program",
            ),
            175: SimpleNamespace(
                id=175,
                state=SimpleNamespace(value="SCHEDULED"),
                score=8.98,
                title_en="Extreme heat increases strength of pure metals",
            ),
        }
    )
    _, handler = _router_and_handler_by_name(
        "schedule_map",
        publisher=publisher,
        ingestion=ingestion,
        scheduled_repo=scheduled_repo,
        draft_repo=draft_repo,
        session_factory=_dummy_session_factory,
    )

    await handler(_Message(), SimpleNamespace(args="24 10"))

    assert scheduled_repo.calls
    assert scheduled_repo.calls[0]["limit"] == 10
    assert publisher.sent
    text = "\n".join(item["text"] for item in publisher.sent)
    assert "Карта публикаций" in text
    assert "Draft #205" in text
    assert "Draft #175" in text


@pytest.mark.asyncio
async def test_schedule_map_reports_empty_window() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    scheduled_repo = _ScheduledRepoSpy(rows=[])
    draft_repo = _DraftRepoSpy()
    _, handler = _router_and_handler_by_name(
        "schedule_map",
        publisher=publisher,
        ingestion=ingestion,
        scheduled_repo=scheduled_repo,
        draft_repo=draft_repo,
        session_factory=_dummy_session_factory,
    )

    await handler(_Message(), SimpleNamespace(args="24 10"))

    assert publisher.sent
    assert "Нет отложенных публикаций" in publisher.sent[-1]["text"]


@pytest.mark.asyncio
async def test_autoplan_preview_uses_service_and_renders_plan() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    autoplan = _AutoPlanSpy(
        preview_result=SimpleNamespace(
            window_hours=24,
            rules=SimpleNamespace(
                timezone_name="UTC",
                min_gap_minutes=120,
                max_posts_per_day=6,
                quiet_start_hour=23,
                quiet_end_hour=8,
                slot_step_minutes=30,
            ),
            considered_count=3,
            scheduled=[
                SimpleNamespace(
                    draft_id=55,
                    schedule_at=datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc),
                    priority=7.4,
                )
            ],
            unscheduled=[56],
        )
    )
    _, handler = _router_and_handler_by_name(
        "autoplan_preview",
        publisher=publisher,
        ingestion=ingestion,
        autoplan=autoplan,
    )

    await handler(_Message(), SimpleNamespace(args="24 5"))

    assert autoplan.preview_calls == [{"hours": 24, "limit": 5}]
    assert publisher.sent
    rendered = "\n".join(item["text"] for item in publisher.sent)
    assert "Smart Scheduler preview" in rendered
    assert "Draft #55" in rendered


@pytest.mark.asyncio
async def test_set_autoplan_rules_updates_service() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    autoplan = _AutoPlanSpy()
    _, handler = _router_and_handler_by_name(
        "set_autoplan_rules",
        publisher=publisher,
        ingestion=ingestion,
        autoplan=autoplan,
    )

    await handler(_Message(), SimpleNamespace(args="120 6 23 8 30 24"))

    assert len(autoplan.set_rules_calls) == 1
    saved = autoplan.set_rules_calls[0]
    assert saved.min_gap_minutes == 120
    assert saved.max_posts_per_day == 6
    assert saved.quiet_start_hour == 23
    assert saved.quiet_end_hour == 8
    assert saved.slot_step_minutes == 30
    assert saved.horizon_hours == 24
    assert publisher.sent
    assert "Правила Smart Scheduler обновлены" in publisher.sent[-1]["text"]


@pytest.mark.asyncio
async def test_autoplan_apply_forwards_to_service() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    autoplan = _AutoPlanSpy(
        apply_result=SimpleNamespace(
            preview=SimpleNamespace(
                window_hours=24,
                considered_count=4,
                scheduled=[
                    SimpleNamespace(
                        draft_id=71,
                        schedule_at=datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc),
                        priority=7.1,
                    )
                ],
                unscheduled=[72],
                rules=SimpleNamespace(
                    timezone_name="UTC",
                    min_gap_minutes=90,
                    max_posts_per_day=6,
                    quiet_start_hour=23,
                    quiet_end_hour=8,
                    slot_step_minutes=30,
                ),
            ),
            scheduled_count=1,
            failed_drafts=[],
        )
    )
    _, handler = _router_and_handler_by_name(
        "autoplan_apply",
        publisher=publisher,
        ingestion=ingestion,
        autoplan=autoplan,
    )

    await handler(_Message(), SimpleNamespace(args="12 3"))

    assert autoplan.apply_calls == [{"user_id": 10, "hours": 12, "limit": 3}]
    assert publisher.sent
    assert "Запускаю Smart Scheduler apply" in publisher.sent[0]["text"]
    assert "Успешно переведено в SCHEDULED: 1" in publisher.sent[1]["text"]
