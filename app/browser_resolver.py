from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from playwright_stealth import stealth_async as _stealth_async  # falha no startup se não instalado

log = logging.getLogger(__name__)

_BLOCKED_RESOURCE_TYPES = frozenset({"stylesheet", "font", "image", "media"})

# Chromium flags otimizados para ambiente VPS (1 vCPU / 4GB RAM)
_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-zygote",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-translate",
    "--no-first-run",
    "--no-default-browser-check",
    "--mute-audio",
    "--disable-notifications",
    "--disable-default-apps",
    "--disable-hang-monitor",
    "--disable-prompt-on-repost",
    "--disable-accelerated-2d-canvas",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-ipc-flooding-protection",
    "--disable-client-side-phishing-detection",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-features=AudioServiceOutOfProcess,IsolateOrigins,site-per-process",
]

_GOOGLE_NEWS_HOST = "news.google.com"
_JS_REDIRECT_TIMEOUT_MS = 10_000
_NAV_TIMEOUT_MS = 25_000


@dataclass(frozen=True)
class ResolvedItem:
    final_url: str
    image_url: str | None


class BrowserResolver:
    """Gerenciador de browser Playwright reutilizável e otimizado para VPS de baixo recurso."""

    def __init__(self, *, max_concurrent: int = 2, nav_timeout_ms: int = _NAV_TIMEOUT_MS):
        self._sem = asyncio.Semaphore(max_concurrent)
        self._nav_timeout = nav_timeout_ms
        self._playwright = None
        self._browser = None

    async def start(self) -> None:
        """Inicia o Playwright e abre uma única instância do Chromium."""
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright não instalado. Execute: pip install playwright && playwright install chromium"
            ) from exc

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=_LAUNCH_ARGS,
        )
        log.info("browser_resolver_started")

    async def stop(self) -> None:
        """Fecha o browser e o Playwright de forma segura."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        log.info("browser_resolver_stopped")

    async def resolve(self, url: str) -> ResolvedItem | None:
        """Navega até a URL, passa pelo bloqueio do Google e extrai apenas a URL final."""
        if self._browser is None:
            raise RuntimeError("BrowserResolver não iniciado — chame start() antes de resolve().")

        async with self._sem:
            context = None
            try:
                context = await self._browser.new_context(
                    java_script_enabled=True,
                    bypass_csp=True,
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    extra_http_headers={
                        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                        "Accept": (
                            "text/html,application/xhtml+xml,application/xml;"
                            "q=0.9,image/webp,*/*;q=0.8"
                        ),
                    },
                )

                # SOCS é o cookie de consentimento atual do Google (substituiu CONSENT em 2023)
                await context.add_cookies([
                    {
                        "name": "SOCS",
                        "value": "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg",
                        "domain": ".google.com",
                        "path": "/",
                    },
                    {
                        "name": "CONSENT",
                        "value": "YES+cb.20230101-07-p0.pt-BR+FX+410",
                        "domain": ".google.com",
                        "path": "/",
                    },
                ])

                page = await context.new_page()

                # Intercepta e aborta recursos pesados para poupar CPU/rede
                async def _handle_route(route):
                    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
                        await route.abort()
                    else:
                        await route.continue_()

                await page.route("**/*", _handle_route)
                await _stealth_async(page)

                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self._nav_timeout,
                )

                # Aguarda até que a URL final deixe de ser o Google News ou o Consent
                if _GOOGLE_NEWS_HOST in page.url:
                    try:
                        await page.wait_for_url(
                            lambda u: _GOOGLE_NEWS_HOST not in u and "consent.google.com" not in u,
                            timeout=_JS_REDIRECT_TIMEOUT_MS,
                        )
                    except Exception:
                        pass

                final_url = page.url

                # Se falhou e continua no Google, descarta para tentar de novo mais tarde
                if _GOOGLE_NEWS_HOST in final_url or "consent.google.com" in final_url:
                    log.warning("browser_bypass_failed", extra={"url": url, "final_url": final_url})
                    return None

                log.debug("browser_resolved", extra={"original": url, "final": final_url})
                
                # Devolvemos a imagem vazia de propósito!
                # Isso forçará o main.py a acionar o fetch_og e usar o seu enrich.py 
                # para buscar as imagens de alta qualidade direto no corpo do site.
                return ResolvedItem(
                    final_url=final_url,
                    image_url=None, 
                )

            except Exception as exc:
                log.warning("browser_resolve_failed", extra={"url": url, "err": str(exc)})
                return None
            finally:
                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass