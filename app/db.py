from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS news (
            news_id TEXT PRIMARY KEY,
            canonical_url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            published_at TEXT NOT NULL,
            snippet TEXT NOT NULL,
            image_url TEXT,
            og_description TEXT,
            telegraph_url TEXT,
            safety_status TEXT NOT NULL DEFAULT 'unknown',
            safety_reason TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT NOT NULL,
            locked_at TEXT,
            last_error TEXT,
            sent_at TEXT,
            telegram_message_id TEXT,
            FOREIGN KEY(news_id) REFERENCES news(news_id)
        );

        CREATE INDEX IF NOT EXISTS idx_queue_status_next ON queue(status, next_attempt_at);
        CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at);
        """
    )


def reset_stale_sending(conn: sqlite3.Connection, *, older_than_minutes: int = 60) -> int:
    # If the process restarts while an item is in 'sending', it would be stuck.
    cutoff = (_utc_now() - timedelta(minutes=older_than_minutes)).isoformat(timespec="seconds")
    cur = conn.execute(
        """
        UPDATE queue
        SET status='queued', locked_at=NULL
        WHERE status='sending' AND locked_at IS NOT NULL AND locked_at <= ?
        """,
        (cutoff,),
    )
    return int(cur.rowcount or 0)


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def upsert_news_and_enqueue(
    conn: sqlite3.Connection,
    *,
    news_id: str,
    canonical_url: str,
    title: str,
    source: str,
    published_at: datetime,
    snippet: str,
    image_url: str | None,
    og_description: str | None,
    safety_status: str,
    safety_reason: str | None,
) -> bool:
    now = _utc_now().isoformat(timespec="seconds")
    published_iso = published_at.astimezone(timezone.utc).isoformat(timespec="seconds")

    with transaction(conn):
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO news (
                news_id, canonical_url, title, source, published_at, snippet, image_url, og_description,
                safety_status, safety_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                news_id,
                canonical_url,
                title,
                source,
                published_iso,
                snippet,
                image_url,
                og_description,
                safety_status,
                safety_reason,
                now,
            ),
        )

        inserted = cur.rowcount == 1
        if not inserted:
            return False

        if safety_status == "adult":
            return True  # stored, but not queued

        conn.execute(
            """
            INSERT INTO queue (news_id, status, attempts, next_attempt_at)
            VALUES (?, 'queued', 0, ?)
            """,
            (news_id, now),
        )
        return True


def get_next_queued(conn: sqlite3.Connection, now_utc: datetime) -> sqlite3.Row | None:
    now = now_utc.astimezone(timezone.utc).isoformat(timespec="seconds")
    with transaction(conn):
        row = conn.execute(
            """
            SELECT q.id as queue_id, q.news_id, n.title, n.canonical_url, n.source, n.published_at,
                   n.snippet, n.image_url, n.og_description, n.telegraph_url, n.safety_status
            FROM queue q
            JOIN news n ON n.news_id = q.news_id
            WHERE q.status IN ('queued', 'retry')
              AND q.next_attempt_at <= ?
              AND (q.locked_at IS NULL)
            ORDER BY n.published_at DESC, q.id ASC
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE queue SET status='sending', locked_at=? WHERE id=?",
            (now, row["queue_id"]),
        )
        return row


def set_telegraph_url(conn: sqlite3.Connection, news_id: str, telegraph_url: str) -> None:
    conn.execute(
        "UPDATE news SET telegraph_url=? WHERE news_id=?",
        (telegraph_url, news_id),
    )


def set_enrichment(
    conn: sqlite3.Connection,
    news_id: str,
    *,
    image_url: str | None,
    og_description: str | None,
) -> None:
    conn.execute(
        "UPDATE news SET image_url=COALESCE(?, image_url), og_description=COALESCE(?, og_description) WHERE news_id=?",
        (image_url, og_description, news_id),
    )


def set_canonical_url(conn: sqlite3.Connection, news_id: str, canonical_url: str) -> bool:
    try:
        conn.execute(
            "UPDATE news SET canonical_url=? WHERE news_id=?",
            (canonical_url, news_id),
        )
        return True
    except sqlite3.IntegrityError:
        # UNIQUE canonical_url conflict (already stored as another news row)
        return False


def mark_sent(conn: sqlite3.Connection, queue_id: int, telegram_message_id: str | None) -> None:
    now = _utc_now().isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE queue
        SET status='sent', sent_at=?, telegram_message_id=?, locked_at=NULL, last_error=NULL
        WHERE id=?
        """,
        (now, telegram_message_id, queue_id),
    )


def mark_retry(conn: sqlite3.Connection, queue_id: int, error: str, delay_sec: int) -> None:
    next_time = (_utc_now() + timedelta(seconds=delay_sec)).isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE queue
        SET status='retry', attempts=attempts+1, next_attempt_at=?, last_error=?, locked_at=NULL
        WHERE id=?
        """,
        (next_time, error[:500], queue_id),
    )


def reschedule_queue(conn: sqlite3.Connection, queue_id: int, next_attempt_utc: datetime) -> None:
    next_time = next_attempt_utc.astimezone(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE queue
        SET status='queued', next_attempt_at=?, locked_at=NULL
        WHERE id=?
        """,
        (next_time, queue_id),
    )
