"""Article extraction."""

from __future__ import annotations

from dataclasses import dataclass
import re

import trafilatura
from readability import Document
from lxml import html as lxml_html


@dataclass(slots=True)
class ExtractionResult:
    text: str | None
    title: str | None


_WHITESPACE_RE = re.compile(r"\s+")
_PAYWALL_HINTS = (
    "access options",
    "access nature",
    "get nature+",
    "subscribe to this journal",
    "rent or buy this article",
    "prices vary by article type",
    "prices may be subject to local taxes",
)
_PAYWALL_LINE_RE = re.compile(
    r"(?i)^(access options|access nature|get nature\+|subscribe to this journal|"
    r"receive \d+ print issues and online access|rent or buy this article|"
    r"prices vary by article type|prices may be subject to local taxes|"
    r"cancel any time|from\$\d|to\$\d|doi:|references?\b)"
)
_DATE_LINE_RE = re.compile(r"\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b")
_NOISE_CLASS_TOKENS = (
    "buyboxsection",
    "liveareasection",
    "readcube-buybox",
    "subscribe-buybox",
    "c-article-references",
    "c-latest-content",
    "js-jobs-career-wrapper",
)
_NOISE_ID_TOKENS = (
    "references",
    "latest-content",
)


class ArticleExtractor:
    def extract(self, html: str) -> ExtractionResult:
        text = None
        title = None

        try:
            text = trafilatura.extract(html, output_format="txt", include_comments=False)
            metadata = trafilatura.extract_metadata(html)
            if metadata and metadata.title:
                title = metadata.title
        except Exception:
            text = None
            title = None

        text = (text or "").strip() or None
        if text and not self._looks_like_paywall_text(text):
            return ExtractionResult(text=text, title=title)

        try:
            doc = Document(html)
            title = title or doc.short_title()
            summary_html = doc.summary()
            text = lxml_html.fromstring(summary_html).text_content().strip() or None
            if text and not self._looks_like_paywall_text(text):
                return ExtractionResult(text=text, title=title)
        except Exception:
            text = text or None

        fallback_text = self._extract_meta_and_article_text(html)
        if fallback_text:
            return ExtractionResult(text=fallback_text, title=title)
        return ExtractionResult(text=text, title=title)

    @staticmethod
    def _normalize_space(value: str) -> str:
        return _WHITESPACE_RE.sub(" ", value).strip()

    @classmethod
    def _looks_like_paywall_text(cls, text: str) -> bool:
        compact = cls._normalize_space(text).lower()
        if not compact:
            return False
        hits = sum(1 for hint in _PAYWALL_HINTS if hint in compact)
        if hits >= 2:
            return True
        if "research highlight" in compact and _DATE_LINE_RE.search(text):
            return True
        return False

    @classmethod
    def _extract_meta_and_article_text(cls, html: str) -> str | None:
        try:
            root = lxml_html.fromstring(html)
        except Exception:
            return None

        description = ""
        for xpath in (
            "//meta[@property='og:description']/@content",
            "//meta[@name='twitter:description']/@content",
            "//meta[@name='description']/@content",
            "//meta[@name='dc.description']/@content",
        ):
            values = root.xpath(xpath)
            if values:
                description = cls._normalize_space(str(values[0]))
                if description:
                    break

        paragraph_nodes = root.xpath(
            "//article[contains(@class,'article-item')]"
            "//div[contains(@class,'c-article-main-column')]//p"
        )
        if not paragraph_nodes:
            paragraph_nodes = root.xpath("//article//p")
        if not paragraph_nodes:
            paragraph_nodes = root.xpath("//main//p")

        paragraphs: list[str] = []
        for node in paragraph_nodes:
            if cls._has_noise_ancestor(node):
                continue
            text = cls._normalize_space(node.text_content())
            if not text:
                continue
            text_lc = text.lower()
            if _PAYWALL_LINE_RE.match(text):
                continue
            if "credit:" in text_lc:
                continue
            if text_lc.startswith("references"):
                continue
            if " et al." in text_lc and "doi.org/" in text_lc:
                continue
            if not any(ch in text for ch in ".?!") and len(text) < 140:
                continue
            if len(text) < 40:
                continue
            paragraphs.append(text)

        lines: list[str] = []
        seen_lower: set[str] = set()
        if description:
            lines.append(description)
            seen_lower.add(description.lower())
        for paragraph in paragraphs:
            paragraph_lc = paragraph.lower()
            if paragraph_lc in seen_lower:
                continue
            lines.append(paragraph)
            seen_lower.add(paragraph_lc)
            if len(lines) >= 4:
                break

        if not lines:
            return None
        return "\n\n".join(lines).strip()

    @staticmethod
    def _has_noise_ancestor(node: lxml_html.HtmlElement) -> bool:
        for element in node.xpath("ancestor-or-self::*"):
            class_name = (element.get("class") or "").lower()
            element_id = (element.get("id") or "").lower()
            if any(token in class_name for token in _NOISE_CLASS_TOKENS):
                return True
            if any(token in element_id for token in _NOISE_ID_TOKENS):
                return True
        return False
