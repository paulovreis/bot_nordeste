from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .util import hamming64


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
            story_hash INTEGER,
            band1 INTEGER,
            band2 INTEGER,
            band3 INTEGER,
            band4 INTEGER,
            dedup_status TEXT,
            dedup_of_news_id TEXT,
            dedup_reason TEXT,
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

    # Lightweight schema migrations for existing DBs.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(news)").fetchall()}
    def _add(col: str, ddl: str) -> None:
        if col not in cols:
            conn.execute(f"ALTER TABLE news ADD COLUMN {ddl}")

    _add("story_hash", "story_hash INTEGER")
    _add("band1", "band1 INTEGER")
    _add("band2", "band2 INTEGER")
    _add("band3", "band3 INTEGER")
    _add("band4", "band4 INTEGER")
    _add("dedup_status", "dedup_status TEXT")
    _add("dedup_of_news_id", "dedup_of_news_id TEXT")
    _add("dedup_reason", "dedup_reason TEXT")

    # Create dedup indexes only after columns exist (prevents crash on existing DBs).
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_news_band1 ON news(published_at, band1)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_news_band2 ON news(published_at, band2)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_news_band3 ON news(published_at, band3)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_news_band4 ON news(published_at, band4)")
    except sqlite3.OperationalError:
        # If something is off (corrupt/partial schema), skip index creation.
        pass


def _u64_to_i64(u: int) -> int:
    # SQLite INTEGER is signed 64-bit; store simhash as signed.
    u &= (1 << 64) - 1
    if u >= (1 << 63):
        return int(u - (1 << 64))
    return int(u)


def _i64_to_u64(i: int) -> int:
    i = int(i)
    if i < 0:
        return int(i + (1 << 64))
    return int(i)


def find_story_duplicate(
    conn: sqlite3.Connection,
    *,
    story_hash_u64: int,
    band1: int,
    band2: int,
    band3: int,
    band4: int,
    cutoff_published_iso: str,
    hamming_max: int,
) -> tuple[str, int] | None:
    if not story_hash_u64:
        return None

    rows = conn.execute(
        """
        SELECT news_id, story_hash
        FROM news
        WHERE published_at >= ?
          AND story_hash IS NOT NULL
          AND (dedup_status IS NULL OR dedup_status != 'dup')
          AND (band1 = ? OR band2 = ? OR band3 = ? OR band4 = ?)
        ORDER BY published_at DESC
        LIMIT 200
        """,
        (cutoff_published_iso, band1, band2, band3, band4),
    ).fetchall()
    if not rows:
        return None

    best_id = None
    best_d = 10**9
    for r in rows:
        sh = r["story_hash"]
        if sh is None:
            continue
        other = _i64_to_u64(int(sh))
        d = hamming64(story_hash_u64, other)
        if d < best_d:
            best_d = d
            best_id = str(r["news_id"])
            if best_d == 0:
                break

    if best_id is not None and best_d <= int(hamming_max):
        return best_id, int(best_d)
    return None


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
    story_hash_u64: int | None = None,
    band1: int | None = None,
    band2: int | None = None,
    band3: int | None = None,
    band4: int | None = None,
    dedup_window_hours: int = 24,
    dedup_hamming_max: int = 3,
) -> bool:
    now = _utc_now().isoformat(timespec="seconds")
    published_iso = published_at.astimezone(timezone.utc).isoformat(timespec="seconds")

    dedup_status = None
    dedup_of = None
    dedup_reason = None

    sh_u64 = int(story_hash_u64 or 0)
    b1 = int(band1 or 0)
    b2 = int(band2 or 0)
    b3 = int(band3 or 0)
    b4 = int(band4 or 0)

    # Story-level dedup (cross-portal) within a rolling window.
    cutoff = (_utc_now() - timedelta(hours=int(dedup_window_hours))).isoformat(timespec="seconds")
    dup = None
    if sh_u64 and (b1 or b2 or b3 or b4):
        dup = find_story_duplicate(
            conn,
            story_hash_u64=sh_u64,
            band1=b1,
            band2=b2,
            band3=b3,
            band4=b4,
            cutoff_published_iso=cutoff,
            hamming_max=int(dedup_hamming_max),
        )
    if dup:
        dedup_status = "dup"
        dedup_of, dist = dup
        dedup_reason = f"simhash_hamming:{dist}"

    with transaction(conn):
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO news (
                news_id, canonical_url, title, source, published_at, snippet, image_url, og_description,
                safety_status, safety_reason,
                story_hash, band1, band2, band3, band4,
                dedup_status, dedup_of_news_id, dedup_reason,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                _u64_to_i64(sh_u64) if sh_u64 else None,
                b1 if sh_u64 else None,
                b2 if sh_u64 else None,
                b3 if sh_u64 else None,
                b4 if sh_u64 else None,
                dedup_status,
                dedup_of,
                dedup_reason,
                now,
            ),
        )

        inserted = cur.rowcount == 1
        if not inserted:
            return False

        if safety_status == "adult":
            return True  # stored, but not queued

        if dedup_status == "dup":
            return True  # stored for audit, but not queued

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


def get_news_id_by_canonical_url(conn: sqlite3.Connection, canonical_url: str) -> str | None:
    row = conn.execute(
        "SELECT news_id FROM news WHERE canonical_url=? LIMIT 1",
        (canonical_url,),
    ).fetchone()
    return str(row["news_id"]) if row else None


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


def mark_skipped(conn: sqlite3.Connection, queue_id: int, reason: str) -> None:
    conn.execute(
        """
        UPDATE queue
        SET status='skipped', locked_at=NULL, last_error=?
        WHERE id=?
        """,
        (reason[:500], queue_id),
    )


def mark_news_dedup(
    conn: sqlite3.Connection,
    news_id: str,
    *,
    dedup_of_news_id: str,
    reason: str,
) -> None:
    conn.execute(
        """
        UPDATE news
        SET dedup_status='dup', dedup_of_news_id=?, dedup_reason=?
        WHERE news_id=?
        """,
        (dedup_of_news_id, reason[:500], news_id),
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
