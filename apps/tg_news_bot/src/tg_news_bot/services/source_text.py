from __future__ import annotations

import re


_BULLET_PREFIX_RE = re.compile(r"^\s*[-*•\u2013\u2014]+\s*")
_META_LABEL_RE = re.compile(r"(?i)^(date|source|summary|share)\s*:\s*(.*)$")
_SEPARATOR_RE = re.compile(r"^\s*[-*•\u2013\u2014]+\s*$")
_NATURE_BROWSER_NOTICE_RE = re.compile(
    r"(?is)\bthank\s+you\s+for\s+visiting\s+nature\.com\.\s*"
    r"you\s+are\s+using\s+a\s+browser\s+version\s+with\s+limited\s+support\s+for\s+css\.\s*"
    r"to\s+obtain\s+the\s+best\s+experience,\s*we\s+recommend\s+you\s+use\s+a\s+more\s+up\s+to\s+date\s+browser\s*"
    r"\(or\s+turn\s+off\s+compatibility\s+mode\s+in\s+internet\s+explorer\)\.\s*"
    r"in\s+the\s+meantime,\s*to\s+ensure\s+continued\s+support,\s*we\s+are\s+displaying\s+the\s+site\s+without\s+styles\s+and\s+javascript\.\s*"
)
_NATURE_ACCESS_OPTIONS_RE = re.compile(
    r"(?is)\baccess\s+options\b.*?"
    r"get\s+nature\+.*?"
    r"\brent\s+or\s+buy\s+this\s+article\b.*?"
    r"\bprices\s+may\s+be\s+subject\s+to\s+local\s+taxes\s+which\s+are\s+calculated\s+during\s+checkout\b\.?\s*"
    r"(?:doi\s*:\s*)?"
)


def _parse_meta_label(line: str) -> tuple[str, str] | None:
    normalized = _BULLET_PREFIX_RE.sub("", line).strip()
    match = _META_LABEL_RE.match(normalized)
    if not match:
        return None
    label = match.group(1).lower()
    rest = match.group(2).strip()
    return label, rest


def sanitize_source_text(text: str | None) -> str:
    if not text:
        return ""

    cleaned = text.strip()
    # Inline metadata prefix from some sources:
    # "Date: ... Source: ... Summary: ...<content>"
    cleaned = re.sub(
        r"(?is)^\s*date\s*:\s*.*?\bsource\s*:\s*.*?\bsummary\s*:\s*",
        "",
        cleaned,
        count=1,
    ).strip()
    cleaned = _NATURE_BROWSER_NOTICE_RE.sub("", cleaned).strip()
    cleaned = _NATURE_ACCESS_OPTIONS_RE.sub("", cleaned).strip()

    lines = cleaned.splitlines()
    result: list[str] = []
    skip_value_after_label = False

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if not skip_value_after_label and result and result[-1] != "":
                result.append("")
            continue

        parsed = _parse_meta_label(line)
        if parsed:
            label, rest = parsed
            if label in {"date", "source", "summary"} and not rest:
                skip_value_after_label = True
            else:
                skip_value_after_label = False
            continue

        if skip_value_after_label:
            skip_value_after_label = False
            continue

        if _SEPARATOR_RE.match(line):
            continue

        result.append(stripped)

    return "\n".join(result).strip()
