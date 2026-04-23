from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

_BLOCKED_RESOURCE_TYPES = frozenset({"stylesheet", "font", "image", "media"})

# Chromium flags otimizados para ambiente VPS (1 vCPU / 4GB RAM)
_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-zygote",                          # evita processo zygote; mais leve que --single-process
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

# Aguarda até a URL não estar mais no Google News (JS redirect)
_GOOGLE_NEWS_HOST = "news.google.com"
_JS_REDIRECT_TIMEOUT_MS = 10_000
_NAV_TIMEOUT_MS = 25_000


@dataclass(frozen=True)
class ResolvedItem:
    final_url: str
    image_url: str | None


class BrowserResolver:
    """Gerenciador de browser Playwright reutilizável e otimizado para VPS de baixo recurso.

    Lifecycle:
        resolver = BrowserResolver()
        await resolver.start()
        try:
            result = await resolver.resolve("https://news.google.com/...")
        finally:
            await resolver.stop()
    """

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
        """Navega até *url*, aguarda redirecionamentos e extrai URL final + og:image.

        Abre um contexto isolado (cookies zerados) por chamada e o fecha ao terminar.
        Retorna None em caso de falha.
        """
        if self._browser is None:
            raise RuntimeError("BrowserResolver não iniciado — chame start() antes de resolve().")

        async with self._sem:
            context = None
            try:
                from playwright_stealth import stealth_async
            except ImportError:
                log.error("playwright_stealth não instalado: pip install playwright-stealth")
                return None

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

                page = await context.new_page()

                # Intercepta e aborta recursos pesados para poupar CPU/rede
                async def _handle_route(route):
                    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
                        await route.abort()
                    else:
                        await route.continue_()

                await page.route("**/*", _handle_route)
                await stealth_async(page)

                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self._nav_timeout,
                )

                # Google News faz JS redirect — aguarda sair do domínio
                if _GOOGLE_NEWS_HOST in page.url:
                    try:
                        await page.wait_for_url(
                            lambda u: _GOOGLE_NEWS_HOST not in u,
                            timeout=_JS_REDIRECT_TIMEOUT_MS,
                        )
                    except Exception:
                        log.debug(
                            "browser_still_on_google_news",
                            extra={"url": url, "current": page.url},
                        )

                final_url = page.url

                # Extrai og:image (e fallback twitter:image) via JS no contexto da página
                image_url: str | None = await page.evaluate(
                    """
                    () => {
                        const sel = [
                            'meta[property="og:image"]',
                            'meta[property="og:image:url"]',
                            'meta[name="twitter:image"]',
                            'meta[name="twitter:image:src"]',
                        ];
                        for (const s of sel) {
                            const m = document.querySelector(s);
                            if (m) {
                                const v = m.getAttribute('content');
                                if (v && v.trim()) return v.trim();
                            }
                        }
                        return null;
                    }
                    """
                )

                log.debug(
                    "browser_resolved",
                    extra={
                        "original": url,
                        "final": final_url,
                        "has_image": bool(image_url),
                    },
                )
                return ResolvedItem(
                    final_url=final_url,
                    image_url=image_url or None,
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
