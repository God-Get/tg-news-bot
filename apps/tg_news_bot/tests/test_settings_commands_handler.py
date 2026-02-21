from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tg_news_bot.ports.publisher import PublisherNotModified
from tg_news_bot.telegram.handlers.settings import SettingsContext, create_settings_router


@dataclass
class _PublisherSpy:
    sent: list[dict] = field(default_factory=list)
    edits: list[dict] = field(default_factory=list)
    deleted: list[dict] = field(default_factory=list)
    next_message_id: int = 1000
    raise_not_modified_on_edit: bool = False

    async def send_text(
        self,
        *,
        chat_id: int,
        topic_id: int | None,
        text: str,
        parse_mode=None,  # noqa: ANN001
        keyboard=None,  # noqa: ANN001
    ):
        self.next_message_id += 1
        self.sent.append(
            {
                "chat_id": chat_id,
                "topic_id": topic_id,
                "text": text,
                "keyboard": keyboard,
                "message_id": self.next_message_id,
            }
        )
        return SimpleNamespace(chat_id=chat_id, message_id=self.next_message_id)

    async def edit_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        keyboard=None,  # noqa: ANN001
        parse_mode=None,  # noqa: ANN001
        disable_web_page_preview: bool = False,
    ) -> None:
        if self.raise_not_modified_on_edit:
            raise PublisherNotModified("message is not modified")
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "keyboard": keyboard,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_web_page_preview,
            }
        )

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        self.deleted.append({"chat_id": chat_id, "message_id": message_id})


@dataclass
class _IngestionRunnerSpy:
    result: object | None = None
    run_once_result: object | None = None
    calls: list[dict] = field(default_factory=list)
    run_once_calls: list[set[int] | None] = field(default_factory=list)

    async def ingest_url(self, *, url: str, source_id=None, topic_hints=None):  # noqa: ANN001
        self.calls.append(
            {
                "url": url,
                "source_id": source_id,
                "topic_hints": topic_hints,
            }
        )
        return self.result

    async def run_once(self, *, source_ids=None):  # noqa: ANN001
        self.run_once_calls.append(source_ids)
        if self.run_once_result is not None:
            return self.run_once_result
        return SimpleNamespace(
            sources_total=1,
            entries_total=1,
            created=1,
            duplicates=0,
            skipped_low_score=0,
            skipped_invalid_entry=0,
            skipped_no_html=0,
            skipped_unsafe=0,
            skipped_blocked=0,
            skipped_rate_limited=0,
            rss_fetch_errors=0,
        )


@dataclass
class _TrendDiscoverySpy:
    scan_result: object | None = None
    topic_rows: list[object] = field(default_factory=list)
    article_rows: list[object] = field(default_factory=list)
    source_rows: list[object] = field(default_factory=list)
    scan_calls: list[dict] = field(default_factory=list)
    ingest_calls: list[int] = field(default_factory=list)
    add_source_calls: list[int] = field(default_factory=list)
    reject_article_calls: list[int] = field(default_factory=list)
    reject_source_calls: list[int] = field(default_factory=list)

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

    async def reject_article_candidate(self, *, candidate_id: int, user_id: int):  # noqa: ANN001
        self.reject_article_calls.append(candidate_id)
        return SimpleNamespace(ok=True, message=f"reject article {candidate_id}")

    async def reject_source_candidate(self, *, candidate_id: int, user_id: int):  # noqa: ANN001
        self.reject_source_calls.append(candidate_id)
        return SimpleNamespace(ok=True, message=f"reject source {candidate_id}")


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


@dataclass
class _CallbackQuery:
    data: str
    user_id: int = 10
    chat_id: int = -1001
    topic_id: int | None = 7
    message_id: int = 4001
    has_message: bool = True
    answers: list[str | None] = field(default_factory=list)

    @property
    def from_user(self):
        return SimpleNamespace(id=self.user_id)

    @property
    def message(self):
        if not self.has_message:
            return None
        return SimpleNamespace(
            chat=SimpleNamespace(id=self.chat_id),
            message_thread_id=self.topic_id,
            message_id=self.message_id,
        )

    async def answer(self, text: str | None = None) -> None:
        self.answers.append(text)


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
class _TrendProfileRow:
    id: int
    name: str
    enabled: bool
    seed_keywords: list[str]
    min_article_score: float


@dataclass
class _TrendProfileRepositorySpy:
    rows: list[_TrendProfileRow] = field(default_factory=list)

    async def list_all(self, session):  # noqa: ANN001
        return list(self.rows)

    async def get_by_id(self, session, profile_id: int):  # noqa: ANN001
        for row in self.rows:
            if row.id == profile_id:
                return row
        return None

    async def set_enabled(self, session, *, profile_id: int, enabled: bool):  # noqa: ANN001
        row = await self.get_by_id(session, profile_id)
        if row is None:
            return None
        row.enabled = bool(enabled)
        return row


@dataclass
class _TrendArticleCandidateRow:
    id: int
    score: float
    title: str | None
    url: str


@dataclass
class _TrendSourceCandidateRow:
    id: int
    score: float
    domain: str


@dataclass
class _TrendCandidateRepositorySpy:
    article_rows: list[_TrendArticleCandidateRow] = field(default_factory=list)
    source_rows: list[_TrendSourceCandidateRow] = field(default_factory=list)

    async def count_pending_article_candidates(self, session):  # noqa: ANN001
        return len(self.article_rows)

    async def count_pending_source_candidates(self, session):  # noqa: ANN001
        return len(self.source_rows)

    async def list_pending_article_candidates(self, session, *, limit: int, offset: int):  # noqa: ANN001
        return list(self.article_rows[offset : offset + limit])

    async def list_pending_source_candidates(self, session, *, limit: int, offset: int):  # noqa: ANN001
        return list(self.source_rows[offset : offset + limit])


@dataclass
class _BotSettingsRow:
    group_chat_id: int | None = None
    inbox_topic_id: int | None = None
    editing_topic_id: int | None = None
    ready_topic_id: int | None = None
    scheduled_topic_id: int | None = None
    published_topic_id: int | None = None
    archive_topic_id: int | None = None
    trend_candidates_topic_id: int | None = None
    channel_id: int | None = None
    autoplan_rules: dict | None = None


@dataclass
class _BotSettingsRepositorySpy:
    row: _BotSettingsRow = field(default_factory=_BotSettingsRow)

    async def get_or_create(self, session):  # noqa: ANN001
        return self.row

    async def get(self, session):  # noqa: ANN001
        return self.row


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
    repository=None,  # noqa: ANN001
    trend_profile_repository=None,  # noqa: ANN001
    trend_candidates_repository=None,  # noqa: ANN001
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
        repository=repository or _BotSettingsRepositorySpy(),
        source_repository=SimpleNamespace(),
        publisher=publisher,
        ingestion_runner=ingestion,
        workflow=SimpleNamespace(),
        trend_discovery=trend_discovery,
        autoplan=autoplan,
        scheduled_repo=scheduled_repo,
        draft_repo=draft_repo,
        trend_profile_repository=trend_profile_repository,
        trend_candidates_repository=trend_candidates_repository,
    )
    router = create_settings_router(context)
    for handler in router.message.handlers:
        if handler.callback.__name__ == name:
            return router, handler.callback
    raise AssertionError(f"handler not found: {name}")


def _router_and_callback_handler(
    *,
    publisher: _PublisherSpy,
    ingestion: _IngestionRunnerSpy,
    trend_discovery: _TrendDiscoverySpy | None = None,
    source_repository=None,  # noqa: ANN001
    session_factory=None,  # noqa: ANN001
    repository=None,  # noqa: ANN001
    trend_profile_repository=None,  # noqa: ANN001
    trend_candidates_repository=None,  # noqa: ANN001
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
        session_factory=session_factory or _dummy_session_factory,
        repository=repository or _BotSettingsRepositorySpy(),
        source_repository=source_repository or _SourceRepositorySpy(),
        publisher=publisher,
        ingestion_runner=ingestion,
        workflow=SimpleNamespace(),
        trend_discovery=trend_discovery,
        trend_profile_repository=trend_profile_repository,
        trend_candidates_repository=trend_candidates_repository,
    )
    router = create_settings_router(context)
    for handler in router.callback_query.handlers:
        if handler.callback.__name__ == "ops_menu_action":
            return router, handler.callback
    raise AssertionError("callback handler not found: ops_menu_action")


def _message_handler(router, name: str):  # noqa: ANN001
    for handler in router.message.handlers:
        if handler.callback.__name__ == name:
            return handler.callback
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
    assert "/setup_ui" in text
    assert "/menu" in text
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
    assert "/source_health [source_id]" in text
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
async def test_menu_sends_operational_center_with_keyboard() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    _, handler = _router_and_handler_by_name(
        "menu",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message())

    assert len(publisher.sent) == 1
    assert "Операционный центр" in publisher.sent[0]["text"]
    assert publisher.sent[0]["keyboard"] is not None


@pytest.mark.asyncio
async def test_menu_reopens_without_old_tail_message() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    _, handler = _router_and_handler_by_name(
        "menu",
        publisher=publisher,
        ingestion=ingestion,
    )

    message = _Message(chat_id=-1001, topic_id=7)
    await handler(message)
    first_menu_message_id = publisher.sent[-1]["message_id"]

    await handler(message)

    assert len(publisher.sent) == 2
    assert publisher.deleted == [{"chat_id": -1001, "message_id": first_menu_message_id}]


@pytest.mark.asyncio
async def test_list_sources_reopens_menu_to_bottom_when_menu_active() -> None:
    publisher = _PublisherSpy()
    source_rows = [
        SimpleNamespace(
            id=1,
            enabled=True,
            trust_score=1.0,
            name="Source 1",
            url="https://example.com/rss",
            tags={"topics": ["ai"]},
        )
    ]
    context = SettingsContext(
        settings=SimpleNamespace(
            admin_user_id=10,
            trend_discovery=SimpleNamespace(default_window_hours=24, mode="suggest"),
            analytics=SimpleNamespace(default_window_hours=24, max_window_hours=240),
            post_formatting=SimpleNamespace(hashtag_mode="both"),
            scheduler=SimpleNamespace(timezone="UTC"),
            internet_scoring=SimpleNamespace(enabled=True),
        ),
        session_factory=_dummy_session_factory,
        repository=_BotSettingsRepositorySpy(),
        source_repository=_SourceRepositorySpy(rows=source_rows),
        publisher=publisher,
        ingestion_runner=_IngestionRunnerSpy(),
        workflow=SimpleNamespace(),
    )
    router = create_settings_router(context)
    menu_handler = _message_handler(router, "menu")
    list_sources_handler = _message_handler(router, "list_sources")

    message = _Message(chat_id=-1001, topic_id=7)
    await menu_handler(message)
    await list_sources_handler(message)

    assert len(publisher.sent) >= 3
    assert publisher.sent[-1]["text"].startswith("Операционный центр")


@pytest.mark.asyncio
async def test_ingest_now_reopens_menu_to_bottom_when_menu_active() -> None:
    publisher = _PublisherSpy()
    context = SettingsContext(
        settings=SimpleNamespace(
            admin_user_id=10,
            trend_discovery=SimpleNamespace(default_window_hours=24, mode="suggest"),
            analytics=SimpleNamespace(default_window_hours=24, max_window_hours=240),
            post_formatting=SimpleNamespace(hashtag_mode="both"),
            scheduler=SimpleNamespace(timezone="UTC"),
            internet_scoring=SimpleNamespace(enabled=True),
        ),
        session_factory=_dummy_session_factory,
        repository=_BotSettingsRepositorySpy(),
        source_repository=_SourceRepositorySpy(),
        publisher=publisher,
        ingestion_runner=_IngestionRunnerSpy(),
        workflow=SimpleNamespace(),
    )
    router = create_settings_router(context)
    menu_handler = _message_handler(router, "menu")
    ingest_now_handler = _message_handler(router, "ingest_now")

    message = _Message(chat_id=-1001, topic_id=7)
    await menu_handler(message)
    await ingest_now_handler(message)

    assert len(publisher.sent) >= 4
    assert publisher.sent[-1]["text"].startswith("Операционный центр")


@pytest.mark.asyncio
async def test_setup_ui_sends_wizard_with_keyboard() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    repo = _BotSettingsRepositorySpy(
        row=_BotSettingsRow(
            group_chat_id=-1001,
            inbox_topic_id=10,
            editing_topic_id=11,
            ready_topic_id=12,
        )
    )
    _, handler = _router_and_handler_by_name(
        "setup_ui",
        publisher=publisher,
        ingestion=ingestion,
        repository=repo,
        session_factory=_dummy_session_factory,
    )

    await handler(_Message(chat_id=-1001, topic_id=12))

    assert publisher.sent
    assert "Setup wizard" in publisher.sent[-1]["text"]
    assert "ready_topic_id: 12 <= current topic" in publisher.sent[-1]["text"]
    assert publisher.sent[-1]["keyboard"] is not None


@pytest.mark.asyncio
async def test_ops_menu_callback_opens_system_page() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    _, handler = _router_and_callback_handler(
        publisher=publisher,
        ingestion=ingestion,
    )

    query = _CallbackQuery(data="ops:page:system")
    await handler(query)

    assert publisher.edits
    assert "Раздел: Система" in publisher.edits[-1]["text"]


@pytest.mark.asyncio
async def test_ops_menu_callback_not_modified_does_not_create_tail() -> None:
    publisher = _PublisherSpy(raise_not_modified_on_edit=True)
    ingestion = _IngestionRunnerSpy()
    _, handler = _router_and_callback_handler(
        publisher=publisher,
        ingestion=ingestion,
    )

    query = _CallbackQuery(data="ops:page:system")
    await handler(query)

    assert publisher.sent == []
    assert publisher.deleted == []


@pytest.mark.asyncio
async def test_ops_menu_trend_profiles_page_renders_profiles() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    profile_repo = _TrendProfileRepositorySpy(
        rows=[
            _TrendProfileRow(id=1, name="AI", enabled=True, seed_keywords=["ai", "llm"], min_article_score=1.2),
            _TrendProfileRow(id=2, name="Space", enabled=False, seed_keywords=["space"], min_article_score=1.0),
        ]
    )
    _, handler = _router_and_callback_handler(
        publisher=publisher,
        ingestion=ingestion,
        trend_profile_repository=profile_repo,
        session_factory=_dummy_session_factory,
    )

    query = _CallbackQuery(data="ops:page:trend_profiles:1")
    await handler(query)

    assert publisher.edits
    text = publisher.edits[-1]["text"]
    assert "Профили трендов: 2" in text
    assert "#1 [ON] AI" in text
    assert "#2 [OFF] Space" in text


@pytest.mark.asyncio
async def test_ops_menu_trend_profile_toggle_updates_state() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    profile_repo = _TrendProfileRepositorySpy(
        rows=[
            _TrendProfileRow(id=7, name="Gadgets", enabled=False, seed_keywords=["gadget"], min_article_score=1.1),
        ]
    )
    _, handler = _router_and_callback_handler(
        publisher=publisher,
        ingestion=ingestion,
        trend_profile_repository=profile_repo,
        session_factory=_dummy_session_factory,
    )

    query = _CallbackQuery(data="ops:prf:tgl:7:1")
    await handler(query)

    assert profile_repo.rows[0].enabled is True
    assert publisher.sent
    assert "Профиль #7 переключен: ON" in publisher.sent[-1]["text"]
    assert publisher.edits
    assert "#7 [ON] Gadgets" in publisher.edits[-1]["text"]


@pytest.mark.asyncio
async def test_ops_menu_trend_queue_renders_candidates() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    candidates_repo = _TrendCandidateRepositorySpy(
        article_rows=[
            _TrendArticleCandidateRow(
                id=11,
                score=3.2,
                title="Quantum networking update",
                url="https://example.com/a11",
            ),
        ],
        source_rows=[
            _TrendSourceCandidateRow(
                id=21,
                score=2.4,
                domain="reddit.com",
            ),
        ],
    )
    _, handler = _router_and_callback_handler(
        publisher=publisher,
        ingestion=ingestion,
        trend_candidates_repository=candidates_repo,
        session_factory=_dummy_session_factory,
    )

    query = _CallbackQuery(data="ops:page:trend_queue:1")
    await handler(query)

    assert publisher.edits
    text = publisher.edits[-1]["text"]
    assert "Очередь trend-кандидатов" in text
    assert "A#11" in text
    assert "S#21" in text


@pytest.mark.asyncio
async def test_ops_menu_trend_queue_actions_call_discovery_methods() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    trend_discovery = _TrendDiscoverySpy()
    candidates_repo = _TrendCandidateRepositorySpy(
        article_rows=[_TrendArticleCandidateRow(id=15, score=2.8, title="AI model", url="https://x/15")],
        source_rows=[_TrendSourceCandidateRow(id=77, score=2.1, domain="hn.com")],
    )
    _, handler = _router_and_callback_handler(
        publisher=publisher,
        ingestion=ingestion,
        trend_discovery=trend_discovery,
        trend_candidates_repository=candidates_repo,
        session_factory=_dummy_session_factory,
    )

    await handler(_CallbackQuery(data="ops:tr:qing:15:1"))
    await handler(_CallbackQuery(data="ops:tr:qrej:15:1"))
    await handler(_CallbackQuery(data="ops:tr:qadd:77:1"))
    await handler(_CallbackQuery(data="ops:tr:qsrej:77:1"))

    assert trend_discovery.ingest_calls == [15]
    assert trend_discovery.reject_article_calls == [15]
    assert trend_discovery.add_source_calls == [77]
    assert trend_discovery.reject_source_calls == [77]


@pytest.mark.asyncio
async def test_ops_setup_cfg_ready_updates_settings() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    repo = _BotSettingsRepositorySpy()
    _, handler = _router_and_callback_handler(
        publisher=publisher,
        ingestion=ingestion,
        repository=repo,
        session_factory=_dummy_session_factory,
    )

    query = _CallbackQuery(data="ops:cfg:ready", chat_id=-10077, topic_id=55)
    await handler(query)

    assert repo.row.group_chat_id == -10077
    assert repo.row.ready_topic_id == 55
    assert publisher.edits
    assert "Последнее действие: ready_topic_id=55" in publisher.edits[-1]["text"]


@pytest.mark.asyncio
async def test_ops_setup_cfg_requires_topic() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    repo = _BotSettingsRepositorySpy()
    _, handler = _router_and_callback_handler(
        publisher=publisher,
        ingestion=ingestion,
        repository=repo,
        session_factory=_dummy_session_factory,
    )

    query = _CallbackQuery(data="ops:cfg:ready", chat_id=-10077, topic_id=None)
    await handler(query)

    assert query.answers
    assert "Действие доступно только в topic" in [value for value in query.answers if value]
    assert repo.row.ready_topic_id is None


@pytest.mark.asyncio
async def test_ops_menu_source_toggle_happy_path() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    source = SimpleNamespace(id=1, enabled=False, trust_score=0.7, name="Source 1", url="https://a.example/rss")
    repo = _SourceRepositorySpy(rows=[source], by_id={1: source})
    _, handler = _router_and_callback_handler(
        publisher=publisher,
        ingestion=ingestion,
        source_repository=repo,
    )

    query = _CallbackQuery(data="ops:src:tgl:1:1")
    await handler(query)

    assert source.enabled is True
    assert publisher.sent
    assert "Источник #1 переключен: ON" in publisher.sent[-1]["text"]
    assert publisher.edits
    assert "#1 [ON]" in publisher.edits[-1]["text"]


@pytest.mark.asyncio
async def test_ops_menu_source_toggle_failure_when_missing() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    repo = _SourceRepositorySpy(rows=[], by_id={})
    _, handler = _router_and_callback_handler(
        publisher=publisher,
        ingestion=ingestion,
        source_repository=repo,
    )

    query = _CallbackQuery(data="ops:src:tgl:99:1")
    await handler(query)

    assert publisher.sent
    assert "Источник #99 не найден." in publisher.sent[-1]["text"]


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
async def test_ingest_now_reports_no_entries_hint() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy(
        run_once_result=SimpleNamespace(
            sources_total=1,
            entries_total=0,
            created=0,
            duplicates=0,
            skipped_low_score=0,
            skipped_invalid_entry=0,
            skipped_no_html=0,
            skipped_unsafe=0,
            skipped_blocked=0,
            skipped_rate_limited=0,
            rss_fetch_errors=0,
        )
    )
    _, handler = _router_and_handler_by_name(
        "ingest_now",
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_Message())

    assert publisher.sent
    text = publisher.sent[-1]["text"]
    assert "Новых RSS entries не найдено" in text
    assert "RSS URL" in text


@pytest.mark.asyncio
async def test_ops_menu_ingest_now_reopens_menu_after_background_job() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    _, handler = _router_and_callback_handler(
        publisher=publisher,
        ingestion=ingestion,
    )

    await handler(_CallbackQuery(data="ops:act:ingest_now"))

    for _ in range(8):
        await asyncio.sleep(0)

    assert publisher.edits
    assert "Ingest запущен в фоне" in publisher.edits[-1]["text"]
    assert any("Запускаю RSS ingestion" in item["text"] for item in publisher.sent)
    assert any("Операционный центр" in item["text"] for item in publisher.sent)


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
async def test_source_health_reports_risk_order() -> None:
    publisher = _PublisherSpy()
    ingestion = _IngestionRunnerSpy()
    source_rows = [
        SimpleNamespace(
            id=1,
            enabled=True,
            trust_score=1.2,
            name="Good source",
            tags={"quality": {"events_total": 10, "last_event": "created", "health": {"consecutive_failures": 0}}},
        ),
        SimpleNamespace(
            id=2,
            enabled=False,
            trust_score=-2.0,
            name="Risky source",
            tags={"quality": {"events_total": 30, "last_event": "rss_http_403", "health": {"consecutive_failures": 6}}},
        ),
    ]
    context = SettingsContext(
        settings=SimpleNamespace(
            admin_user_id=10,
            trend_discovery=SimpleNamespace(default_window_hours=24, mode="suggest"),
            analytics=SimpleNamespace(default_window_hours=24, max_window_hours=240),
            post_formatting=SimpleNamespace(hashtag_mode="both"),
            scheduler=SimpleNamespace(timezone="UTC"),
            internet_scoring=SimpleNamespace(enabled=True),
        ),
        session_factory=_dummy_session_factory,
        repository=SimpleNamespace(),
        source_repository=_SourceRepositorySpy(rows=source_rows),
        publisher=publisher,
        ingestion_runner=ingestion,
        workflow=SimpleNamespace(),
    )
    router = create_settings_router(context)
    handler = None
    for item in router.message.handlers:
        if item.callback.__name__ == "source_health":
            handler = item.callback
            break
    assert handler is not None

    await handler(_Message(), SimpleNamespace(args=None))

    assert publisher.sent
    text = "\n".join(item["text"] for item in publisher.sent)
    assert "Source health" in text
    assert "#2" in text
    assert "fails=6" in text


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
