from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from . import db
from .config import load_settings
from .enrich import fetch_og
from .image_fallback import get_fallback_image
from .log import setup_logging
from .rss import default_queries, fetch_items
from .safety import classify_text
from .telegraph import TelegraphClient, build_content
from .telegram import TelegramClient, build_inline_button
from .timeutil import SendWindow, parse_hhmm
from .util import (
    bands16,
    canonicalize_url,
    domain_from_url,
    extract_keywords,
    is_blocked_source_url,
    is_homepage_url,
    normalize_text,
    simhash64,
    truncate,
)

log = logging.getLogger(__name__)


async def collector_loop(conn, settings) -> None:
    queries = settings.queries_override or default_queries()

    # Collector avoids per-item HTML fetches for performance.
    while True:
        try:
            inserted = 0
            blocked = 0
            total = 0

            for item in fetch_items(
                queries=queries,
                max_age_days=settings.max_age_days,
                max_items_total=settings.max_items_per_fetch,
                max_items_per_query=settings.max_items_per_query,
            ):
                total += 1
                safety_status, safety_reason = classify_text(item.title, item.snippet, None)

                # Story fingerprint for cross-portal dedup (fast; no extra HTTP).
                tokens = extract_keywords(f"{item.title} {item.snippet}")
                story_hash = simhash64(tokens)
                b1, b2, b3, b4 = bands16(story_hash)

                ok = db.upsert_news_and_enqueue(
                    conn,
                    news_id=item.news_id,
                    canonical_url=item.canonical_url,
                    title=item.title,
                    source=item.source,
                    published_at=item.published_at,
                    snippet=item.snippet,
                    image_url=None,
                    og_description=None,
                    safety_status=safety_status,
                    safety_reason=safety_reason,
                    story_hash_u64=story_hash,
                    band1=b1,
                    band2=b2,
                    band3=b3,
                    band4=b4,
                    dedup_window_hours=settings.dedup_window_hours,
                    dedup_hamming_max=settings.dedup_hamming_max,
                )
                if ok:
                    if safety_status == "adult":
                        blocked += 1
                    else:
                        inserted += 1

            log.info(
                "collector_done",
                extra={"total": total, "inserted_or_queued": inserted, "blocked_adult": blocked},
            )

            try:
                qdel, ndel = db.purge_older_than_days(conn, older_than_days=settings.retention_days)
                if qdel or ndel:
                    log.info("retention_purge", extra={"queue_deleted": qdel, "news_deleted": ndel})
            except Exception:
                log.debug("retention_purge_failed")
        except Exception:
            log.exception("collector_error")

        await asyncio.sleep(settings.fetch_interval_min * 60)


async def sender_loop(conn, settings) -> None:
    tz = ZoneInfo(settings.timezone)
    window = SendWindow(tz=tz, start=parse_hhmm(settings.send_window_start), end=parse_hhmm(settings.send_window_end))

    timeout = httpx.Timeout(connect=10.0, read=20.0, write=5.0, pool=5.0)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
    }

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        tg = TelegramClient(settings.bot_token, client)
        tgraph = (
            TelegraphClient(settings.telegraph_token, client)
            if settings.use_telegraph and settings.telegraph_token
            else None
        )

        while True:
            now_utc = datetime.now(timezone.utc)

            if not window.is_open(now_utc):
                next_open = window.next_open_utc(now_utc)
                sleep_for = max(30, int((next_open - now_utc).total_seconds()))
                log.info("send_window_closed", extra={"sleep_sec": sleep_for})
                await asyncio.sleep(sleep_for)
                continue

            row = db.get_next_queued(conn, now_utc)
            if not row:
                await asyncio.sleep(max(5, int(settings.idle_poll_sec)))
                continue

            queue_id = int(row["queue_id"])
            news_id = row["news_id"]

            # If we crossed the boundary between selecting and sending, reschedule.
            now_utc = datetime.now(timezone.utc)
            if not window.is_open(now_utc):
                next_open = window.next_open_utc(now_utc)
                db.reschedule_queue(conn, queue_id, next_open)
                continue

            try:
                title = row["title"]
                url = row["canonical_url"]
                snippet = row["snippet"]
                image_url = row["image_url"]
                og_desc = row["og_description"]
                telegraph_url = row["telegraph_url"]
                safety_status = row["safety_status"]

                allow_claude_fallback = False

                # Hard block: never send non-news sources.
                if is_blocked_source_url(url):
                    db.mark_skipped(conn, queue_id, "blocked_source_url")
                    continue

                # Enrich on-demand (one HTTP request per sent item)
                if not image_url or not og_desc or is_homepage_url(url) or domain_from_url(url) == "news.google.com":
                    og = await fetch_og(client, url)
                    # Fallback only when page couldn't be fetched or returned no usable image.
                    allow_claude_fallback = not bool(getattr(og, "parsed_html", False)) or not bool(og.image_url)

                    # If the final URL after redirects is bad, don't send.
                    if og.final_url and (is_homepage_url(og.final_url) or is_blocked_source_url(og.final_url)):
                        db.mark_retry(conn, queue_id, f"bad_final_url:{og.final_url}", delay_sec=2 * 3600)
                        continue

                    # Canonical URL improvement (avoid homepages).
                    if og.canonical_url:
                        cand = canonicalize_url(og.canonical_url)
                        if cand and not is_homepage_url(cand) and not is_blocked_source_url(cand) and cand != url:
                            ok = db.set_canonical_url(conn, news_id, cand)
                            if not ok:
                                try:
                                    existing = db.get_news_id_by_canonical_url(conn, cand)
                                    if existing:
                                        db.mark_news_dedup(
                                            conn,
                                            news_id,
                                            dedup_of_news_id=existing,
                                            reason="canonical_conflict",
                                        )
                                except Exception:
                                    pass
                                db.mark_skipped(conn, queue_id, "duplicate_canonical_url")
                                continue
                            url = cand

                    if og.image_url or og.description:
                        db.set_enrichment(conn, news_id, image_url=og.image_url, og_description=og.description)
                        image_url = image_url or og.image_url
                        og_desc = og_desc or og.description

                # If we still don't have an image, try fallback chain — but only
                # when the article page had no <img> tags or couldn't be fetched.
                if not image_url and settings.image_fallback and allow_claude_fallback:
                    _fallback_reason = "fetch_error" if not getattr(og, "parsed_html", False) else "no_img_tags"
                    log.info(
                        "image_fallback_start",
                        extra={"news_id": news_id, "url": url, "reason": _fallback_reason},
                    )
                    fb = await get_fallback_image(
                        client,
                        title,
                        claude_token=settings.claude_token,
                        claude_model=settings.claude_model,
                    )
                    if fb:
                        log.info("image_fallback_used", extra={"news_id": news_id, "url": fb})
                        db.set_enrichment(conn, news_id, image_url=fb, og_description=None)
                        image_url = fb

                if settings.require_image and not image_url:
                    db.mark_retry(conn, queue_id, "no_image", delay_sec=2 * 3600)
                    continue

                # safety second pass
                if safety_status != "adult":
                    status2, reason2 = classify_text(title, snippet, og_desc)
                    if status2 == "adult":
                        db.mark_retry(conn, queue_id, f"blocked_by_safety:{reason2}", delay_sec=6 * 3600)
                        continue

                if settings.use_telegraph and tgraph and not telegraph_url:
                    published_local = datetime.fromisoformat(row["published_at"]).astimezone(tz)
                    content_nodes = build_content(
                        title=title,
                        source_url=url,
                        source_name=row["source"],
                        published_at=published_local,
                        snippet=snippet,
                        image_url=image_url,
                    )
                    telegraph_url = await tgraph.create_page(
                        title=truncate(title, 120),
                        author_name="Bot Nordeste",
                        content_nodes=content_nodes,
                    )
                    db.set_telegraph_url(conn, news_id, telegraph_url)

                button_url = telegraph_url if (settings.use_telegraph and telegraph_url) else url
                button = build_inline_button("LEITURA RÁPIDA", button_url)

                published_local = datetime.fromisoformat(row["published_at"]).astimezone(tz)
                source_display = normalize_text(row["source"] or "")
                domain = domain_from_url(url)
                dom = (domain or "").strip().lower()
                if dom.endswith("google.com"):
                    domain = ""
                if source_display and "news.google.com" in source_display.lower():
                    source_display = ""
                if domain and (not source_display or source_display == "google-news"):
                    source_display = domain
                elif domain and source_display and domain not in source_display.lower():
                    # Keep source concise and informative
                    source_display = domain
                if source_display.strip().lower() == "news.google.com":
                    source_display = ""

                dt = published_local.strftime("%d/%m/%Y %H:%M")
                title_line = f"📰 <b>{_escape(normalize_text(title))}</b>"
                if source_display:
                    info_line = f"🌐 <i>{_escape(source_display)}</i>  •  🕒 <i>{dt}</i>"
                else:
                    info_line = f"🕒 <i>{dt}</i>"
                text = f"{title_line}\n{info_line}".strip()

                if settings.dry_run:
                    log.info("dry_run_send", extra={"news_id": news_id, "url": url})
                    db.mark_sent(conn, queue_id, telegram_message_id=None)
                else:
                    mid = None
                    if image_url:
                        try:
                            mid = await tg.send_photo(
                                chat_id=settings.chat_id,
                                photo_url=image_url,
                                caption=text,
                                reply_markup=button,
                            )
                        except Exception:
                            log.debug("send_photo_failed")
                            mid = None

                            # Try to re-enrich from the publisher page to get a usable on-site image.
                            try:
                                og2 = await fetch_og(client, url)
                                allow_claude_fallback = not bool(getattr(og2, "parsed_html", False)) or not bool(og2.image_url)
                                if og2.image_url and og2.image_url != image_url:
                                    try:
                                        mid = await tg.send_photo(
                                            chat_id=settings.chat_id,
                                            photo_url=og2.image_url,
                                            caption=text,
                                            reply_markup=button,
                                        )
                                        db.set_enrichment(conn, news_id, image_url=og2.image_url, og_description=None)
                                        image_url = og2.image_url
                                    except Exception:
                                        mid = None
                            except Exception:
                                pass

                            # If image is required, try one extra fallback image —
                            # only when the page had no <img> tags or couldn't be fetched.
                            if settings.require_image and settings.image_fallback and allow_claude_fallback:
                                alt = await get_fallback_image(
                                    client,
                                    title,
                                    claude_token=settings.claude_token,
                                    claude_model=settings.claude_model,
                                )
                                if alt and alt != image_url:
                                    try:
                                        mid = await tg.send_photo(
                                            chat_id=settings.chat_id,
                                            photo_url=alt,
                                            caption=text,
                                            reply_markup=button,
                                        )
                                        db.set_enrichment(conn, news_id, image_url=alt, og_description=None)
                                        image_url = alt
                                    except Exception:
                                        mid = None

                            if settings.require_image and not mid:
                                db.mark_retry(conn, queue_id, "send_photo_failed", delay_sec=30 * 60)
                                continue

                    if not mid:
                        if settings.require_image:
                            db.mark_retry(conn, queue_id, "no_image_to_send", delay_sec=30 * 60)
                            continue

                        mid = await tg.send_message(
                            chat_id=settings.chat_id,
                            text=text,
                            reply_markup=button,
                            disable_web_page_preview=True,
                        )
                    db.mark_sent(conn, queue_id, telegram_message_id=mid)

                await asyncio.sleep(settings.send_interval_min * 60)

            except Exception as e:
                log.exception("send_error", extra={"queue_id": queue_id})
                # backoff: 5m, 15m, 1h, 6h
                attempts = 1
                try:
                    # best effort fetch current attempts
                    r2 = conn.execute("SELECT attempts FROM queue WHERE id=?", (queue_id,)).fetchone()
                    if r2:
                        attempts = int(r2[0]) + 1
                except Exception:
                    pass
                delay = 300
                if attempts >= 2:
                    delay = 900
                if attempts >= 3:
                    delay = 3600
                if attempts >= 4:
                    delay = 21600
                db.mark_retry(conn, queue_id, str(e), delay_sec=delay)


def _escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


async def main_async() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    if not settings.bot_token or not settings.chat_id:
        log.error("missing_env", extra={"need": ["BOT_TOKEN", "CHAT_ID"]})
        raise SystemExit(2)

    if settings.use_telegraph and not settings.telegraph_token:
        log.error("missing_env", extra={"need": ["TELEGRAPH_TOKEN"]})
        raise SystemExit(2)

    db_path = os.getenv("DB_PATH", "/data/bot.db")
    conn = db.connect(db_path)
    db.init_schema(conn)

    try:
        qdel, ndel = db.purge_older_than_days(conn, older_than_days=settings.retention_days)
        if qdel or ndel:
            log.info("retention_purge", extra={"queue_deleted": qdel, "news_deleted": ndel})
    except Exception:
        log.debug("retention_purge_failed")
    reset = db.reset_stale_sending(conn, older_than_minutes=60)
    if reset:
        log.info("reset_stale_sending", extra={"count": reset})

    log.info("startup", extra={"db_path": db_path, "dry_run": settings.dry_run})

    await asyncio.gather(
        collector_loop(conn, settings),
        sender_loop(conn, settings),
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
