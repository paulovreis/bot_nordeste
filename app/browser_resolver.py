from __future__ import annotations
import logging
import os
import re
from dataclasses import dataclass
import httpx

log = logging.getLogger(__name__)

# Regex para encontrar a URL final e a Imagem dentro do HTML retornado pelo proxy
_OG_URL_RE = re.compile(rb'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']', re.I)
_CANONICAL_RE = re.compile(rb'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', re.I)
_OG_IMAGE_RE = re.compile(rb'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I)

def _extract_from_html(body: bytes) -> tuple[str | None, str | None]:
    """Extrai final_url e image_url do HTML processado pelo ScraperAPI."""
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
        # ATENÇÃO: Insira a SUA chave aqui, ou configure a variável de ambiente.
        print("Api key: ", os.getenv("SCRAPER_API_KEY", "").strip())
        self.api_key = os.getenv("SCRAPER_API_KEY", "").strip()
        self.api_url = "http://api.scraperapi.com"

    async def start(self) -> None:
        if not self.api_key or self.api_key == "ab6206d826ecf3a34d93afec797d133c":
            log.error("SCRAPER_API_KEY inválida ou não configurada!")
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
            # 60s de timeout porque os proxies da ScraperAPI podem demorar um pouco para responder
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.get(self.api_url, params=params)
                
                # Se não retornou 200, a chave está errada ou os créditos acabaram
                if r.status_code != 200:
                    log.warning("scraper_api_error", extra={"status": r.status_code, "body": r.text})
                    return None

                # Extrai a URL verdadeira e a Imagem direto do HTML fornecido
                final_url, image_url = _extract_from_html(r.content)

                if final_url:
                    log.info("scraper_api_resolved", extra={"original": url, "final": final_url})
                    # Como já pegamos a imagem por regex aqui, passamos pro main.py.
                    return ResolvedItem(final_url=final_url, image_url=image_url)

                log.warning("scraper_api_unresolved", extra={"url": url})

        except Exception as exc:
            log.warning("scraper_api_failed", extra={"url": url, "err": str(exc)})

        return None