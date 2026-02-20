from __future__ import annotations

from tg_news_bot.services.source_text import sanitize_source_text


def test_sanitize_source_text_removes_inline_metadata_prefix() -> None:
    text = (
        "Date: - November 17, 2025 - Source: - Florida State University - Summary: "
        "Researchers found a robust signal in long-term data."
    )

    assert sanitize_source_text(text) == "Researchers found a robust signal in long-term data."


def test_sanitize_source_text_removes_multiline_metadata_block() -> None:
    text = (
        "Stanford's tiny eye chip helps the blind see again\n"
        "- Date:\n"
        "- October 22, 2025\n"
        "- Source:\n"
        "- Stanford Medicine\n"
        "- Summary:\n"
        "- A wireless eye implant restored reading ability.\n"
        "- Share:\n"
        "A tiny wireless chip at the back of the eye restored partial vision.\n"
        "Most trial participants regained reading ability within a year."
    )

    assert sanitize_source_text(text) == (
        "Stanford's tiny eye chip helps the blind see again\n"
        "A tiny wireless chip at the back of the eye restored partial vision.\n"
        "Most trial participants regained reading ability within a year."
    )


def test_sanitize_source_text_removes_nature_browser_warning_banner() -> None:
    text = (
        "Biotech investor set to lead US National Science Foundation\n\n"
        "Thank you for visiting nature.com. You are using a browser version with limited support for CSS. "
        "To obtain the best experience, we recommend you use a more up to date browser "
        "(or turn off compatibility mode in Internet Explorer). In the meantime, to ensure continued support, "
        "we are displaying the site without styles and JavaScript. "
        "US President Donald Trump plans to nominate biotechnology investor Jim O'Neill to be the next leader "
        "of the National Science Foundation (NSF)."
    )

    cleaned = sanitize_source_text(text)

    assert "Thank you for visiting nature.com." not in cleaned
    assert cleaned == (
        "Biotech investor set to lead US National Science Foundation\n\n"
        "US President Donald Trump plans to nominate biotechnology investor Jim O'Neill to be the next leader "
        "of the National Science Foundation (NSF)."
    )


def test_sanitize_source_text_removes_nature_access_options_block() -> None:
    text = (
        "Biotech investor set to lead US National Science Foundation\n\n"
        "Access options Access Nature and 54 other Nature Portfolio journals "
        "Get Nature+, our best-value online-access subscription 27.99 / 30 days cancel any time "
        "Subscribe to this journal Receive 51 print issues and online access 185.98 per year only 3.65 per issue "
        "Rent or buy this article Prices vary by article type from$1.95 to$39.95 "
        "Prices may be subject to local taxes which are calculated during checkout doi: "
        "US President Donald Trump plans to nominate biotechnology investor Jim O'Neill to be the next leader "
        "of the National Science Foundation (NSF)."
    )

    cleaned = sanitize_source_text(text)

    assert "Access options Access Nature" not in cleaned
    assert cleaned == (
        "Biotech investor set to lead US National Science Foundation\n\n"
        "US President Donald Trump plans to nominate biotechnology investor Jim O'Neill to be the next leader "
        "of the National Science Foundation (NSF)."
    )
