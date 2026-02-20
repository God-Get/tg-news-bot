from __future__ import annotations

from tg_news_bot.telegram.handlers.settings import (
    parse_source_args,
    parse_source_batch_args,
)


def test_parse_source_args_supports_pipe_separator() -> None:
    url, name = parse_source_args("https://example.com/rss | Example Feed")

    assert url == "https://example.com/rss"
    assert name == "Example Feed"


def test_parse_source_batch_args_supports_multiline_add_source_block() -> None:
    raw = (
        "https://www.nature.com/nature.rss | Nature\n"
        "/add_source https://news.mit.edu/rss/feed | MIT News\n"
        "/add_source https://www.nasa.gov/news-release/feed/ | NASA News"
    )

    assert parse_source_batch_args(raw) == [
        ("https://www.nature.com/nature.rss", "Nature"),
        ("https://news.mit.edu/rss/feed", "MIT News"),
        ("https://www.nasa.gov/news-release/feed/", "NASA News"),
    ]


def test_parse_source_batch_args_returns_empty_for_blank_input() -> None:
    assert parse_source_batch_args(" \n  \n") == []


def test_parse_source_batch_args_supports_single_line_without_name() -> None:
    assert parse_source_batch_args("https://rss.arxiv.org/rss/cs.AI") == [
        ("https://rss.arxiv.org/rss/cs.AI", "")
    ]
