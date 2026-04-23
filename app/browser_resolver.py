from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from playwright_stealth import stealth_async as _stealth_async

log = logging.getLogger(__name__)

# Liberei 'image' e 'stylesheet' porque o Google precisa deles para executar o JS de redirect
_BLOCKED_RESOURCE_TYPES = frozenset({"font", "media"})

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
    def __init__(self, *, max_concurrent: int = 2, nav_timeout_ms: int = _NAV_TIMEOUT_MS):
        self._sem = asyncio.Semaphore(max_concurrent)
        self._nav_timeout = nav_timeout_ms
        self._playwright = None
        self._browser = None

    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("playwright não instalado.") from exc

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        log.info("browser_resolver_started")

    async def stop(self) -> None:
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
        if self._browser is None:
            raise RuntimeError("BrowserResolver não iniciado.")

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
                    )
                )

                await context.add_cookies([{
                    "name": "CONSENT",
                    "value": "YES+cb.20230101-01-p0.pt-BR+FX+410",
                    "domain": ".google.com",
                    "path": "/"
                }])

                page = await context.new_page()

                async def _handle_route(route):
                    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
                        await route.abort()
                    else:
                        await route.continue_()

                await page.route("**/*", _handle_route)
                await _stealth_async(page)

                await page.goto(url, wait_until="domcontentloaded", timeout=self._nav_timeout)

                if _GOOGLE_NEWS_HOST in page.url:
                    try:
                        await page.wait_for_url(
                            lambda u: _GOOGLE_NEWS_HOST not in u and "consent.google.com" not in u,
                            timeout=_JS_REDIRECT_TIMEOUT_MS,
                        )
                    except Exception:
                        pass

                final_url = page.url

                # Extração à força pelo DOM caso o redirect JS falhe
                if _GOOGLE_NEWS_HOST in final_url or "consent.google.com" in final_url:
                    extracted_url = await page.evaluate('''() => {
                        const cwiz = document.querySelector('c-wiz[data-n-v-url]');
                        if (cwiz) return cwiz.getAttribute('data-n-v-url');
                        
                        const links = Array.from(document.querySelectorAll('a'));
                        const realLink = links.find(a => a.href && !a.href.includes('google.com'));
                        return realLink ? realLink.href : null;
                    }''')
                    
                    if extracted_url:
                        final_url = extracted_url
                    else:
                        log.warning("browser_bypass_failed", extra={"url": url, "final_url": final_url})
                        return None

                # Se o Google bater de frente com o Captcha
                if "sorry/index" in final_url:
                    log.warning("captcha_hit", extra={"url": url})
                    return None

                log.debug("browser_resolved", extra={"original": url, "final": final_url})
                
                return ResolvedItem(final_url=final_url, image_url=None)

            except Exception as exc:
                log.warning("browser_resolve_failed", extra={"url": url, "err": str(exc)})
                return None
            finally:
                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass