from __future__ import annotations

import re

MAX_TOTAL_CHARS = 1000
TARGET_MIN_CHARS = 500
TARGET_MAX_CHARS = 900

MAX_TITLE_CHARS = 80
MAX_VISUAL_LINES = 8

HASHTAG_RE = re.compile(r"#([a-zA-Zа-яА-Я0-9_]{2,})")


def _split_lines(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [ln.rstrip() for ln in text.strip().split("\n")]


def _first_nonempty_line(lines: list[str]) -> str:
    for ln in lines:
        if ln.strip():
            return ln.strip()
    return ""


def validate_post_text(text: str) -> tuple[bool, str | None, str]:
    if text is None:
        return False, "Пустой текст.", ""

    normalized = "\n".join(_split_lines(text))
    if not normalized.strip():
        return False, "Пустой текст.", ""

    total = len(normalized)
    if total > MAX_TOTAL_CHARS:
        return False, f"Слишком длинно: {total} символов (макс {MAX_TOTAL_CHARS}).", normalized

    lines = _split_lines(normalized)
    title = _first_nonempty_line(lines)
    if not title:
        return False, "Не найден заголовок (первая строка).", normalized
    if len(title) > MAX_TITLE_CHARS:
        return False, f"Заголовок слишком длинный: {len(title)} (макс {MAX_TITLE_CHARS}).", normalized

    visual_lines = len([ln for ln in lines if ln.strip() or ln == ""])
    if visual_lines > MAX_VISUAL_LINES:
        return False, f"Слишком много строк: {visual_lines} (ориентир ≤ {MAX_VISUAL_LINES}).", normalized

    return True, None, normalized


def extract_hashtags(text: str) -> list[str]:
    tags = HASHTAG_RE.findall(text or "")
    seen = set()
    out: list[str] = []
    for t in tags:
        t = t.lower()
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
