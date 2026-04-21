from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode


_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "igshid",
    "mc_cid",
    "mc_eid",
}


def canonicalize_url(url: str) -> str:
    url = url.strip()
    p = urlparse(url)
    # remove fragments
    fragment = ""
    # normalize netloc casing
    netloc = p.netloc.lower()
    # remove tracking query params
    q = [(k, v) for (k, v) in parse_qsl(p.query, keep_blank_values=True) if k not in _TRACKING_PARAMS]
    query = urlencode(q, doseq=True)
    # strip default ports
    netloc = re.sub(r":(80|443)$", "", netloc)

    return urlunparse((p.scheme, netloc, p.path, p.params, query, fragment))


def make_news_id(canonical_url: str, source: str, published_iso: str) -> str:
    raw = f"{canonical_url}|{source}|{published_iso}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def normalize_text(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host or ""
    except Exception:
        return ""


def is_homepage_url(url: str) -> bool:
    """Best-effort check to avoid sending publisher home pages instead of articles."""
    try:
        p = urlparse((url or "").strip())
        if p.scheme not in {"http", "https"}:
            return False
        if not p.netloc:
            return False
        path = (p.path or "").strip()
        if path in {"", "/"}:
            return True
        if path.lower() in {"/index.html", "/index.php", "/home", "/inicio", "/in%C3%ADcio"}:
            return True
        return False
    except Exception:
        return False
