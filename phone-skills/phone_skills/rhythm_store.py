"""Private, bounded sensor state and composition of a daily rhythm plan."""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .daily_rhythm import build_plan
from .coros_sleep import CorosSleepCache
from .rhythm_calendar import CalendarUnavailable, refresh_calendar
from .rhythm_places import (learn_commute, learn_departure_habit,
                             learn_segment_trips, trim_samples)
from .rhythm_amap import AmapCache, AmapClient
from .rhythm_weather import forecast_for_departure, weather_for_reminder


class RhythmStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("schema") == 1:
                return data
        except (OSError, ValueError, TypeError):
            pass
        return {"schema": 1, "locations": [], "segments": [], "anchors": {},
                "sleep": {"status": "unavailable"}}

    def update(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or payload.get("schema") not in {1, 2}:
            raise ValueError("invalid rhythm schema")
        locations = payload.get("locations", [])
        segments = payload.get("segments", [])
        anchors = payload.get("anchors", {})
        sleep = payload.get("sleep")
        if (not isinstance(locations, list) or not isinstance(segments, list)
                or not isinstance(anchors, dict) or len(locations) > 1000 or len(segments) > 32):
            raise ValueError("invalid rhythm samples")
        if not locations and not segments and sleep is None and not anchors:
            raise ValueError("empty rhythm payload")
        segment_locations = []
        for segment in segments:
            if not isinstance(segment, dict) or not str(segment.get("segment_id", "")):
                raise ValueError("invalid rhythm segment")
            samples = segment.get("samples", [])
            if not isinstance(samples, list) or len(samples) > 500:
                raise ValueError("invalid rhythm segment samples")
            for item in samples:
                segment_locations.append(item)
        for item in locations + segment_locations:
            try:
                lat, lon, accuracy = float(item["lat"]), float(item["lon"]), float(item["accuracy_m"])
                captured = datetime.fromisoformat(item["captured_at"])
                if (captured.tzinfo is None or not -90 <= lat <= 90 or not -180 <= lon <= 180
                        or not 0 <= accuracy <= 10000):
                    raise ValueError()
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid location sample") from exc
        if len(anchors) > 10 or any(not isinstance(key, str) or len(key) > 80 or
                                     not isinstance(value, dict) for key, value in anchors.items()):
            raise ValueError("invalid anchors")
        if sleep is not None and (not isinstance(sleep, dict) or sleep.get("status") not in
                                  {"unavailable", "permission_required", "no_records", "records_ok", "error"}):
            raise ValueError("invalid sleep status")
        with self._lock:
            state = self.read()
            state["locations"] = trim_samples(state["locations"] + locations + segment_locations)
            existing_segments = {str(item.get("segment_id")): item
                                 for item in state.get("segments", [])
                                 if isinstance(item, dict) and item.get("segment_id")}
            for segment in segments:
                segment_id = str(segment["segment_id"])
                previous = existing_segments.get(segment_id)
                if isinstance(previous, dict):
                    merged = dict(previous)
                    merged_samples = list(previous.get("samples", [])) + list(segment.get("samples", []))
                    unique_samples = []
                    seen_samples = set()
                    for item in merged_samples:
                        if not isinstance(item, dict):
                            continue
                        key = (str(item.get("captured_at", "")), str(item.get("lat", "")),
                               str(item.get("lon", "")))
                        if key in seen_samples:
                            continue
                        seen_samples.add(key)
                        unique_samples.append(item)
                    merged["samples"] = unique_samples[-500:]
                    for key in ("reason", "event_uid", "event_title", "destination", "started_at", "ended_at"):
                        if key in segment and segment[key] not in (None, ""):
                            merged[key] = segment[key]
                    existing_segments[segment_id] = merged
                else:
                    existing_segments[segment_id] = dict(segment)
            state["segments"] = list(existing_segments.values())[-200:]
            state["anchors"].update(anchors)
            if sleep is not None:
                # A phone Health Connect no_records/permission response must
                # never erase a valid COROS MCP cache already known to the PC.
                current_sleep = state.get("sleep")
                if (not isinstance(current_sleep, dict)
                        or current_sleep.get("status") != "records_ok"
                        or sleep.get("status") == "records_ok"):
                    state["sleep"] = sleep
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="rhythm-", suffix=".tmp", delete=False) as output:
                temp_path = Path(output.name)
                json.dump(state, output, ensure_ascii=False)
            try:
                os.replace(temp_path, self.path)
            finally:
                temp_path.unlink(missing_ok=True)
        return {"status": "ok", "location_count": len(state["locations"]),
                "segment_count": len(state["segments"])}


class RhythmService:
    def __init__(self, store: RhythmStore, ics_path: Path, now: Callable[[], datetime] | None = None,
                 sleep_cache: CorosSleepCache | None = None, amap_client: AmapClient | None = None):
        self.store = store
        self.ics_path = Path(ics_path)
        self.now = now or (lambda: datetime.now().astimezone())
        self.sleep_cache = sleep_cache
        self.amap_client = amap_client or AmapClient(os.environ.get("AMAP_WEB_KEY", ""))
        self.amap_cache = AmapCache(store.path.with_name("amap-cache.json"))

    def plan(self) -> dict:
        now = self.now()
        state = self.store.read()
        if self.sleep_cache is not None:
            sleep = self.sleep_cache.get(now)
            self.sleep_cache.maybe_refresh(now)
        else:
            sleep = state.get("sleep")
        try:
            events = refresh_calendar(self.ics_path, now)
        except (CalendarUnavailable, RuntimeError, OSError):
            events = []
        anchors = state.get("anchors", {})
        segment_learning = learn_segment_trips(state.get("segments", []), anchors)
        commute = learn_commute(state.get("locations", []), anchors)
        # Complete event segments are the stronger signal.  Before three
        # valid trips exist, retain the broader location estimator as a
        # fallback but expose the segment evidence in the returned plan.
        if segment_learning.get("confidence") == "learned":
            commute = segment_learning
        commute_input = {"minutes": commute["estimate_minutes"], "confidence":
                         "high" if commute["confidence"] == "learned" else "default"}
        samples = state.get("locations", [])
        def eligible_time(item: dict) -> datetime | None:
            try:
                captured = datetime.fromisoformat(str(item["captured_at"]))
                lat, lon, accuracy = (float(item[key]) for key in ("lat", "lon", "accuracy_m"))
                if (item.get("coordinate_system") != "WGS84" or captured.tzinfo is None
                        or not all(math.isfinite(value) for value in (lat, lon, accuracy))
                        or not -90 <= lat <= 90 or not -180 <= lon <= 180
                        or not 0 <= accuracy <= 100
                        or not timedelta(0) <= now - captured <= timedelta(hours=12)):
                    return None
                return captured
            except (KeyError, TypeError, ValueError, OverflowError):
                return None

        valid_samples = [(eligible_time(item), item) for item in samples]
        location = max(((at, item) for at, item in valid_samples if at is not None),
                       key=lambda pair: pair[0], default=(None, None))[1]
        weather_day = now.date() if now.hour < 12 else now.date() + timedelta(days=1)
        habit = learn_departure_habit(samples, anchors, weather_day, now)
        base = build_plan(now, events, commute_input, None, location, sleep, habit,
                          tracking_analysis=segment_learning)
        departure = base.get("departure")
        weather = (forecast_for_departure(location, datetime.fromisoformat(departure["at"]), now)
                   if location and departure else None)
        reminder = base["weather_reminder"]
        reminder_weather = weather_for_reminder(
            location, datetime.fromisoformat(reminder["at"]),
            datetime.fromisoformat(reminder["expected_departure_at"])
            if reminder["expected_departure_at"] else None,
            now, self.amap_client, self.amap_cache)
        plan = build_plan(now, events, commute_input, weather, location, sleep, habit,
                          reminder_weather, segment_learning)
        plan["commute"] = {
            "route": commute.get("route"),
            "estimate_minutes": commute.get("estimate_minutes", 45),
            "sample_count": commute.get("sample_count", 0),
            "confidence": commute.get("confidence", "default"),
            "source": "event_segments" if segment_learning.get("confidence") == "learned"
                      else "location_history",
        }
        if location:
            resolved = self.amap_cache.resolve(location, now, self.amap_client)
            if resolved.get("status") == "ok":
                plan["location"] = {
                    "status": "ok",
                    "district": resolved.get("district", ""),
                    "address": resolved.get("address", ""),
                    "sample_at": resolved.get("sample_at", location.get("captured_at", "")),
                    "queried_at": resolved.get("queried_at", now.isoformat()),
                    "source": resolved.get("source", "Amap"),
                    "accuracy_m": location.get("accuracy_m"),
                }
            else:
                plan["location"] = {"status": "unavailable", "reason": resolved.get("reason", "reverse_failed")}
        else:
            plan["location"] = {"status": "unavailable", "reason": "no_recent_sample"}
        return plan
