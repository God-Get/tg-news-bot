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
