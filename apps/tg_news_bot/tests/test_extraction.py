from __future__ import annotations

from types import SimpleNamespace

from tg_news_bot.services.extraction import ArticleExtractor
from tg_news_bot.services import extraction as extraction_module


def test_extract_uses_meta_article_fallback_for_paywall_text(monkeypatch) -> None:
    html = """
    <html>
      <head>
        <meta property="og:description" content="An adhesive inspired by gecko foot pads helps a robot climb rough and smooth walls." />
      </head>
      <body>
        <article>
          <p>Access options</p>
          <p>Access Nature and 54 other Nature Portfolio journals</p>
          <p>Get Nature+, our best-value online-access subscription</p>
          <p>By heating and cooling its feet, a robot can slowly climb walls. Credit: J. Feng et al./Matter</p>
          <p>A four-legged robot can crawl up walls made of steel, glass, aluminium or rough wood.</p>
          <p>Feng, J. et al. Matter https://doi.org/10.1016/j.matt.2025.102571 (2026).</p>
          <p>Rent or buy this article</p>
          <p>Prices may be subject to local taxes which are calculated during checkout</p>
          <p>References</p>
        </article>
      </body>
    </html>
    """

    monkeypatch.setattr(
        extraction_module.trafilatura,
        "extract",
        lambda *_args, **_kwargs: (
            "- RESEARCH HIGHLIGHT\n"
            "Access options\n"
            "Access Nature and 54 other Nature Portfolio journals\n"
            "Get Nature+, our best-value online-access subscription\n"
            "Rent or buy this article\n"
            "Prices may be subject to local taxes which are calculated during checkout\n"
            "References"
        ),
    )
    monkeypatch.setattr(
        extraction_module.trafilatura,
        "extract_metadata",
        lambda *_args, **_kwargs: SimpleNamespace(title="Super-sticky feet help a robot to climb the walls"),
    )

    result = ArticleExtractor().extract(html)

    assert result.title == "Super-sticky feet help a robot to climb the walls"
    assert result.text is not None
    assert "Access Nature and 54 other Nature Portfolio journals" not in result.text
    assert "Rent or buy this article" not in result.text
    assert "Credit:" not in result.text
    assert "et al. Matter https://doi.org/" not in result.text
    assert "A four-legged robot can crawl up walls" in result.text
    assert "An adhesive inspired by gecko foot pads" in result.text


def test_extract_keeps_regular_trafilatura_text(monkeypatch) -> None:
    monkeypatch.setattr(
        extraction_module.trafilatura,
        "extract",
        lambda *_args, **_kwargs: (
            "Researchers demonstrated a compact new battery chemistry.\n"
            "The prototype retained 90% capacity after 2000 cycles."
        ),
    )
    monkeypatch.setattr(
        extraction_module.trafilatura,
        "extract_metadata",
        lambda *_args, **_kwargs: SimpleNamespace(title="Battery chemistry breakthrough"),
    )

    result = ArticleExtractor().extract("<html><body>ignored</body></html>")

    assert result.title == "Battery chemistry breakthrough"
    assert result.text == (
        "Researchers demonstrated a compact new battery chemistry.\n"
        "The prototype retained 90% capacity after 2000 cycles."
    )
