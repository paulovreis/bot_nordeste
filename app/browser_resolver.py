from __future__ import annotations
import logging
import os
from dataclasses import dataclass
import httpx

log = logging.getLogger(__name__)

@dataclass(frozen=True)
class ResolvedItem:
    final_url: str
    image_url: str | None

class BrowserResolver:
    """Resolve Google News redirect URLs via ScraperAPI."""

    def __init__(self, **kwargs):
        self.api_key = os.getenv("SCRAPER_API_KEY", "").strip()
        self.api_url = "http://api.scraperapi.com"

    async def start(self) -> None:
        if not self.api_key or self.api_key == "SUA_API_KEY_AQUI":
            log.error("SCRAPER_API_KEY não configurada nas variáveis de ambiente do Docker!")
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
            # Timeout aumentado para 60s
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.get(self.api_url, params=params)
                
                # Se a chave for inválida ou os créditos acabarem, logamos o motivo exato
                if r.status_code != 200:
                    log.warning("scraper_api_error", extra={"status": r.status_code, "body": r.text})
                    return None

                # O ScraperAPI injeta a URL final resolvida neste header
                final_url = r.headers.get("sa-final-url")

                if final_url and "google.com" not in final_url:
                    log.info("scraper_api_resolved", extra={"original": url, "final": final_url})
                    # Devolvemos a URL limpa. O enrich.py nativo do sistema vai buscar a imagem perfeita.
                    return ResolvedItem(final_url=final_url, image_url=None)

                log.warning("scraper_api_unresolved", extra={"url": url, "returned_url": final_url})

        except httpx.TimeoutException:
            log.warning("scraper_api_timeout", extra={"url": url})
        except Exception as exc:
            log.warning("scraper_api_failed", extra={"url": url, "err": str(exc)})

        return None