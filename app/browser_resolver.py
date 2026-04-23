from __future__ import annotations
import logging
from dataclasses import dataclass
import httpx
import os

log = logging.getLogger(__name__)

@dataclass(frozen=True)
class ResolvedItem:
    final_url: str
    image_url: str | None

class BrowserResolver:
    """Gerenciador via ScraperAPI para burlar 429 com custo zero."""

    def __init__(self, **kwargs):
        # Cadastre-se em scraperapi.com para obter sua chave gratuita
        self.api_key = os.getenv("SCRAPER_API_KEY", default=None)
        self.api_url = "http://api.scraperapi.com"

    async def start(self) -> None:
        log.info("scraper_api_resolver_started")

    async def stop(self) -> None:
        pass

    async def resolve(self, url: str) -> ResolvedItem | None:
        if "news.google.com" not in url:
            return ResolvedItem(final_url=url, image_url=None)

        params = {
            'api_key': self.api_key,
            'url': url,
            'follow_redirect': 'true',
            'render': 'false' # 'false' é mais barato e rápido apenas para resolver a URL
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(self.api_url, params=params)
                
                # O ScraperAPI retorna a URL final nos headers ou você pode extrair do corpo
                final_url = r.url
                
                # Se ainda caiu no Google, você pode ativar o 'render': 'true' 
                # para carregar o JavaScript, mas isso consome mais créditos.
                
                if "google.com" not in str(final_url):
                    return ResolvedItem(final_url=str(final_url), image_url=None)
                
        except Exception as exc:
            log.warning("scraper_api_failed", extra={"url": url, "err": str(exc)})
            
        return None