from tg_news_bot.utils.url import (
    extract_domain,
    make_absolute,
    normalize_title_key,
    normalize_url,
)


def test_normalize_url_removes_tracking_and_sorts_params() -> None:
    raw = "HTTP://Example.COM/Path/?utm_source=x&b=2&a=1"
    assert normalize_url(raw) == "http://example.com/Path?a=1&b=2"


def test_normalize_url_trims_default_ports_and_trailing_slash() -> None:
    assert normalize_url("https://example.com:443/path/") == "https://example.com/path"
    assert normalize_url("http://example.com:80/path/") == "http://example.com/path"


def test_extract_domain_strips_www() -> None:
    assert extract_domain("https://www.Example.com/test") == "example.com"


def test_make_absolute_joins_urls() -> None:
    base = "https://example.com/a/b"
    assert make_absolute("/img.png", base) == "https://example.com/img.png"


def test_normalize_title_key_compacts_text() -> None:
    assert normalize_title_key("AI: New model!!!") == "ai new model"
