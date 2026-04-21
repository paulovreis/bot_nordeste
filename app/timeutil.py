from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SendWindow:
    tz: ZoneInfo
    start: time
    end: time

    def is_open(self, now_utc: datetime) -> bool:
        local = now_utc.astimezone(self.tz)
        t = local.timetz().replace(tzinfo=None)
        return self.start <= t < self.end

    def next_open_utc(self, now_utc: datetime) -> datetime:
        local = now_utc.astimezone(self.tz)
        today = local.date()
        local_start = datetime.combine(today, self.start, tzinfo=self.tz)
        local_end = datetime.combine(today, self.end, tzinfo=self.tz)

        if local < local_start:
            return local_start.astimezone(ZoneInfo("UTC"))
        if local >= local_end:
            next_day = today + timedelta(days=1)
            next_start = datetime.combine(next_day, self.start, tzinfo=self.tz)
            return next_start.astimezone(ZoneInfo("UTC"))
        return now_utc


def parse_hhmm(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("Expected HH:MM")
    h = int(parts[0])
    m = int(parts[1])
    return time(hour=h, minute=m)
