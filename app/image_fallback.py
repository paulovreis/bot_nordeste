from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

import httpx

from .claude import choose_image_url
from .enrich import _is_bad_image
from .util import extract_keywords

log = logging.getLogger(__name__)


async def _openverse_candidates(client: httpx.AsyncClient, query: str) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        r = await client.get(
            "https://api.openverse.org/v1/images",
            params={
                "q": q,
                "page_size": 18,
                "license_type": "all",
                "mature": "false",
            },
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
                "User-Agent": "bot-nordeste/1.0",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(15.0),
        )
        if r.status_code != 200:
            log.debug("openverse_http_error", extra={"q": q, "status": r.status_code})
            return []
        data = json.loads(r.content) or {}
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
        log.debug("openverse_search_failed", extra={"q": q, "err": str(e)})
        return []


async def get_fallback_image(
    client: httpx.AsyncClient,
    query: str,
    *,
    claude_token: str | None = None,
    claude_model: str = "claude-3-5-haiku-latest",
) -> str | None:
    """Fallback image via Openverse + Claude ranking. Returns None if nothing found."""
    q = (query or "").strip()
    if not q or not claude_token:
        return None

    tokens = extract_keywords(q, max_tokens=10)
    search_q = " ".join(tokens) if tokens else q

    cands = await _openverse_candidates(client, search_q)
    if not cands and search_q != q:
        cands = await _openverse_candidates(client, q)
    if not cands:
        return None

    picked = await choose_image_url(
        client,
        token=claude_token,
        model=claude_model,
        article_title=q,
        candidates=cands,
    )
    if picked and not _is_bad_image(picked):
        return picked
    return None
