from __future__ import annotations
import logging
import re
from dataclasses import dataclass
import httpx
import os

log = logging.getLogger(__name__)

_OG_URL_RE = re.compile(rb'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']', re.I)
_CANONICAL_RE = re.compile(rb'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', re.I)
_OG_IMAGE_RE = re.compile(rb'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I)


def _extract_from_html(body: bytes) -> tuple[str | None, str | None]:
    """Return (final_url, image_url) from page HTML using og/canonical tags."""
    url: str | None = None
    image: str | None = None

    for pattern in (_OG_URL_RE, _CANONICAL_RE):
        m = pattern.search(body)
        if m:
            candidate = m.group(1).decode("utf-8", errors="replace").strip()
            if candidate.startswith("http") and "google.com" not in candidate:
                url = candidate
                break

    m = _OG_IMAGE_RE.search(body)
    if m:
        img = m.group(1).decode("utf-8", errors="replace").strip()
        if img.startswith("http"):
            image = img

    return url, image


@dataclass(frozen=True)
class ResolvedItem:
    final_url: str
    image_url: str | None

class BrowserResolver:
    """Resolve Google News redirect URLs via ScraperAPI."""

    def __init__(self, **kwargs):
        self.api_key = os.getenv("SCRAPER_API_KEY", default=None)
        self.api_url = "http://api.scraperapi.com"

    async def start(self) -> None:
        log.info("scraper_api_resolver_started")

    async def stop(self) -> None:
        pass

    async def resolve(self, url: str) -> ResolvedItem | None:
        if "news.google.com" not in url:
            return ResolvedItem(final_url=url, image_url=None)

        if not self.api_key:
            log.warning("scraper_api_no_key", extra={"url": url})
            return None

        params = {
            "api_key": self.api_key,
            "url": url,
            "follow_redirect": "true",
            "render": "false",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                r = await client.get(self.api_url, params=params)
                r.raise_for_status()

                # ScraperAPI fetches the target page server-side and returns its content.
                # The actual resolved URL must be extracted from the response body.
                final_url, image_url = _extract_from_html(r.content)

                if final_url:
                    log.info("scraper_api_resolved", extra={"original": url, "final": final_url})
                    return ResolvedItem(final_url=final_url, image_url=image_url)

                # Fallback: check if httpx followed an HTTP-level redirect away from google.com
                str_url = str(r.url)
                if "google.com" not in str_url and str_url.startswith("http"):
                    return ResolvedItem(final_url=str_url, image_url=None)

                log.warning("scraper_api_unresolved", extra={"url": url, "status": r.status_code})

        except Exception as exc:
            log.warning("scraper_api_failed", extra={"url": url, "err": str(exc)})

        return None
