from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright_stealth import stealth_async as _stealth_async

log = logging.getLogger(__name__)

# AGORA SIM! Liberei 'image' e 'stylesheet' para imitar um usuário real.
# Só estamos bloqueando fontes pesadas e vídeos para poupar CPU/Rede.
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
_JS_REDIRECT_TIMEOUT_MS = 12_000
_NAV_TIMEOUT_MS = 30_000

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
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=_LAUNCH_ARGS,
        )
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
                    ),
                    extra_http_headers={
                        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    },
                )

                # Cookies modernos para burlar o consent.google.com
                await context.add_cookies([
                    {
                        "name": "SOCS",
                        "value": "CAESHAgCEhJnd3NfMjAyMzA4MTAtMF9SQzI6cHQtQlI6QU0aCgkJGAgJCgkJGAg=",
                        "domain": ".google.com",
                        "path": "/"
                    },
                    {
                        "name": "CONSENT",
                        "value": "PENDING+900",
                        "domain": ".google.com",
                        "path": "/"
                    }
                ])

                page = await context.new_page()

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

                # Aguarda até que o domínio não seja mais google.com
                try:
                    await page.wait_for_url(
                        lambda u: "google.com" not in urlparse(u).netloc,
                        timeout=_JS_REDIRECT_TIMEOUT_MS,
                    )
                except Exception:
                    pass

                final_url = page.url

                # Plano de Segurança Máxima: se ainda estiver preso no Google
                if "google.com" in urlparse(final_url).netloc:
                    extracted_url = await page.evaluate('''() => {
                        // 1. Pega a URL do atributo c-wiz (padrão atual do interstitial)
                        const cwiz = document.querySelector('c-wiz[data-n-v-url]');
                        if (cwiz) return cwiz.getAttribute('data-n-v-url');
                        
                        // 2. Busca qualquer link 'a' que não aponte para o google
                        const links = Array.from(document.querySelectorAll('a'));
                        for (const a of links) {
                            if (a.href && a.href.startsWith('http') && !a.href.includes('google.com')) {
                                return a.href;
                            }
                        }

                        // 3. Tenta forçar clique em botões de "Aceitar Tudo" caso não tenha saído do consent
                        const btns = Array.from(document.querySelectorAll('button'));
                        const acceptBtn = btns.find(b => /(aceitar|accept|concordo)/i.test(b.innerText));
                        if (acceptBtn) acceptBtn.click();
                        else {
                            const forms = document.querySelectorAll('form');
                            if (forms.length > 0) forms[0].submit();
                        }

                        return null;
                    }''')
                    
                    if extracted_url:
                        final_url = extracted_url
                    else:
                        # Dá uma margem de tempo caso ele tenha acabado de clicar no botão "Aceitar"
                        try:
                            await page.wait_for_url(
                                lambda u: "google.com" not in urlparse(u).netloc,
                                timeout=4000,
                            )
                            final_url = page.url
                        except Exception:
                            pass

                # Se ao fim de tudo ele não saiu, registra a falha
                if "google.com" in urlparse(final_url).netloc:
                    log.warning("browser_bypass_failed", extra={"url": url, "final_url": final_url})
                    return None

                log.debug("browser_resolved", extra={"original": url, "final": final_url})
                
                # Retorna NONE na imagem para forçar o enrich.py a fazer a extração profunda da foto real
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