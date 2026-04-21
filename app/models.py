from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class NewsItem:
    news_id: str
    canonical_url: str
    title: str
    source: str
    published_at: datetime
    snippet: str

    image_url: str | None = None
    og_description: str | None = None
    telegraph_url: str | None = None

    safety_status: str = "unknown"  # safe|adult|unknown
    safety_reason: str | None = None
