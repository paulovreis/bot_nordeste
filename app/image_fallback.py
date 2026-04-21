from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from .enrich import _is_bad_image  # reuse the same filters

log = logging.getLogger(__name__)


async def find_commons_image(client: httpx.AsyncClient, query: str) -> str | None:
    """Best-effort free fallback image search.

    Uses Wikimedia Commons API (no paid key) and returns a direct image URL.
    """

    q = (query or "").strip()
    if not q:
        return None

    try:
        r = await client.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": q,
                "gsrnamespace": 6,  # File:
                "gsrlimit": 6,
                "prop": "imageinfo",
                "iiprop": "url",
                "iiurlwidth": 1280,
            },
            follow_redirects=True,
        )
        r.raise_for_status()
        data = r.json()

        pages = (data.get("query") or {}).get("pages") or {}
        # pages is a dict keyed by pageid
        for _, page in pages.items():
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            url = info.get("thumburl") or info.get("url")
            if not url:
                continue
            if _is_bad_image(url):
                continue
            p = urlparse(url)
            if p.scheme not in {"http", "https"}:
                continue
            return url

        return None
    except Exception as e:
        log.debug("commons_image_search_failed", extra={"q": q, "err": str(e)})
        return None
