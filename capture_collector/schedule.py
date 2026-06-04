from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_RANGE_RE = re.compile(r"^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$")


@dataclass(frozen=True)
class ActiveHours:
    """Daily capture window in local or configured timezone."""

    start_minutes: int  # minutes since midnight [0, 1439]
    end_minutes: int
    timezone: str | None = None  # IANA name; None = system local timezone

    @property
    def label(self) -> str:
        return f"{_minutes_to_hhmm(self.start_minutes)}-{_minutes_to_hhmm(self.end_minutes)}"


def _minutes_to_hhmm(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h:02d}:{m:02d}"


def _parse_hhmm(value: str, field: str) -> int:
    text = str(value).strip()
    match = _TIME_RE.match(text)
    if not match:
        raise ValueError(f"{field} must be HH:MM (got {value!r})")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError(f"{field} out of range: {value!r}")
    return hour * 60 + minute


def parse_active_hours(raw: Any, *, default_tz: str | None = None) -> ActiveHours | None:
    """
    YAML examples:
      active_hours: "05:00-20:00"
      active_hours:
        start: "05:00"
        end: "20:00"
        timezone: Asia/Kolkata
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        match = _RANGE_RE.match(text)
        if not match:
            raise ValueError(
                'active_hours string must look like "05:00-20:00" (got {0!r})'.format(raw)
            )
        start = _parse_hhmm(match.group(1), "active_hours.start")
        end = _parse_hhmm(match.group(2), "active_hours.end")
        return ActiveHours(start_minutes=start, end_minutes=end, timezone=default_tz)
    if isinstance(raw, dict):
        start_raw = raw.get("start")
        end_raw = raw.get("end")
        if start_raw is None or end_raw is None:
            raise ValueError("active_hours mapping requires start and end (HH:MM)")
        tz = str(raw.get("timezone") or default_tz or "").strip() or None
        return ActiveHours(
            start_minutes=_parse_hhmm(str(start_raw), "active_hours.start"),
            end_minutes=_parse_hhmm(str(end_raw), "active_hours.end"),
            timezone=tz,
        )
    raise ValueError("active_hours must be a string range or a mapping with start/end")


def now_in_schedule_tz(active_hours: ActiveHours | None) -> datetime:
    if active_hours is None or not active_hours.timezone:
        return datetime.now().astimezone()
    return datetime.now(ZoneInfo(active_hours.timezone))


def is_within_active_hours(active_hours: ActiveHours | None, when: datetime | None = None) -> bool:
    if active_hours is None:
        return True
    when = when or now_in_schedule_tz(active_hours)
    minute_of_day = when.hour * 60 + when.minute
    start, end = active_hours.start_minutes, active_hours.end_minutes
    if start == end:
        return True
    if start < end:
        return start <= minute_of_day < end
    return minute_of_day >= start or minute_of_day < end


def seconds_until_window_opens(active_hours: ActiveHours, when: datetime | None = None) -> float:
    """Seconds to wait until the next window open (>= 1)."""
    when = when or now_in_schedule_tz(active_hours)
    if is_within_active_hours(active_hours, when):
        return 1.0

    tz = when.tzinfo or timezone.utc
    start, end = active_hours.start_minutes, active_hours.end_minutes
    minute_of_day = when.hour * 60 + when.minute

    open_today = when.replace(
        hour=start // 60,
        minute=start % 60,
        second=0,
        microsecond=0,
    )
    if start < end:
        if minute_of_day < start:
            target = open_today
        else:
            target = open_today + timedelta(days=1)
    else:
        if minute_of_day >= end and minute_of_day < start:
            target = open_today
        else:
            target = open_today + timedelta(days=1)

    delta = (target - when).total_seconds()
    return max(1.0, delta)


def wait_until_active(
    active_hours: ActiveHours | None,
    stop: Any,
    *,
    log: Any,
    poll_sec: float = 30.0,
) -> bool:
    """
    Block until inside active hours or stop is set.
    Returns True if window is open, False if stop was requested.
    """
    if active_hours is None or is_within_active_hours(active_hours):
        return True

    while not stop.is_set():
        when = now_in_schedule_tz(active_hours)
        if is_within_active_hours(active_hours, when):
            return True
        secs = seconds_until_window_opens(active_hours, when)
        tz_label = active_hours.timezone or "local"
        log.info(
            "outside active hours (%s %s); next capture window in %.0fs",
            active_hours.label,
            tz_label,
            secs,
        )
        remaining = secs
        while remaining > 0 and not stop.is_set():
            chunk = min(remaining, poll_sec)
            if stop.wait(chunk):
                return False
            remaining -= chunk
    return False
