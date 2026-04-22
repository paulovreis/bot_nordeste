from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
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


_BLOCKED_HOST_SUFFIXES = {
    # social media / UGC
    "facebook.com",
    "fb.com",
    "instagram.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "youtu.be",
    "reddit.com",
    "pinterest.com",
    "pinterest.co",
    "linkedin.com",
    "t.me",
    "telegram.me",
}


def is_blocked_source_url(url: str) -> bool:
    """Return True if the URL is not a news site/article source.

    This is intentionally conservative: it blocks common social networks, maps, and
    messaging shortlinks that are frequently not actual news articles.
    """
    try:
        u = (url or "").strip()
        if not u:
            return False
        p = urlparse(u)
        if p.scheme not in {"http", "https"}:
            return True

        host = (p.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if not host:
            return True

        # Suffix match for blocked hosts
        for suf in _BLOCKED_HOST_SUFFIXES:
            if host == suf or host.endswith("." + suf):
                return True

        # Block Google Maps and similar
        path = (p.path or "").lower()
        if host in {"maps.app.goo.gl", "goo.gl"} and "maps" in path:
            return True
        if host.endswith("google.com") and path.startswith("/maps"):
            return True
        if host.endswith("google.com") and path.startswith("/travel"):
            return True
        if host.startswith("maps."):
            return True

        return False
    except Exception:
        # Fail-closed to avoid sending non-news sources.
        return True


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
        low = path.lower()
        if low in {"/index.html", "/index.php", "/home", "/inicio", "/in%C3%ADcio"}:
            return True
        if low.rstrip("/") in {"/inicio", "/in%C3%ADcio"}:
            return True
        return False
    except Exception:
        return False


_PT_STOPWORDS = {
    "a",
    "à",
    "ao",
    "aos",
    "as",
    "às",
    "com",
    "como",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "é",
    "em",
    "entre",
    "foi",
    "for",
    "há",
    "isso",
    "já",
    "mais",
    "mas",
    "na",
    "nas",
    "não",
    "no",
    "nos",
    "o",
    "os",
    "ou",
    "para",
    "pela",
    "pelas",
    "pelo",
    "pelos",
    "por",
    "que",
    "se",
    "sem",
    "ser",
    "sob",
    "sua",
    "suas",
    "seu",
    "seus",
    "também",
    "tem",
    "têm",
    "um",
    "uma",
    "umas",
    "uns",
}


_TOKEN_RE = re.compile(r"[0-9a-zA-ZÀ-ÿ]+", re.UNICODE)


def extract_keywords(text: str, *, max_tokens: int = 64) -> list[str]:
    """Extract a stable list of keywords for deduplication.

    - Lowercases
    - Removes PT-BR stopwords
    - Drops very short tokens
    """
    raw = normalize_text(text).lower()
    if not raw:
        return []

    tokens: list[str] = []
    for m in _TOKEN_RE.finditer(raw):
        t = m.group(0)
        if len(t) <= 2:
            continue
        if t in _PT_STOPWORDS:
            continue
        tokens.append(t)

    if not tokens:
        return []

    # Keep most frequent tokens; stable tie-breaker by token.
    from collections import Counter

    c = Counter(tokens)
    ordered = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
    return [t for (t, _) in ordered[:max_tokens]]


def _hash64(token: str) -> int:
    # Stable 64-bit hash.
    d = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(d, "big", signed=False)


def simhash64(tokens: list[str]) -> int:
    """Compute a 64-bit SimHash from tokens."""
    if not tokens:
        return 0
    from collections import Counter

    weights = Counter(tokens)
    acc = [0] * 64
    for tok, w in weights.items():
        h = _hash64(tok)
        for i in range(64):
            bit = (h >> i) & 1
            acc[i] += w if bit else -w

    out = 0
    for i, v in enumerate(acc):
        if v >= 0:
            out |= 1 << i
    return out


def bands16(h: int) -> tuple[int, int, int, int]:
    u = h & ((1 << 64) - 1)
    return (
        u & 0xFFFF,
        (u >> 16) & 0xFFFF,
        (u >> 32) & 0xFFFF,
        (u >> 48) & 0xFFFF,
    )


def hamming64(a: int, b: int) -> int:
    x = (a ^ b) & ((1 << 64) - 1)
    # Python 3.11+: int.bit_count()
    return x.bit_count()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

