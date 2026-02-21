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
_RU_HASHTAG_ALIASES = {
    "ai": "ии",
    "artificial_intelligence": "ии",
    "machine_learning": "машинное_обучение",
    "deep_learning": "глубокое_обучение",
    "neural_network": "нейросети",
    "science": "наука",
    "space": "космос",
    "space_tech": "космос",
    "energy": "энергия",
    "new_energy": "новая_энергия",
    "biotech": "биотех",
    "technology": "технологии",
    "tech": "технологии",
}
_CANONICAL_HASHTAGS = {
    "artificial_intelligence": "ai",
    "machine_learning": "machine_learning",
    "deep_learning": "deep_learning",
    "neural_network": "neural_network",
    "space_tech": "space",
    "new_energy": "energy",
    "technology": "technology",
    "tech": "technology",
}
_HASHTAG_STOPWORDS = {
    "update",
    "updates",
    "article",
    "articles",
    "summary",
    "source",
    "report",
    "official",
    "today",
    "yesterday",
}


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
        mode=fmt.hashtag_mode,
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
    hot_score = _extract_hot_score(draft)
    trust_score = _extract_trust_score(draft)
    lines = [
        f"Draft #{draft.id}",
        f"State: {effective_state}",
        f"Score: {score_text}",
        f"Hot score: {hot_score:.2f}" if hot_score is not None else "Hot score: N/A",
        f"Trust score: {trust_score:.2f}" if trust_score is not None else "Trust score: N/A",
        f"Domain: {draft.domain or '-'}",
        f"Image: {draft.image_status}",
        f"URL: {draft.normalized_url}",
    ]
    top_reasons = _top_scoring_reasons(draft, limit=3)
    if top_reasons:
        lines.append("Reasons: " + "; ".join(top_reasons))
    if effective_state == DraftState.SCHEDULED:
        schedule_text = _format_schedule_at(schedule_at) if schedule_at else "-"
        lines.append(f"Schedule at: {schedule_text}")
    return "\n".join(lines)


def _extract_hot_score(draft: Draft) -> float | None:
    reasons = draft.score_reasons if isinstance(draft.score_reasons, dict) else {}
    raw = reasons.get("hot_score")
    if isinstance(raw, (int, float)):
        return float(raw)

    trend_total = 0.0
    for key, value in reasons.items():
        if not isinstance(value, (int, float)):
            continue
        if str(key).startswith("trend:"):
            trend_total += float(value)
    return trend_total if trend_total > 0 else 0.0


def _extract_trust_score(draft: Draft) -> float | None:
    reasons = draft.score_reasons if isinstance(draft.score_reasons, dict) else {}
    raw = reasons.get("trust_score")
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


def _top_scoring_reasons(draft: Draft, *, limit: int) -> list[str]:
    reasons = draft.score_reasons if isinstance(draft.score_reasons, dict) else {}
    candidates: list[tuple[str, float]] = []
    for key, value in reasons.items():
        if not isinstance(value, (int, float)):
            continue
        reason_key = str(key)
        if reason_key in {"length", "age_hours", "hot_score", "trust_score", "safety_quality"}:
            continue
        if reason_key.startswith("auto_"):
            continue
        if reason_key in {"manual_hashtags"}:
            continue
        candidates.append((reason_key, float(value)))

    candidates.sort(key=lambda item: (abs(item[1]), item[0]), reverse=True)
    result: list[str] = []
    for key, value in candidates[: max(limit, 0)]:
        result.append(f"{_reason_label(key)}={value:+.2f}")
    return result


def _reason_label(key: str) -> str:
    if key.startswith("kw_title:"):
        return f"title_kw({key.removeprefix('kw_title:').lower()})"
    if key.startswith("kw:"):
        return f"kw({key.removeprefix('kw:').lower()})"
    if key.startswith("trend:"):
        return f"trend({key.removeprefix('trend:').lower()})"
    if key.startswith("domain:"):
        return f"domain({key.removeprefix('domain:').lower()})"
    return key


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
    mode: str,
) -> list[str]:
    if limit <= 0:
        return []

    tags: list[str] = []
    seen: set[str] = set()

    mode_normalized = _normalize_hashtag_mode(mode)

    def add_variants(raw_value: str) -> None:
        for normalized in _iter_tag_variants(raw_value, mode=mode_normalized):
            canonical = _canonical_tag(normalized)
            if normalized and canonical not in seen and _is_quality_tag(normalized):
                seen.add(canonical)
                tags.append(f"#{normalized}")
            if len(tags) >= limit:
                break

    reasons = draft.score_reasons if isinstance(draft.score_reasons, dict) else {}
    manual_hashtags = reasons.get("manual_hashtags")
    if isinstance(manual_hashtags, list):
        for item in manual_hashtags:
            normalized = _normalize_tag(str(item).lstrip("#"))
            canonical = _canonical_tag(normalized)
            if normalized and canonical not in seen and _is_quality_tag(normalized):
                seen.add(canonical)
                tags.append(f"#{normalized}")
        # If editor explicitly set manual hashtags, do not mix with auto keywords
        # and preserve the explicit order from the editor.
        return tags

    auto_hashtags = reasons.get("auto_hashtags")
    if isinstance(auto_hashtags, list):
        for item in auto_hashtags:
            add_variants(str(item).lstrip("#"))
            if len(tags) >= limit:
                return tags[:limit]

    for key in reasons:
        if key.startswith("kw:"):
            add_variants(key.removeprefix("kw:"))
            if len(tags) >= limit:
                return tags[:limit]

    domain_tag = _normalize_tag(draft.domain or "")
    domain_canonical = _canonical_tag(domain_tag)
    if domain_tag and domain_canonical not in seen and _is_quality_tag(domain_tag):
        seen.add(domain_canonical)
        tags.append(f"#{domain_tag}")

    if not tags:
        for fallback_tag in _iter_tag_variants(fallback, mode=mode_normalized):
            canonical = _canonical_tag(fallback_tag)
            if fallback_tag and canonical not in seen and _is_quality_tag(fallback_tag):
                seen.add(canonical)
                tags.append(f"#{fallback_tag}")
            if len(tags) >= limit:
                break

    return tags[:limit]


def _iter_tag_variants(value: str, *, mode: str) -> list[str]:
    base = _normalize_tag(value)
    if not base:
        return []
    translated_tag = ""
    translated = _RU_HASHTAG_ALIASES.get(base)
    if translated:
        translated_tag = _normalize_tag(translated)

    if mode == "en":
        return [base]
    if mode == "ru":
        if translated_tag:
            return [translated_tag]
        if _contains_cyrillic(base):
            return [base]
        return []

    variants = [base]
    if translated_tag and translated_tag not in variants:
        variants.append(translated_tag)
    return variants


def _normalize_tag(value: str) -> str:
    text = re.sub(r"[^0-9a-zA-Zа-яА-ЯёЁ_]+", "_", value.strip().lower()).strip("_")
    if not text:
        return ""
    if text[0].isdigit():
        return f"tag_{text}"
    return text


def _canonical_tag(value: str) -> str:
    return _CANONICAL_HASHTAGS.get(value, value)


def _is_quality_tag(value: str) -> bool:
    if value in _HASHTAG_STOPWORDS:
        return False
    if len(value) < 2 or len(value) > 32:
        return False
    if value.isdigit():
        return False
    if value.startswith("http"):
        return False
    letters = [ch for ch in value if ch != "_" and ch.isalpha()]
    if len(letters) < 2:
        return False
    return True


def _normalize_hashtag_mode(value: str) -> str:
    mode = (value or "both").strip().lower()
    if mode not in {"ru", "en", "both"}:
        return "both"
    return mode


def _contains_cyrillic(value: str) -> bool:
    return bool(re.search(r"[а-яё]", value.lower()))


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
