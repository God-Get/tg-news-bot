from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tg_news_bot.services.ingestion import IngestionRunner


def test_topic_hints_from_tags_supports_topics_list() -> None:
    result = IngestionRunner._topic_hints_from_tags({"topics": ["AI", "space", ""]})

    assert result == ["ai", "space"]


def test_topic_hints_from_tags_supports_single_topic() -> None:
    result = IngestionRunner._topic_hints_from_tags({"topic": "new energy"})

    assert result == ["new energy"]


def test_topic_hints_from_tags_handles_missing() -> None:
    result = IngestionRunner._topic_hints_from_tags(None)

    assert result == []


def test_normalized_url_candidates_include_queryless_variant() -> None:
    candidates = IngestionRunner._normalized_url_candidates(
        "https://example.com/a/?utm_source=1&x=2",
        entry_id=None,
    )

    assert candidates[0] == "https://example.com/a?x=2"
    assert "https://example.com/a" in candidates


def test_rate_limit_source_check() -> None:
    runner = object.__new__(IngestionRunner)
    runner._settings = SimpleNamespace(rss=SimpleNamespace(per_source_min_interval_seconds=120))
    now = datetime.now(timezone.utc)
    runner._source_last_poll = {1: now - timedelta(seconds=10), 2: now - timedelta(seconds=500)}

    assert runner._is_rate_limited_source(1, now=now) is True
    assert runner._is_rate_limited_source(2, now=now) is False


def test_should_try_insecure_ssl() -> None:
    runner = object.__new__(IngestionRunner)
    runner._settings = SimpleNamespace(
        rss=SimpleNamespace(
            allow_insecure_ssl_fallback=True,
            insecure_ssl_domains=["badssl.example"],
        )
    )

    assert runner._should_try_insecure_ssl(url="https://badssl.example/feed", feed_tags=None) is True
    assert runner._should_try_insecure_ssl(url="https://another.example/feed", feed_tags={"allow_insecure_ssl": True}) is True
    assert runner._should_try_insecure_ssl(url="https://another.example/feed", feed_tags={"allow_insecure_ssl": False}) is False


def test_blocked_reason_matches_domain_and_subdomain() -> None:
    runner = object.__new__(IngestionRunner)
    runner._settings = SimpleNamespace(
        rss=SimpleNamespace(
            blocked_domains=["example.com"],
            blocked_url_keywords=[],
            blocked_title_keywords=[],
        )
    )

    assert (
        runner._blocked_reason(
            domain="example.com",
            normalized_url="https://example.com/news/1",
        )
        == "domain:example.com"
    )
    assert (
        runner._blocked_reason(
            domain="blog.example.com",
            normalized_url="https://blog.example.com/news/1",
        )
        == "domain:example.com"
    )


def test_blocked_reason_matches_url_keyword() -> None:
    runner = object.__new__(IngestionRunner)
    runner._settings = SimpleNamespace(
        rss=SimpleNamespace(
            blocked_domains=[],
            blocked_url_keywords=["/sponsored/", "utm_medium=ad"],
            blocked_title_keywords=[],
        )
    )

    assert (
        runner._blocked_reason(
            domain="site.test",
            normalized_url="https://site.test/sponsored/post-1",
        )
        == "url_keyword:/sponsored/"
    )
    assert (
        runner._blocked_reason(
            domain="site.test",
            normalized_url="https://site.test/post-1?utm_medium=ad&utm_source=rss",
        )
        == "url_keyword:utm_medium=ad"
    )


def test_blocked_reason_matches_title_keyword() -> None:
    runner = object.__new__(IngestionRunner)
    runner._settings = SimpleNamespace(
        rss=SimpleNamespace(
            blocked_domains=[],
            blocked_url_keywords=[],
            blocked_title_keywords=["podcast", "newsletter"],
        )
    )

    assert (
        runner._blocked_reason(
            domain="site.test",
            normalized_url="https://site.test/post-1",
            title="Weekly AI Podcast roundup",
        )
        == "title_keyword:podcast"
    )
