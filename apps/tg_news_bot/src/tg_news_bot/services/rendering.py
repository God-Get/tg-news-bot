"""Draft rendering helpers."""

from __future__ import annotations

from datetime import datetime
from html import escape
import re

from tg_news_bot.config import PostFormattingSettings
from tg_news_bot.db.models import Draft, DraftState
from telegram_publisher.types import PostContent

CAPTION_MAX_LEN = 1024
MESSAGE_MAX_LEN = 4096
DEFAULT_TITLE = "Без заголовка"
DEFAULT_BODY = "Текст будет добавлен после обработки."
_ALLOWED_SECTIONS = ("title", "body", "hashtags", "source")
_DEFAULT_ORDER = ["title", "body", "hashtags", "source"]
_DEFAULT_FORMATTING = PostFormattingSettings()


def render_post_content(
    draft: Draft,
    formatting: PostFormattingSettings | None = None,
) -> PostContent:
    fmt = formatting or _DEFAULT_FORMATTING
    include_source_text = fmt.source_mode in {"text", "both"}
    parse_mode = "HTML"
    photo = draft.tg_image_file_id or draft.source_image_url

    title, body = _split_title_body(draft)
    hashtags = _extract_hashtags(
        draft,
        limit=fmt.hashtags_limit,
        fallback=fmt.fallback_hashtag,
    )
    title_markup = f"<b>{escape(title)}</b>"
    hashtags_text = escape(" ".join(hashtags) if hashtags else "")
    source_text = (
        f'<a href="{escape(draft.normalized_url, quote=True)}">{escape(fmt.source_label)}</a>'
        if include_source_text
        else ""
    )
    ordered_sections = _ordered_sections(fmt.sections_order)

    def build_text(body_plain: str) -> str:
        section_values = {
            "title": title_markup,
            "body": escape(body_plain),
            "hashtags": hashtags_text,
            "source": source_text,
        }
        sections = [section_values[name] for name in ordered_sections if section_values.get(name)]
        text_value = fmt.section_separator.join(sections)
        if text_value:
            return text_value
        if include_source_text:
            return (
                f'<a href="{escape(draft.normalized_url, quote=True)}">'
                f"{escape(fmt.source_label)}</a>"
            )
        return DEFAULT_BODY

    text = build_text(body)

    max_len = CAPTION_MAX_LEN if photo else MESSAGE_MAX_LEN
    if len(text) > max_len:
        text = _fit_html_text_to_limit(
            max_len=max_len,
            full_body_plain=body,
            build_text=build_text,
        )

    return PostContent(text=text, photo=photo, parse_mode=parse_mode)


def _fit_html_text_to_limit(
    *,
    max_len: int,
    full_body_plain: str,
    build_text,
) -> str:
    current = build_text(full_body_plain)
    if len(current) <= max_len:
        return current

    low = 0
    high = len(full_body_plain)
    best_text = ""
    while low <= high:
        mid = (low + high) // 2
        candidate_body = full_body_plain[:mid].rstrip()
        if mid < len(full_body_plain):
            candidate_body = candidate_body.rstrip()
            if candidate_body:
                candidate_body = f"{candidate_body}…"
            else:
                candidate_body = "…"
        candidate_text = build_text(candidate_body)
        if len(candidate_text) <= max_len:
            best_text = candidate_text
            low = mid + 1
        else:
            high = mid - 1

    if best_text:
        return best_text
    return _truncate_html_preserving_tags(current, max_len=max_len)


def _truncate_html_preserving_tags(text: str, *, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return "…"

    clipped = text[: max_len - 1].rstrip()
    clipped = _trim_unfinished_html_tag(clipped)

    open_b = clipped.count("<b>") - clipped.count("</b>")
    open_a = len(re.findall(r"<a\s+[^>]*>", clipped)) - clipped.count("</a>")
    suffix = ""
    if open_a > 0:
        suffix += "</a>" * open_a
    if open_b > 0:
        suffix += "</b>" * open_b

    allowed = max_len - 1 - len(suffix)
    if allowed <= 0:
        return "…"
    clipped = clipped[:allowed].rstrip()
    clipped = _trim_unfinished_html_tag(clipped)
    return f"{clipped}…{suffix}"


def _trim_unfinished_html_tag(text: str) -> str:
    last_lt = text.rfind("<")
    last_gt = text.rfind(">")
    if last_lt > last_gt:
        return text[:last_lt].rstrip()
    return text


def _ordered_sections(raw_order: str) -> list[str]:
    parsed = [item.strip().lower() for item in raw_order.split(",") if item.strip()]
    selected: list[str] = []
    for item in parsed:
        if item in _ALLOWED_SECTIONS and item not in selected:
            selected.append(item)
    if selected:
        return selected
    return list(_DEFAULT_ORDER)


def _format_schedule_at(schedule_at: datetime) -> str:
    if schedule_at.tzinfo is None:
        return schedule_at.strftime("%Y-%m-%d %H:%M")
    return schedule_at.strftime("%Y-%m-%d %H:%M UTC")


def render_card_text(
    draft: Draft,
    *,
    schedule_at: datetime | None = None,
    state: DraftState | None = None,
) -> str:
    effective_state = state or draft.state
    score_text = f"{draft.score:.2f}" if draft.score is not None else "N/A"
    lines = [
        f"Draft #{draft.id}",
        f"State: {effective_state}",
        f"Score: {score_text}",
        f"Domain: {draft.domain or '-'}",
        f"Image: {draft.image_status}",
        f"URL: {draft.normalized_url}",
    ]
    if effective_state == DraftState.SCHEDULED:
        schedule_text = _format_schedule_at(schedule_at) if schedule_at else "-"
        lines.append(f"Schedule at: {schedule_text}")
    return "\n".join(lines)


def _split_title_body(draft: Draft) -> tuple[str, str]:
    raw = _normalize_escaped_whitespace((draft.post_text_ru or "").strip())
    raw = _remove_trailing_source(raw, normalized_url=draft.normalized_url)
    title_fallback = _normalize_escaped_whitespace(
        (draft.title_en or "").strip()
    ) or DEFAULT_TITLE

    if not raw:
        return title_fallback, DEFAULT_BODY

    parts = [part.strip() for part in re.split(r"\n\s*\n", raw, maxsplit=1)]
    if len(parts) == 2 and parts[0]:
        title = parts[0]
        body = parts[1] or DEFAULT_BODY
        return title, body

    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    if len(lines) >= 2:
        candidate_title = lines[0]
        candidate_body = "\n".join(lines[1:]).strip()
        if 3 <= len(candidate_title) <= 140 and candidate_body:
            return candidate_title, candidate_body

    return title_fallback, raw


def _extract_hashtags(
    draft: Draft,
    *,
    limit: int,
    fallback: str,
) -> list[str]:
    if limit <= 0:
        return []

    tags: list[str] = []
    seen: set[str] = set()

    reasons = draft.score_reasons if isinstance(draft.score_reasons, dict) else {}
    auto_hashtags = reasons.get("auto_hashtags")
    if isinstance(auto_hashtags, list):
        for item in auto_hashtags:
            normalized = _normalize_tag(str(item).lstrip("#"))
            if normalized and normalized not in seen:
                seen.add(normalized)
                tags.append(f"#{normalized}")

    for key in reasons:
        if key.startswith("kw:"):
            tag = _normalize_tag(key.removeprefix("kw:"))
            if tag and tag not in seen:
                seen.add(tag)
                tags.append(f"#{tag}")

    domain_tag = _normalize_tag(draft.domain or "")
    if domain_tag and domain_tag not in seen:
        tags.append(f"#{domain_tag}")

    if not tags:
        fallback_tag = _normalize_tag(fallback)
        if fallback_tag:
            tags = [f"#{fallback_tag}"]

    return tags[:limit]


def _normalize_tag(value: str) -> str:
    text = re.sub(r"[^0-9a-zA-Z_]+", "_", value.strip().lower()).strip("_")
    if not text:
        return ""
    if text[0].isdigit():
        return f"tag_{text}"
    return text


def _normalize_escaped_whitespace(value: str) -> str:
    return (
        value.replace("\\\\r\\\\n", "\n")
        .replace("\\\\n", "\n")
        .replace("\\\\t", " ")
        .replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\t", " ")
    )


def _remove_trailing_source(value: str, *, normalized_url: str) -> str:
    lines = [line.rstrip() for line in value.split("\n")]
    if not lines:
        return value

    known_url = normalized_url.strip()
    source_re = re.compile(r"^\s*источник\s*[:(]?\s*(?P<url>https?://\S+)?\s*\)?\s*$", re.IGNORECASE)
    url_only_re = re.compile(r"^\s*https?://\S+\s*$", re.IGNORECASE)

    while lines:
        candidate = lines[-1].strip()
        if not candidate:
            lines.pop()
            continue
        source_match = source_re.match(candidate)
        if source_match:
            url = (source_match.group("url") or "").rstrip(").,")
            if not url or not known_url or url.startswith(known_url):
                lines.pop()
                continue
        if url_only_re.match(candidate):
            clean_url = candidate.rstrip(").,")
            if not known_url or clean_url.startswith(known_url):
                lines.pop()
                continue
        break

    return "\n".join(lines).strip()
