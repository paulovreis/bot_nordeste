from __future__ import annotations
import logging
import os
import re
from dataclasses import dataclass
import httpx

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


@dataclass(frozen=True)
class ResolvedItem:
    final_url: str
    image_url: str | None


class BrowserResolver:
    def __init__(self, **kwargs):
        # Carrega todas as chaves separadas por vírgula
        keys_env = os.getenv(
            "SCRAPER_API_KEYS", os.getenv("SCRAPER_API_KEY", "")
        ).strip()
        self.api_keys = [k.strip() for k in keys_env.split(",") if k.strip()]
        self.current_key_index = 0
        self.api_url = "http://api.scraperapi.com"

    @property
    def active_key(self) -> str | None:
        return self.api_keys[self.current_key_index] if self.api_keys else None

    def _rotate_key(self):
        if len(self.api_keys) > 1:
            self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
            log.warning(
                "scraper_api_key_rotated", extra={"new_index": self.current_key_index}
            )

    async def start(self) -> None:
        if not self.api_keys:
            log.error("Nenhuma SCRAPER_API_KEY configurada!")
        log.info(
            "scraper_api_resolver_started", extra={"total_keys": len(self.api_keys)}
        )

    async def stop(self) -> None:
        pass

    async def resolve(self, url: str) -> ResolvedItem | None:
        if "news.google.com" not in url:
            return ResolvedItem(final_url=url, image_url=None)

        if not self.api_keys:
            log.warning("scraper_api_no_key")
            return None

        max_attempts = len(
            self.api_keys
        )  # Tenta no máximo o número de chaves que você tem

        async with httpx.AsyncClient(timeout=60.0) as client:
            for _ in range(max_attempts):
                params = {
                    "api_key": self.api_key,
                    # Garanta que a URL sempre tenha o prefixo correto
                    "url": url if url.startswith("http") else f"https://{url}",
                    "follow_redirect": "true",
                    "render": "true",  # Alterado: Necessário para processar redirecionamentos JS do Google
                    "premium": "true",  # Novo: Usa pool de proxies premium que não estão bloqueados pelo Google
                    "country_code": "br",
                }

                try:
                    r = await client.get(self.api_url, params=params)

                    # 403 ou 429 indica que os créditos da chave atual acabaram
                    if r.status_code in (403, 429):
                        self._rotate_key()
                        continue  # Tenta de novo com a nova chave

                    if r.status_code != 200:
                        print(f"Erro ao acessar ScraperAPI: {r.status_code} - {r.text}")
                        log.warning(
                            "scraper_api_error", extra={"status": r.status_code}
                        )
                        return None

                    final_url, image_url = _extract_from_html(r.content)

                    if final_url:
                        return ResolvedItem(final_url=final_url, image_url=image_url)

                    return None

                except Exception as exc:
                    print(f"Erro ao acessar ScraperAPI: {exc}")
                    log.warning("scraper_api_failed", extra={"err": str(exc)})
                    return None

        return None
