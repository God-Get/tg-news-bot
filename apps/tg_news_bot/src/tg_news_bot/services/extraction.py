"""Article extraction."""

from __future__ import annotations

from dataclasses import dataclass

import trafilatura
from readability import Document
from lxml import html as lxml_html


@dataclass(slots=True)
class ExtractionResult:
    text: str | None
    title: str | None


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

        if text:
            return ExtractionResult(text=text.strip(), title=title)

        try:
            doc = Document(html)
            title = title or doc.short_title()
            summary_html = doc.summary()
            text = lxml_html.fromstring(summary_html).text_content()
            return ExtractionResult(text=text.strip(), title=title)
        except Exception:
            return ExtractionResult(text=None, title=title)
