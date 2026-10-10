"""Refresh the Feishu-backed calendar, then expand dated ICS events."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path

import recurring_ical_events
from icalendar import Calendar


class CalendarUnavailable(RuntimeError):
    pass


_lock = threading.Lock()
_last_refresh: dict[str, datetime] = {}
REFRESH_INTERVAL = timedelta(minutes=30)
_refresh = os.environ.get("PHONE_AI_CALENDAR_REFRESH", "").strip()
WSL_REFRESH = shlex.split(_refresh) if _refresh else []


def _aware(value: datetime | date, zone) -> datetime | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def _load_events(ics_path: Path, now: datetime, days: tuple[int, ...]) -> list[dict]:
    if now.tzinfo is None:
        raise ValueError("now must have timezone")
    try:
        calendar = Calendar.from_ical(ics_path.read_bytes())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise CalendarUnavailable("calendar file unavailable") from exc
    selected = {now.date() + timedelta(days=day) for day in days}
    begin = datetime.combine(min(selected), time.min, now.tzinfo)
    end = datetime.combine(max(selected) + timedelta(days=1), time.min, now.tzinfo)
    try:
        occurrences = recurring_ical_events.of(calendar).between(begin, end)
    except (ValueError, TypeError, KeyError) as exc:
        raise CalendarUnavailable("calendar recurrence invalid") from exc
    result = []
    for entry in occurrences:
        if str(entry.get("STATUS", "")).upper() == "CANCELLED":
            continue
        start_field = entry.get("DTSTART")
        if start_field is None:
            continue
        start = _aware(start_field.dt, now.tzinfo)
        if start is None or start.date() not in selected:
            continue
        end_field = entry.get("DTEND")
        finish = _aware(end_field.dt, now.tzinfo) if end_field else None
        title = str(entry.get("SUMMARY", "")).strip()
        location = str(entry.get("LOCATION", "")).strip()
        ambiguous = any(word in title for word in ("可能", "待定", "别人", "代班", "候选"))
        confidence = "low" if ambiguous or not title else "high"
        result.append({"uid": str(entry.get("UID", "")), "start_at": start.isoformat(),
                       "end_at": finish.isoformat() if finish else None, "title": title,
                       "location": location, "source": "feishu_calendar",
                       "confidence": confidence})
    return sorted(result, key=lambda item: item["start_at"])


def load_tomorrow_events(ics_path: Path, now: datetime) -> list[dict]:
    return _load_events(ics_path, now, (1,))


def load_today_and_tomorrow_events(ics_path: Path, now: datetime) -> list[dict]:
    return _load_events(ics_path, now, (0, 1))


def refresh_calendar(ics_path: Path, now: datetime) -> list[dict]:
    """Only read ICS after a successful Feishu pull, or a recent successful pull."""
    key = str(ics_path)
    with _lock:
        last = _last_refresh.get(key)
        if last is None or now - last >= REFRESH_INTERVAL or now < last:
            try:
                completed = subprocess.run(WSL_REFRESH, capture_output=True, text=True,
                                           encoding="utf-8", errors="replace", timeout=90, check=False)
                payload = json.loads(completed.stdout.strip().splitlines()[-1])
                if completed.returncode != 0 or payload.get("calendar", {}).get("ok") is not True:
                    raise CalendarUnavailable("Feishu calendar pull failed")
            except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, TypeError) as exc:
                raise CalendarUnavailable("Feishu calendar pull unavailable") from exc
            _last_refresh[key] = now
    return load_today_and_tomorrow_events(ics_path, now)
