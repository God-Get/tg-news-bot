"""Image selection and validation."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image
from lxml import html as lxml_html
from httpx import AsyncClient

from tg_news_bot.config import ImageFilterSettings
from tg_news_bot.db.models import ImageStatus
from tg_news_bot.utils.url import make_absolute


MAX_IMAGE_BYTES = 5 * 1024 * 1024


@dataclass(slots=True)
class ImageSelection:
    url: str | None
    status: ImageStatus


class ImageSelector:
    def __init__(self, settings: ImageFilterSettings, http: AsyncClient) -> None:
        self._settings = settings
        self._http = http

    async def select(self, html: str, base_url: str) -> ImageSelection:
        candidates = self._extract_candidates(html, base_url)
        if not candidates:
            return ImageSelection(url=None, status=ImageStatus.NO_IMAGE)

        saw_error = False
        for url in candidates:
            if self._is_rejected_url(url):
                continue
            status = await self._validate_image(url)
            if status == ImageStatus.OK:
                return ImageSelection(url=url, status=ImageStatus.OK)
            if status == ImageStatus.ERROR:
                saw_error = True
        return ImageSelection(
            url=None,
            status=ImageStatus.ERROR if saw_error else ImageStatus.REJECTED,
        )

    def _extract_candidates(self, html: str, base_url: str) -> list[str]:
        try:
            doc = lxml_html.fromstring(html)
        except Exception:
            return []

        urls: list[str] = []
        for xpath in [
            "//meta[@property='og:image']/@content",
            "//meta[@name='twitter:image']/@content",
            "//meta[@name='twitter:image:src']/@content",
        ]:
            urls.extend(doc.xpath(xpath))

        if not urls:
            urls.extend(doc.xpath("//img/@src"))

        result = []
        for url in urls:
            if not url:
                continue
            result.append(make_absolute(url, base_url))
        return result

    def _is_rejected_url(self, url: str) -> bool:
        lowered = url.lower()
        for ext in self._settings.reject_extensions:
            if lowered.endswith(ext):
                return True
        for keyword in self._settings.reject_path_keywords:
            if keyword in lowered:
                return True
        return False

    async def _validate_image(self, url: str) -> ImageStatus:
        try:
            response = await self._http.get(url, timeout=10)
        except Exception:
            return ImageStatus.ERROR
        if response.status_code >= 400:
            return ImageStatus.ERROR
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_IMAGE_BYTES:
                    return ImageStatus.REJECTED
            except (ValueError, TypeError):
                pass
        if len(response.content) > MAX_IMAGE_BYTES:
            return ImageStatus.REJECTED

        try:
            with Image.open(BytesIO(response.content)) as img:
                width, height = img.size
        except Exception:
            return ImageStatus.REJECTED

        if width < self._settings.min_width or height < self._settings.min_height:
            return ImageStatus.REJECTED
        ratio = width / height if height else 0
        if ratio < self._settings.min_aspect_ratio or ratio > self._settings.max_aspect_ratio:
            return ImageStatus.REJECTED
        return ImageStatus.OK
