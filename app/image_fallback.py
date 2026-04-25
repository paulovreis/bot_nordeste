from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from .claude import choose_image_url
from .enrich import _is_bad_image  # reuse the same filters
from .util import extract_keywords

log = logging.getLogger(__name__)


async def _openverse_candidates(client: httpx.AsyncClient, query: str) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        r = await client.get(
            "https://api.openverse.engineering/v1/images",
            params={
                "q": q,
                "page_size": 18,
                "license_type": "all",
                "mature": "false",
            },
            follow_redirects=True,
        )
        r.raise_for_status()
        data = r.json() or {}
        results = data.get("results") or []
        out: list[dict] = []
        for it in results:
            url = (it.get("url") or "").strip()
            if not url or _is_bad_image(url):
                continue
            p = urlparse(url)
            if p.scheme not in {"http", "https"}:
                continue
            out.append(
                {
                    "url": url,
                    "title": (it.get("title") or "").strip(),
                    "creator": (it.get("creator") or "").strip(),
                    "license": (it.get("license") or "").strip(),
                    "source": "openverse",
                }
            )
        return out
    except Exception as e:
        print(f"Erro ao acessar Openverse: {q} - {str(e)}")
        log.debug("openverse_search_failed", extra={"q": q, "err": str(e)})
        return []


async def find_claude_image(
    client: httpx.AsyncClient,
    *,
    claude_token: str,
    claude_model: str,
    title: str,
) -> str | None:
    """Claude-assisted image fallback.

    We search for real candidates via Openverse, then ask Claude to pick the best.
    """
    if not claude_token:
        return None

    tokens = extract_keywords(title, max_tokens=10)
    q = " ".join(tokens) if tokens else (title or "")

    cands = await _openverse_candidates(client, q)
    if not cands and q != title:
        cands = await _openverse_candidates(client, title)
    if not cands:
        return None

    picked = await choose_image_url(
        client,
        token=claude_token,
        model=claude_model,
        article_title=title,
        candidates=cands,
    )
    if picked and not _is_bad_image(picked):
        return picked
    return None


async def find_wikipedia_image(client: httpx.AsyncClient, query: str) -> str | None:
    """Try to get a relevant thumbnail from Portuguese Wikipedia.

    This tends to work well for places, public works, and public figures.
    """
    q = (query or "").strip()
    if not q:
        return None

    try:
        r = await client.get(
            "https://pt.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "list": "search",
                "srsearch": q,
                "srlimit": 1,
                "utf8": 1,
            },
            follow_redirects=True,
        )
        r.raise_for_status()
        data = r.json()
        hits = ((data.get("query") or {}).get("search") or [])
        if not hits:
            return None
        title = hits[0].get("title")
        if not title:
            return None

        r2 = await client.get(
            "https://pt.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "prop": "pageimages",
                "piprop": "thumbnail",
                "pithumbsize": 1280,
                "titles": title,
                "utf8": 1,
            },
            follow_redirects=True,
        )
        r2.raise_for_status()
        data2 = r2.json()
        pages = ((data2.get("query") or {}).get("pages") or {})
        for _, page in pages.items():
            thumb = page.get("thumbnail") or {}
            url = (thumb.get("source") or "").strip()
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
        log.debug("wikipedia_image_search_failed", extra={"q": q, "err": str(e)})
        return None


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


async def get_fallback_image(
    client: httpx.AsyncClient,
    query: str,
    *,
    claude_token: str | None = None,
    claude_model: str = "claude-3-5-haiku-latest",
) -> str | None:
    """Fallback chain for when the article page has no usable image.

    Order: Claude(Openverse) -> Wikipedia -> Commons.
    """
    q = (query or "").strip()
    if not q:
        return None

    if claude_token:
        url = await find_claude_image(client, claude_token=claude_token, claude_model=claude_model, title=q)
        if url:
            return url

    url = await find_wikipedia_image(client, q)
    if url:
        return url

    return await find_commons_image(client, q)
