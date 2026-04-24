from __future__ import annotations

import os
from dataclasses import dataclass


def _get_env(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    return val.strip()


def _get_int(name: str, default: int) -> int:
    raw = _get_env(name)
    if raw is None:
        return default
    return int(raw)


def _get_bool(name: str, default: bool = False) -> bool:
    raw = _get_env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    bot_token: str
    chat_id: str
    use_telegraph: bool
    telegraph_token: str | None

    fetch_interval_min: int
    send_interval_min: int
    max_age_days: int

    timezone: str
    send_window_start: str
    send_window_end: str

    dry_run: bool
    log_level: str

    max_items_per_fetch: int
    max_items_per_query: int

    idle_poll_sec: int

    queries_override: list[str] | None

    require_image: bool
    image_fallback: bool

    dedup_window_hours: int
    dedup_hamming_max: int

    retention_days: int

    claude_token: str | None
    claude_model: str


def load_settings() -> Settings:
    bot_token = _get_env("BOT_TOKEN") or ""
    chat_id = _get_env("CHAT_ID") or ""
    use_telegraph = _get_bool("USE_TELEGRAPH", False)
    telegraph_token = _get_env("TELEGRAPH_TOKEN")
    if use_telegraph and not telegraph_token:
        raise ValueError("USE_TELEGRAPH=1 requer TELEGRAPH_TOKEN")

    queries_raw = _get_env("QUERIES")
    queries_override = None
    if queries_raw:
        queries_override = [q.strip() for q in queries_raw.split(",") if q.strip()]

    return Settings(
        bot_token=bot_token,
        chat_id=chat_id,
        use_telegraph=use_telegraph,
        telegraph_token=telegraph_token,
        fetch_interval_min=_get_int("FETCH_INTERVAL_MIN", 40),
        send_interval_min=_get_int("SEND_INTERVAL_MIN", 10),
        max_age_days=_get_int("MAX_AGE_DAYS", 30),
        timezone=_get_env("TIMEZONE", "America/Sao_Paulo") or "America/Sao_Paulo",
        send_window_start=_get_env("SEND_WINDOW_START", "08:00") or "08:00",
        send_window_end=_get_env("SEND_WINDOW_END", "22:00") or "22:00",
        dry_run=_get_bool("DRY_RUN", False),
        log_level=_get_env("LOG_LEVEL", "INFO") or "INFO",
        max_items_per_fetch=_get_int("MAX_ITEMS_PER_FETCH", 120),
        max_items_per_query=_get_int("MAX_ITEMS_PER_QUERY", 10),
        idle_poll_sec=_get_int("IDLE_POLL_SEC", 20),
        queries_override=queries_override,
        require_image=_get_bool("REQUIRE_IMAGE", True),
        image_fallback=_get_bool("IMAGE_FALLBACK", True),

        dedup_window_hours=_get_int("DEDUP_WINDOW_HOURS", 24),
        dedup_hamming_max=_get_int("DEDUP_HAMMING_MAX", 8),

        retention_days=_get_int("RETENTION_DAYS", 30),

        claude_token=_get_env("CLAUDE_TOKEN"),
        claude_model=_get_env("CLAUDE_MODEL", "claude-3-5-haiku-latest") or "claude-3-5-haiku-latest",
    )
