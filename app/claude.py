from __future__ import annotations

import json
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class ClaudeError(RuntimeError):
    pass


async def choose_image_url(
    client: httpx.AsyncClient,
    *,
    token: str,
    model: str,
    article_title: str,
    candidates: list[dict[str, Any]],
) -> str | None:
    """Ask Claude to pick the best image URL from a list of real candidates.

    We intentionally *do not* ask Claude to browse or invent URLs.
    """

    if not token.strip():
        return None
    if not candidates:
        return None

    # Keep payload small and stable.
    short = []
    for c in candidates[:18]:
        short.append(
            {
                "url": c.get("url"),
                "title": c.get("title"),
                "creator": c.get("creator"),
                "license": c.get("license"),
                "source": c.get("source"),
            }
        )

    sys = (
        "You are selecting an image for a Brazilian news Telegram bot. "
        "Pick ONE best candidate URL from the provided list. "
        "Rules: never pick logos, favicons, icons, avatars, brand banners, or placeholders. "
        "Prefer a photo/illustration that plausibly represents the article topic. "
        "If nothing is good, return null. "
        "Return strictly JSON: {\"url\": string|null}."
    )

    user = {
        "article_title": article_title,
        "candidates": short,
    }

    try:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": token,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 150,
                "temperature": 0,
                "system": sys,
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(user, ensure_ascii=False),
                    }
                ],
            },
        )
        r.raise_for_status()
        data = r.json()

        # Anthropic messages: content is a list of blocks.
        blocks = data.get("content") or []
        text = "".join((b.get("text") or "") for b in blocks if b.get("type") == "text").strip()
        if not text:
            return None

        try:
            obj = json.loads(text)
        except Exception:
            # Try to salvage if Claude returned extra text.
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                obj = json.loads(text[start : end + 1])
            else:
                return None

        url = (obj.get("url") or "").strip() if isinstance(obj, dict) else ""
        if not url:
            return None

        # Must be one of the provided candidates (avoid hallucination).
        cand_urls = {str(c.get("url") or "").strip() for c in short}
        return url if url in cand_urls else None

    except httpx.HTTPStatusError as e:
        # Don't leak token or payload.
        log.debug("claude_http_error", extra={"status": e.response.status_code})
        return None
    except Exception as e:
        log.debug("claude_choose_failed", extra={"err": str(e)})
        return None
