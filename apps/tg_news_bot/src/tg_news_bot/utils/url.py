"""URL helpers."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_name",
    "utm_id",
    "utm_reader",
    "utm_referrer",
    "gclid",
    "fbclid",
    "ref",
    "ref_src",
    "source",
    "rss",
    "mc_cid",
    "mc_eid",
}


def normalize_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    scheme = parsed.scheme.lower() if parsed.scheme else "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]

    query_params = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False) if k not in TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_params))

    return urlunparse((scheme, netloc, path, "", query, ""))


def extract_domain(raw_url: str) -> str:
    netloc = urlparse(raw_url).netloc.lower()
    if netloc.startswith("www."):
        return netloc[4:]
    return netloc


def make_absolute(url: str, base_url: str) -> str:
    return urljoin(base_url, url)


def normalize_title_key(value: str | None) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z]+", " ", (value or "").strip().lower())
    return re.sub(r"\s+", " ", cleaned).strip()
