from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable

import feedparser
from dateutil import parser as dateparser

from .models import NewsItem
from .util import canonicalize_url, is_blocked_source_url, is_homepage_url, make_news_id, normalize_text, truncate

log = logging.getLogger(__name__)


def default_queries() -> list[str]:
    # Keep this list intentionally small for performance.
    # Use Google News query syntax with OR to widen coverage.
    geos = [
        "Nordeste",
        "Nordestino",
        "Nordestina",
        "Bahia",
        "Pernambuco",
        "Ceará",
        "Maranhão",
        "Paraíba",
        "Rio Grande do Norte",
        "Alagoas",
        "Sergipe",
        "Piauí",
    ]

    obras = "(\"obra pública\" OR \"grande obra\" OR ponte OR rodovia OR ferrovia OR metrô OR porto OR aeroporto OR saneamento OR hospital OR habitação OR habitações OR barragem OR açude OR solar OR eólica OR energia OR energética OR seca OR exportação OR exportações OR educação OR educacional OR desenvolvimento OR desigualdade OR emendas OR licitação OR PAC OR concurso OR \"concurso público\")"
    politica = "(política OR governador OR prefeitura OR \"assembleia legislativa\" OR eleição OR \"gestão pública\" OR eleições OR lula OR pt OR )"

    queries: list[str] = []
    for geo in geos:
        queries.append(f"{obras} {geo}")
        queries.append(f"{politica} {geo}")
    return queries


def google_news_rss_url(query: str) -> str:
    # hl=pt-BR, gl=BR, ceid=BR:pt-419 (LatAm) is common; keep stable.
    from urllib.parse import quote_plus

    q = quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"


def _extract_source(entry) -> str:
    # Google News RSS often contains <source>.
    src = entry.get("source")
    if isinstance(src, dict):
        title = (src.get("title") or "").strip()
        if title:
            return title
    return "google-news"


def _strip_source_suffix(title: str, source: str) -> str:
    """Remove the ' - Source Name' suffix that Google News appends to RSS titles.

    Handles edge cases like 'Title - - Source' (when the article title itself ends with ' -').
    """
    if not source or source == "google-news":
        return title
    suffix = f" - {source}"
    if title.endswith(suffix):
        # Strip suffix then clean up any trailing ' -' left behind
        title = title[: -len(suffix)].rstrip(" -").strip()
    return title


def _extract_best_link(entry) -> str | None:
    # Prefer a non-news.google.com link when present.
    links = entry.get("links") or []
    for l in links:
        href = (l.get("href") or "").strip()
        if href and "news.google.com" not in href and not is_homepage_url(href):
            return href

    # Do NOT fallback to <source href>, it is commonly the publisher homepage.

    link = (entry.get("link") or "").strip()
    if not link:
        return None
    if is_homepage_url(link):
        return None
    if is_blocked_source_url(link):
        return None
    return link


def _parse_published(entry) -> datetime | None:
    for key in ("published", "updated", "pubDate"):
        val = entry.get(key)
        if val:
            try:
                dt = dateparser.parse(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                continue
    return None


def fetch_items(
    *,
    queries: list[str],
    max_age_days: int,
    max_items_total: int,
    max_items_per_query: int,
) -> Iterable[NewsItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    total = 0
    for query in queries:
        if total >= max_items_total:
            break
        url = google_news_rss_url(query)
        feed = feedparser.parse(url)
        if feed.bozo:
            log.warning("rss_bozo", extra={"query": query})

        per_q = 0
        for entry in feed.entries:
            if per_q >= max_items_per_query or total >= max_items_total:
                break

            link = _extract_best_link(entry)
            raw_title = (entry.get("title") or "").strip()
            if not link or not raw_title:
                continue
            source = _extract_source(entry)
            title = _strip_source_suffix(raw_title, source)

            # block non-news sources early (social media, maps, etc.)
            if is_blocked_source_url(link):
                continue

            # only accept http(s)
            try:
                from urllib.parse import urlparse

                if urlparse(link).scheme not in {"http", "https"}:
                    continue
            except Exception:
                continue

            published = _parse_published(entry)
            if not published:
                continue
            published_utc = published.astimezone(timezone.utc)
            if published_utc < cutoff:
                continue

            canonical = canonicalize_url(link)
            if is_homepage_url(canonical) or is_blocked_source_url(canonical):
                continue
            published_iso = published_utc.isoformat(timespec="seconds")
            news_id = make_news_id(canonical, source, published_iso)

            snippet = entry.get("summary") or entry.get("description") or ""
            snippet = truncate(_strip_html(snippet), 240)

            yield NewsItem(
                news_id=news_id,
                canonical_url=canonical,
                title=truncate(title, 160),
                source=source,
                published_at=published_utc,
                snippet=snippet,
            )
            per_q += 1
            total += 1


def _strip_html(s: str) -> str:
    import re

    s = re.sub(r"<[^>]+>", " ", s or "")
    return normalize_text(s)
