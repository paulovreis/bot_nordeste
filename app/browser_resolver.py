from __future__ import annotations
import datetime
import logging
import os
import re
import sqlite3
from dataclasses import dataclass

import httpx

from . import db

log = logging.getLogger(__name__)

_OG_URL_RE = re.compile(
    rb'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']', re.I
)
_CANONICAL_RE = re.compile(
    rb'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', re.I
)
_OG_IMAGE_RE = re.compile(
    rb'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I
)

_CREDIT_ERROR_PHRASES = ("invalid api key", "out of credits", "quota", "unauthorized", "api credits")


def _extract_from_html(body: bytes) -> tuple[str | None, str | None]:
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


def _next_month_first(d: datetime.date) -> datetime.date:
    if d.month == 12:
        return datetime.date(d.year + 1, 1, 1)
    return datetime.date(d.year, d.month + 1, 1)


@dataclass(frozen=True)
class ResolvedItem:
    final_url: str
    image_url: str | None


class BrowserResolver:
    def __init__(self, conn: sqlite3.Connection):
        keys_env = os.getenv("SCRAPER_API_KEYS", "").strip()
        self.api_keys: list[str] = [k.strip() for k in keys_env.split(",") if k.strip()]
        self._conn = conn
        self._exhausted: dict[str, datetime.date] = {}
        self.api_url = "http://api.scraperapi.com"

    def _available_keys(self) -> list[str]:
        today = datetime.date.today()
        return [
            k for k in self.api_keys
            if k not in self._exhausted or today >= _next_month_first(self._exhausted[k])
        ]

    def _mark_exhausted(self, key: str) -> None:
        today = datetime.date.today()
        self._exhausted[key] = today
        db.save_key_exhausted(self._conn, key, today)
        print(f"Chave ScraperAPI esgotada: {key[-4:]}, marcada como esgotada em {today}")
        log.warning(
            "scraper_api_key_exhausted",
            extra={"key_suffix": key[-4:], "available_remaining": len(self._available_keys())},
        )

    async def start(self) -> None:
        self._exhausted = db.load_exhausted_keys(self._conn)
        if not self.api_keys:
            log.error("Nenhuma SCRAPER_API_KEY configurada!")
        log.info(
            "scraper_api_resolver_started",
            extra={"total_keys": len(self.api_keys), "exhausted_keys": len(self._exhausted)},
        )

    async def stop(self) -> None:
        pass

    async def resolve(self, url: str) -> ResolvedItem | None:
        if "news.google.com" not in url:
            return ResolvedItem(final_url=url, image_url=None)

        available = self._available_keys()
        if not available:
            log.warning("scraper_api_no_key_available")
            return None

        target_url = url if url.startswith("http") else f"https://{url}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            for key in available:
                params = {
                    "api_key": key,
                    "url": target_url,
                    "follow_redirect": "true",
                    "render": "true",
                    "premium": "true",
                    "country_code": "br",
                }
                try:
                    r = await client.get(self.api_url, params=params)

                    if r.status_code in (403, 429):
                        body_lower = r.text.lower()
                        print(f"Resposta do ScraperAPI: {r.status_code} - {r.text}")
                        if any(p in body_lower for p in _CREDIT_ERROR_PHRASES):
                            self._mark_exhausted(key)
                            continue
                        print(f"Erro ao acessar ScraperAPI: {r.status_code} - {r.text}")
                        log.warning("scraper_api_target_403", extra={"url": url, "status": r.status_code})
                        return None

                    if r.status_code != 200:
                        print(f"Erro ao acessar ScraperAPI: {r.status_code} - {r.text}")
                        log.warning("scraper_api_error", extra={"status": r.status_code})
                        return None

                    final_url, image_url = _extract_from_html(r.content)
                    return ResolvedItem(final_url=final_url, image_url=image_url) if final_url else None

                except Exception as exc:
                    print(f"Erro ao acessar ScraperAPI: {exc}")
                    log.warning("scraper_api_failed", extra={"err": str(exc)})
                    return None

        return None
