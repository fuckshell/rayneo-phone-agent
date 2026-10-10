"""Deterministic daily reminder decisions; all inputs use local-aware times."""
from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, time, timedelta
from typing import Any


# User-adjustable defaults. Preparation is before actual intended sleep.
BASELINE_PREPARE = time(22, 30)
PREPARATION_MINUTES = 75
TARGET_SLEEP_HOURS = 8
WAKE_TO_DEPARTURE_MINUTES = 60
ARRIVAL_BUFFER_MINUTES = 15
DEFAULT_COMMUTE_MINUTES = 45
IDLE_TRACK_INTERVAL_SECONDS = 30 * 60
EVENT_TRACK_INTERVAL_SECONDS = 5 * 60
EVENT_TRACK_LEAD_MINUTES = 90
EVENT_TRACK_TAIL_MINUTES = 30


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return result if result.tzinfo is not None else None


def build_plan(now: datetime, events: list[dict], commute: dict,
               weather: dict | None, location: dict | None,
               sleep: dict | None, departure_habit: dict | None = None,
               weather_reminder_weather: dict | None = None,
               tracking_analysis: dict | None = None) -> dict:
    if now.tzinfo is None:
        raise ValueError("now must have a timezone")
    local_day = now.date()
    baseline = datetime.combine(local_day, BASELINE_PREPARE, now.tzinfo)
    next_day = local_day + timedelta(days=1)
    eligible = []
    for event in events:
        start = _datetime(event.get("start_at"))
        if (start and start.astimezone(now.tzinfo).date() in (local_day, next_day)
                and event.get("confidence") == "high"
                and event.get("status") != "cancelled"):
            eligible.append((start, event))
    eligible.sort(key=lambda item: item[0])
    tomorrow_events = [(start, event) for start, event in eligible
                       if start.astimezone(now.tzinfo).date() == next_day]
    plan: dict[str, Any] = {
        "schema": 1,
        "local_date": local_day.isoformat(),
        "expires_at": datetime.combine(next_day + timedelta(days=1), time.min, now.tzinfo).isoformat(),
        # The phone UI needs the same source-of-truth that was used to make
        # the bedtime decision; do not force it to query Health Connect again.
        "sleep": sleep if isinstance(sleep, dict) else {"status": "unavailable"},
        "bedtime": {
            "at": baseline.isoformat(),
            "purpose": "start_bath_and_bed_preparation",
            "basis": "22:30 regularity baseline",
            "source": "local_policy",
        },
    }
    if tomorrow_events:
        start, event = tomorrow_events[0]
        learned = commute.get("minutes") if commute.get("confidence") == "high" else None
        minutes = int(learned) if isinstance(learned, (int, float)) and 5 <= learned <= 180 else DEFAULT_COMMUTE_MINUTES
        departure = start - timedelta(minutes=minutes + ARRIVAL_BUFFER_MINUTES)
        wake = departure - timedelta(minutes=WAKE_TO_DEPARTURE_MINUTES)
        prepare = wake - timedelta(hours=TARGET_SLEEP_HOURS, minutes=PREPARATION_MINUTES)
        if prepare < baseline:
            plan["bedtime"].update(at=prepare.isoformat(), basis="next_day_early_arrival",
                                   source=event.get("source", "calendar"))
    upcoming = [(start, event) for start, event in eligible
                if start - timedelta(minutes=DEFAULT_COMMUTE_MINUTES + ARRIVAL_BUFFER_MINUTES) > now]
    if upcoming:
        start, event = upcoming[0]
        learned = commute.get("minutes") if commute.get("confidence") == "high" else None
        minutes = int(learned) if isinstance(learned, (int, float)) and 5 <= learned <= 180 else DEFAULT_COMMUTE_MINUTES
        departure = start - timedelta(minutes=minutes + ARRIVAL_BUFFER_MINUTES)
        destination = str(event.get("location") or "").strip()
        item: dict[str, Any] = {
            "at": departure.isoformat(),
            "event_uid": str(event.get("uid") or ""),
            "event_title": str(event.get("title") or ""),
            "destination": destination,
            "navigation_allowed": bool(destination),
            "source": event.get("source", "calendar"),
            "commute_minutes": minutes,
            "commute_source": "learned" if learned is not None and minutes != DEFAULT_COMMUTE_MINUTES else "default",
            "weather": {"status": "unavailable"},
        }
        if weather and weather.get("status") == "ok":
            valid = _datetime(weather.get("valid_at"))
            if valid and abs((valid - departure).total_seconds()) <= 3600:
                item["weather"] = {key: weather[key] for key in
                                   ("status", "temperature_c", "rain_probability", "advice", "valid_at", "source")
                                   if key in weather}
        plan["departure"] = item

    # Tracking is calendar-driven but bounded.  The phone stays on its idle
    # cadence until the first useful pre-departure window, then records a
    # short trajectory segment at a higher cadence and flushes it afterwards.
    tracking_windows = []
    for start, event in eligible:
        if start <= now or start - now > timedelta(hours=36):
            continue
        learned = commute.get("minutes") if commute.get("confidence") == "high" else None
        minutes = int(learned) if isinstance(learned, (int, float)) and 5 <= learned <= 180 else DEFAULT_COMMUTE_MINUTES
        departure_at = start - timedelta(minutes=minutes + ARRIVAL_BUFFER_MINUTES)
        window_start = departure_at - timedelta(minutes=EVENT_TRACK_LEAD_MINUTES)
        window_end = start + timedelta(minutes=EVENT_TRACK_TAIL_MINUTES)
        tracking_windows.append({
            "event_uid": str(event.get("uid") or ""),
            "event_title": str(event.get("title") or ""),
            "destination": str(event.get("location") or "").strip(),
            "start_at": window_start.isoformat(),
            "end_at": window_end.isoformat(),
            "departure_at": departure_at.isoformat(),
            "event_at": start.isoformat(),
            "interval_seconds": EVENT_TRACK_INTERVAL_SECONDS,
            "source": event.get("source", "calendar"),
        })
    tracking_windows.sort(key=lambda item: item["start_at"])
    active_window = next((item for item in tracking_windows
                          if _datetime(item["start_at"]) <= now < _datetime(item["end_at"])), None)
    next_window = next((item for item in tracking_windows
                        if _datetime(item["end_at"]) > now), None)
    cadence_window = active_window or next_window or {}
    plan["tracking"] = {
        "mode": "event_window" if active_window else "scheduled" if next_window else "idle",
        "interval_seconds": cadence_window.get("interval_seconds", IDLE_TRACK_INTERVAL_SECONDS),
        "idle_interval_seconds": IDLE_TRACK_INTERVAL_SECONDS,
        "windows": tracking_windows[:8],
        "active_event_uid": (active_window or {}).get("event_uid", ""),
    }
    if isinstance(tracking_analysis, dict):
        # Keep the analysis bounded and explicit so the phone can show why a
        # departure estimate changed without exposing raw location history.
        plan["tracking"]["learned"] = {
            key: tracking_analysis[key] for key in
            ("route", "sample_count", "estimate_minutes", "confidence", "trips")
            if key in tracking_analysis
        }
    weather_day = local_day if now.hour < 12 else next_day
    fallback = datetime.combine(weather_day, time(7, 0), now.tzinfo)
    expected = None
    basis = "07:00_fallback"
    if "departure" in plan:
        departure_at = datetime.fromisoformat(plan["departure"]["at"])
        if departure_at.date() == weather_day:
            expected = departure_at
            basis = "calendar"
    if expected is None and departure_habit and departure_habit.get("confidence") == "learned":
        candidate = _datetime(departure_habit.get("at"))
        if candidate and candidate.astimezone(now.tzinfo).date() == weather_day:
            expected = candidate
            basis = "habit"
    reminder_at = max(now, expected - timedelta(minutes=45)) if expected else fallback
    if reminder_at.astimezone(now.tzinfo).date() != weather_day:
        reminder_at, expected, basis = fallback, None, "07:00_fallback"
    plan["weather_reminder"] = {
        "local_date": weather_day.isoformat(), "at": reminder_at.isoformat(),
        "expected_departure_at": expected.isoformat() if expected else None,
        "basis": basis, "weather": weather_reminder_weather or {"status": "unavailable"},
    }
    if isinstance(sleep, dict) and sleep.get("status") == "records_ok":
        try:
            queried_at = _datetime(sleep.get("queried_at"))
            recent = [item["main_sleep_minutes"] for item in sleep.get("records", [])
                      if isinstance(item.get("main_sleep_minutes"), int)
                      and 30 <= item["main_sleep_minutes"] <= 960
                      and 0 <= (local_day - datetime.fromisoformat(item["wake_date"]).date()).days <= 7]
            if (queried_at and timedelta(0) <= now - queried_at <= timedelta(hours=24)
                    and len(recent) >= 3 and statistics.median(recent[-3:]) < 7 * 60):
                adjusted = baseline - timedelta(minutes=15)
                current = datetime.fromisoformat(plan["bedtime"]["at"])
                if adjusted < current:
                    plan["bedtime"].update(at=adjusted.isoformat(), basis="recent_short_sleep",
                                           source="coros")
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    plan["plan_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return plan
