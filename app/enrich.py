from __future__ import annotations

import logging
import json
import re
from typing import NamedTuple
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from .util import normalize_text

log = logging.getLogger(__name__)


class OgData(NamedTuple):
    image_url: str | None
    description: str | None
    site_name: str | None
    canonical_url: str | None
    final_url: str | None
    has_img_tag: bool
    parsed_html: bool


_BAD_IMAGE_HINTS = (
    "logo",
    "brand",
    "branding",
    "favicon",
    "icon",
    "avatar",
    "profile",
    "sprite",
    "share",
    "social",
    "default",
    "placeholder",
    "ads",
    "banner",
    "header",
)

_BAD_PATH_HINTS = (
    "/themes/",
    "/theme/",
    "/assets/logo",
    "/assets/brand",
    "/static/logo",
    "/static/brand",
    "/logos/",
    "/logo/",
)

_BAD_ANCESTOR_TAGS = {"header", "nav", "footer", "aside"}
_BAD_ANCESTOR_CLASS_HINTS = (
    "menu",
    "navbar",
    "nav",
    "topbar",
    "footer",
    "sidebar",
    "brand",
    "logo",
)


def _is_bad_image(url: str) -> bool:
    u = (url or "").lower()
    if not u:
        return True
    if u.startswith("data:"):
        return True
    if any(h in u for h in _BAD_IMAGE_HINTS):
        return True
    path = urlparse(u).path
    if any(h in path for h in _BAD_PATH_HINTS):
        return True
    if path.endswith(".svg"):
        return True
    if path.endswith(".ico"):
        return True
    return False


def _parse_srcset(srcset: str) -> str | None:
    # pick the largest width candidate when possible
    best_url = None
    best_w = -1
    for part in (srcset or "").split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        cand_url = bits[0].strip()
        w = -1
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                w = int(bits[1][:-1])
            except Exception:
                w = -1
        if w > best_w:
            best_w = w
            best_url = cand_url
        elif best_url is None:
            best_url = cand_url
    return best_url


def _to_int(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int("".join(ch for ch in raw if ch.isdigit()))
    except Exception:
        return None


def _has_bad_ancestor(node) -> bool:
    cur = getattr(node, "parent", None)
    depth = 0
    while cur is not None and depth < 8:
        tag = (getattr(cur, "tag", "") or "").lower()
        if tag in _BAD_ANCESTOR_TAGS:
            # Many news sites wrap the hero image in an <header> inside <article>.
            # We want to ignore the *site* header/nav, but allow an article header.
            if tag == "header":
                cls = (getattr(cur, "attributes", {}) or {}).get("class") or ""
                cls = cls.lower()
                if cls and any(h in cls for h in _BAD_ANCESTOR_CLASS_HINTS):
                    return True

                # If this header is within an <article>, treat it as content.
                a = getattr(cur, "parent", None)
                a_depth = 0
                within_article = False
                while a is not None and a_depth < 8:
                    a_tag = (getattr(a, "tag", "") or "").lower()
                    if a_tag == "article":
                        within_article = True
                        break
                    a = getattr(a, "parent", None)
                    a_depth += 1
                if within_article:
                    # don't block
                    pass
                else:
                    return True
            else:
                return True
        cls = (getattr(cur, "attributes", {}) or {}).get("class") or ""
        cls = cls.lower()
        if cls and any(h in cls for h in _BAD_ANCESTOR_CLASS_HINTS):
            return True
        cur = getattr(cur, "parent", None)
        depth += 1
    return False


def _candidate_score(node, attrs: dict, base: int, site_name: str | None, base_host: str) -> int:
    score = base
    alt = (attrs.get("alt") or attrs.get("title") or "").strip().lower()
    cls = (attrs.get("class") or "").strip().lower()
    if "logo" in alt or "logo" in cls or "brand" in cls:
        score -= 120
    if "marca" in alt or "marca" in cls:
        score -= 80
    if site_name and site_name.lower() in alt:
        score -= 60

    w = _to_int(attrs.get("width"))
    h = _to_int(attrs.get("height"))
    if w is not None and w < 200:
        score -= 40
    if h is not None and h < 200:
        score -= 40
    if w is not None and w >= 600:
        score += 8
    if h is not None and h >= 350:
        score += 8

    # Penalize images wrapped by a link to the site home (common for logos).
    cur = getattr(node, "parent", None)
    depth = 0
    while cur is not None and depth < 6:
        if (getattr(cur, "tag", "") or "").lower() == "a":
            href = (getattr(cur, "attributes", {}) or {}).get("href") or ""
            if _href_looks_home(href, base_host=base_host):
                score -= 90
            break
        cur = getattr(cur, "parent", None)
        depth += 1

    return score


def _pick_img_src(attrs: dict) -> str:
    src = (attrs.get("src") or "").strip()

    # Many sites keep a placeholder in src and the real image in data-src/srcset.
    if src and _is_bad_image(src):
        src = ""
    if src and any(h in src.lower() for h in ("placeholder", "blank", "spacer", "pixel")):
        src = ""

    if not src:
        src = (
            attrs.get("data-src")
            or attrs.get("data-lazy-src")
            or attrs.get("data-original")
            or attrs.get("data-zoom-src")
            or ""
        ).strip()
        if src and _is_bad_image(src):
            src = ""

    if not src:
        srcset = (attrs.get("srcset") or attrs.get("data-srcset") or "").strip()
        src = _parse_srcset(srcset) or ""
        if src and _is_bad_image(src):
            src = ""

    return src


def _href_looks_home(href: str, base_host: str) -> bool:
    h = (href or "").strip()
    if not h:
        return False
    if h in {"/", "#", "#/"}:
        return True
    try:
        p = urlparse(h)
        if p.scheme in {"http", "https"} and p.netloc:
            return p.netloc.lower().endswith(base_host.lower()) and (p.path in {"", "/"})
    except Exception:
        return False
    return False


def _extract_urls_from_style(style: str) -> list[str]:
    # background-image: url('...') / url("...") / url(...)
    if not style:
        return []
    out: list[str] = []
    for m in re.finditer(r"url\(([^)]+)\)", style, flags=re.IGNORECASE):
        raw = m.group(1).strip().strip('"').strip("'")
        if raw:
            out.append(raw)
    return out


def _extract_jsonld_images(tree: HTMLParser) -> list[str]:
    imgs: list[str] = []

    def visit(val):
        if isinstance(val, dict):
            # common keys
            for k in ("image", "thumbnailUrl", "contentUrl", "url"):
                if k in val:
                    visit(val[k])
            # nested graph
            if "@graph" in val:
                visit(val["@graph"])
            for v in val.values():
                visit(v)
        elif isinstance(val, list):
            for it in val:
                visit(it)
        elif isinstance(val, str):
            s = val.strip()
            if s.startswith("http") or s.startswith("/"):
                imgs.append(s)

    for node in tree.css("script[type='application/ld+json']")[:12]:
        txt = (node.text() or "").strip()
        if not txt:
            continue
        # Some pages include multiple JSON blocks or invalid trailing commas; be lenient.
        try:
            data = json.loads(txt)
        except Exception:
            continue
        visit(data)

    # keep only likely image urls
    cleaned: list[str] = []
    for u in imgs:
        low = u.lower()
        if any(ext in low for ext in (".jpg", ".jpeg", ".png", ".webp")) or ("/images/" in low) or ("/img/" in low):
            cleaned.append(u)
    return cleaned


def _extract_jsonld_urls(tree: HTMLParser) -> list[str]:
    urls: list[str] = []

    def visit(val):
        if isinstance(val, dict):
            for k in ("mainEntityOfPage", "url", "@id"):
                if k in val:
                    visit(val[k])
            if "@graph" in val:
                visit(val["@graph"])
            for v in val.values():
                visit(v)
        elif isinstance(val, list):
            for it in val:
                visit(it)
        elif isinstance(val, str):
            s = val.strip()
            if s.startswith("http") or s.startswith("/"):
                urls.append(s)

    for node in tree.css("script[type='application/ld+json']")[:12]:
        txt = (node.text() or "").strip()
        if not txt:
            continue
        try:
            data = json.loads(txt)
        except Exception:
            continue
        visit(data)

    return urls


def _find_content_root(tree: HTMLParser):
    for sel in (
        "article",
        "main article",
        "main",
        "[role='main']",
        "#content",
        ".content",
        ".entry-content",
        ".post-content",
        ".post",
        ".materia",
        ".news",
    ):
        node = tree.css_first(sel)
        if node is not None:
            return node
    return None


def _extract_meta(tree: HTMLParser) -> dict[str, str]:
    metas: dict[str, str] = {}
    for node in tree.css("meta"):
        attrs = node.attributes
        key = (attrs.get("property") or attrs.get("name") or "").strip().lower()
        val = (attrs.get("content") or "").strip()
        if key and val and key not in metas:
            metas[key] = val
    return metas


def _extract_image_candidates(tree: HTMLParser, site_name: str | None, base_host: str) -> list[tuple[str, int]]:
    candidates: list[tuple[str, int]] = []

    root = _find_content_root(tree)

    scopes = [root] if root is not None else []
    scopes.append(tree)

    for scope in scopes:
        # Prefer <img> in content root
        for node in scope.css("img")[:80]:
            if _has_bad_ancestor(node):
                continue
            attrs = node.attributes
            src = _pick_img_src(attrs)
            if src and not _is_bad_image(src):
                score = _candidate_score(
                    node,
                    attrs,
                    base=30 if scope is root else 18,
                    site_name=site_name,
                    base_host=base_host,
                )
                candidates.append((src, score))

        # Also support <picture><source srcset=...> patterns
        for node in scope.css("picture source")[:80]:
            if _has_bad_ancestor(node):
                continue
            attrs = node.attributes
            srcset = (attrs.get("srcset") or attrs.get("data-srcset") or "").strip()
            src = _parse_srcset(srcset) or ""
            if src and not _is_bad_image(src):
                score = _candidate_score(
                    node,
                    attrs,
                    base=28 if scope is root else 16,
                    site_name=site_name,
                    base_host=base_host,
                )
                candidates.append((src, score))

        # Background images inside content root (common for hero images)
        for node in scope.css("article [style], [style]")[:120]:
            if _has_bad_ancestor(node):
                continue
            style = (node.attributes.get("style") or "").strip()
            for u in _extract_urls_from_style(style):
                if u and not _is_bad_image(u):
                    candidates.append((u, 26 if scope is root else 14))

    # JSON-LD images (often the canonical hero image)
    for u in _extract_jsonld_images(tree)[:10]:
        if u and not _is_bad_image(u):
            candidates.append((u, 34))

    return candidates


async def fetch_og(client: httpx.AsyncClient, url: str) -> OgData:
    try:
        current = url
        for hop in range(2):
            r = await client.get(current, follow_redirects=True)
            r.raise_for_status()

            ctype = (r.headers.get("content-type") or "").lower()
            # Be conservative: if it's clearly not HTML, don't try to parse.
            if ctype and ("text/html" not in ctype and "application/xhtml" not in ctype and "xml" not in ctype):
                return OgData(None, None, None, None, str(r.url), False, False)

            # limit parse size
            html = r.text[:300_000]
            tree = HTMLParser(html)
            meta = _extract_meta(tree)

            base_url = str(r.url)
            base_host = urlparse(base_url).netloc
            site_name = normalize_text(meta.get("og:site_name") or "") or urlparse(base_url).netloc
            description = normalize_text(
                meta.get("og:description")
                or meta.get("twitter:description")
                or meta.get("description")
                or ""
            )

            # canonical URL extraction (avoid publisher home pages)
            canonical_candidates: list[str] = []
            og_url = (meta.get("og:url") or meta.get("twitter:url") or "").strip()
            if og_url:
                canonical_candidates.append(og_url)
            canon_link = tree.css_first("link[rel='canonical']")
            if canon_link is not None:
                href = (canon_link.attributes.get("href") or "").strip()
                if href:
                    canonical_candidates.append(href)
            canonical_candidates.extend(_extract_jsonld_urls(tree))

            canonical_url = None
            for cand in canonical_candidates:
                abs_c = urljoin(base_url, cand)
                p = urlparse(abs_c)
                if p.scheme not in {"http", "https"}:
                    continue
                if not p.netloc:
                    continue
                # avoid trivial homepage canonical
                if p.netloc.lower().endswith(base_host.lower()) and (p.path in {"", "/"}):
                    continue
                canonical_url = abs_c
                break

            # If we landed on Google News wrapper, re-fetch the publisher page.
            if hop == 0 and base_host.lower() == "news.google.com" and canonical_url:
                log.debug("og_google_news_follow", extra={"original": url, "publisher": canonical_url})
                current = canonical_url
                continue

            # If we are still on Google News and couldn't resolve the publisher URL,
            # don't treat Google HTML as the news site.
            if base_host.lower() == "news.google.com" and not canonical_url:
                log.info("og_google_news_unresolved", extra={"url": url})
                return OgData(None, None, None, None, base_url, False, False)

            root = _find_content_root(tree)
            has_img_tag = False
            try:
                if root is not None:
                    has_img_tag = bool(root.css_first("img"))
                if not has_img_tag:
                    has_img_tag = bool(tree.css_first("img"))
            except Exception:
                has_img_tag = False

            # image candidates (meta first, then article body)
            meta_imgs: list[tuple[str, int]] = []
            for k, score in (
                # Meta images often point to site logos/banners. Keep them as fallback.
                ("og:image", 14),
                ("og:image:url", 14),
                ("og:image:secure_url", 14),
                ("twitter:image", 13),
                ("twitter:image:src", 13),
            ):
                v = (meta.get(k) or "").strip()
                if v and not _is_bad_image(v):
                    meta_imgs.append((v, score))

            body_imgs = _extract_image_candidates(tree, site_name=site_name, base_host=base_host)
            all_imgs = meta_imgs + body_imgs

            best = None
            best_score = -10**9
            best_is_meta = False
            for raw, s in all_imgs:
                abs_url = urljoin(base_url, raw)
                score = s
                low = abs_url.lower()
                is_meta = s <= 14
                if any(h in low for h in _BAD_IMAGE_HINTS):
                    score -= 80
                if low.endswith((".jpg", ".jpeg", ".png", ".webp")):
                    score += 5
                if low.endswith(".gif"):
                    score -= 10
                # extra penalty for likely brand images served as og:image
                if is_meta and any(h in low for h in _BAD_PATH_HINTS):
                    score -= 80
                if score > best_score:
                    best_score = score
                    best = abs_url
                    best_is_meta = is_meta

            # If there are body images available, reject weak meta images (likely brand/logo).
            # If there are NO body images at all, accept og:image as the article image.
            if best_is_meta and best_score < 26 and body_imgs:
                image_url = None
            else:
                image_url = best if best_score >= 12 else None

            log.debug(
                "og_result",
                extra={
                    "final_url": base_url,
                    "has_img_tag": bool(has_img_tag),
                    "image_url": image_url,
                    "candidates": len(all_imgs),
                    "best_score": best_score if all_imgs else None,
                },
            )
            return OgData(
                image_url=image_url,
                description=description or None,
                site_name=site_name or None,
                canonical_url=canonical_url,
                final_url=base_url,
                has_img_tag=bool(has_img_tag),
                parsed_html=True,
            )

        return OgData(None, None, None, None, None, False, False)
    except Exception as e:
        log.debug("og_fetch_failed", extra={"url": url, "err": str(e)})
        return OgData(None, None, None, None, None, False, False)
