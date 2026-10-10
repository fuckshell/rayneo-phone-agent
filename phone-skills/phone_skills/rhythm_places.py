"""Conservative commute estimation from bounded, accurate location samples."""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta


MAX_SAMPLES = 2000
MAX_ACCURACY_M = 100
DEFAULT_MINUTES = 45


def trim_samples(samples: list[dict]) -> list[dict]:
    def instant(item: dict) -> float:
        try:
            at = datetime.fromisoformat(str(item["captured_at"]))
            return at.timestamp() if at.tzinfo is not None else float("-inf")
        except (KeyError, TypeError, ValueError, OverflowError):
            return float("-inf")

    return sorted(samples, key=instant)[-MAX_SAMPLES:]


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radians = [math.radians(value) for value in (lat1, lon1, lat2, lon2)]
    a, b, c, d = radians
    delta = math.sin((c - a) / 2) ** 2 + math.cos(a) * math.cos(c) * math.sin((d - b) / 2) ** 2
    return 12742 * math.asin(min(1, math.sqrt(delta)))


def classify_place(sample: dict, anchors: dict) -> dict | None:
    try:
        if float(sample["accuracy_m"]) > MAX_ACCURACY_M:
            return None
        lat, lon = float(sample["lat"]), float(sample["lon"])
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    matches = []
    for label, anchor in anchors.items():
        if not anchor.get("confirmed"):
            continue
        try:
            distance = _distance_km(lat, lon, float(anchor["lat"]), float(anchor["lon"]))
            if distance <= max(0.15, float(anchor.get("radius_m", 200)) / 1000):
                matches.append((distance, label))
        except (KeyError, TypeError, ValueError):
            continue
    if not matches:
        return None
    distance, label = min(matches)
    return {"label": label, "distance_m": round(distance * 1000), "source": "confirmed_anchor"}


def learn_departure_habit(samples: list[dict], anchors: dict, target_day: date,
                          now: datetime) -> dict:
    unreliable = {"confidence": "unreliable", "sample_days": 0}
    if now.tzinfo is None:
        return unreliable
    observations: dict[date, list[tuple[datetime, str | None]]] = {}
    for sample in trim_samples(samples):
        try:
            at = datetime.fromisoformat(sample["captured_at"])
            lat, lon, accuracy = (float(sample[key]) for key in ("lat", "lon", "accuracy_m"))
            if (at.tzinfo is None or not 0 <= accuracy <= MAX_ACCURACY_M
                    or not -90 <= lat <= 90 or not -180 <= lon <= 180):
                continue
            at = at.astimezone(now.tzinfo)
            age = (target_day - at.date()).days
            if not 1 <= age <= 28 or (at.weekday() < 5) != (target_day.weekday() < 5):
                continue
            place = classify_place(sample, anchors)
            observations.setdefault(at.date(), []).append((at, place["label"] if place else None))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    departures = []
    for day, points in observations.items():
        points.sort()
        for previous, current, away in zip(points, points[1:], points[2:]):
            if (previous[1] and previous[1].startswith("家") and current[1] == previous[1]
                    and away[1] != previous[1]
                    and 5 <= (current[0] - previous[0]).total_seconds() / 60 <= 120
                    and 0 < (away[0] - current[0]).total_seconds() / 60 <= 120):
                departures.append(away[0].hour * 60 + away[0].minute)
                break
    unreliable["sample_days"] = len(departures)
    if len(departures) < 5 or max(departures) - min(departures) > 45:
        return unreliable
    median = round(statistics.median(departures))
    predicted = datetime.combine(target_day, datetime.min.time(), now.tzinfo) + timedelta(minutes=median)
    return {"confidence": "learned", "at": predicted.isoformat(), "sample_days": len(departures)}


def learn_commute(samples: list[dict], anchors: dict) -> dict:
    result = {"route": None, "sample_count": 0, "estimate_minutes": DEFAULT_MINUTES,
              "confidence": "default"}
    observations = []
    for sample in trim_samples(samples):
        place = classify_place(sample, anchors)
        if not place:
            continue
        try:
            instant = datetime.fromisoformat(sample["captured_at"])
            if instant.tzinfo is None:
                continue
            observations.append((instant, place["label"], sample))
        except (KeyError, TypeError, ValueError):
            continue
    observations.sort(key=lambda item: item[0])
    stays = []
    for previous, current in zip(observations, observations[1:]):
        minutes = (current[0] - previous[0]).total_seconds() / 60
        if previous[1] == current[1] and 5 <= minutes <= 120:
            if stays and stays[-1][1] == current[1] and (previous[0] - stays[-1][2]).total_seconds() < 3600:
                stays[-1] = (stays[-1][0], current[1], current[0], current[2])
            else:
                stays.append((previous[0], current[1], current[0], current[2]))
    trips: dict[tuple[str, str], list[int]] = {}
    for origin, destination in zip(stays, stays[1:]):
        if origin[1] == destination[1]:
            continue
        minutes = (destination[0] - origin[2]).total_seconds() / 60
        if not 5 <= minutes <= 180:
            continue
        distance = _distance_km(origin[3]["lat"], origin[3]["lon"],
                                destination[3]["lat"], destination[3]["lon"])
        if distance / (minutes / 60) > 130:
            continue
        trips.setdefault((origin[1], destination[1]), []).append(round(minutes))
    if not trips:
        return result
    route, durations = max(trips.items(), key=lambda item: len(item[1]))
    result.update(route=list(route), sample_count=len(durations))
    if len(durations) >= 3:
        result.update(estimate_minutes=round(statistics.median(durations)), confidence="learned")
    return result


def _destination_anchor(destination: str, anchors: dict) -> str | None:
    """Resolve a calendar location to a confirmed anchor without fuzzy GPS guessing."""
    text = str(destination or "").strip().casefold()
    if not text:
        return None
    candidates = []
    for label, anchor in anchors.items():
        if not isinstance(anchor, dict) or not anchor.get("confirmed"):
            continue
        normalized = str(label).strip().casefold()
        if text == normalized or text in normalized or normalized in text:
            candidates.append((abs(len(normalized) - len(text)), label))
    return min(candidates)[1] if candidates else None


def learn_segment_trips(segments: list[dict], anchors: dict) -> dict:
    """Learn event-specific departure/arrival times from complete phone segments.

    A segment is useful only when it contains a confirmed home departure and a
    confirmed destination arrival.  This deliberately ignores incomplete or
    ambiguous segments and requires three independent trips before promotion.
    """
    result = {"route": None, "sample_count": 0, "estimate_minutes": DEFAULT_MINUTES,
              "confidence": "default", "trips": []}
    if not isinstance(segments, list) or not isinstance(anchors, dict):
        return result
    trips = []
    for segment in segments[-200:]:
        if not isinstance(segment, dict):
            continue
        destination_label = _destination_anchor(segment.get("destination", ""), anchors)
        if not destination_label:
            continue
        observations = []
        for sample in segment.get("samples", []):
            if not isinstance(sample, dict):
                continue
            place = classify_place(sample, anchors)
            if not place:
                continue
            try:
                instant = datetime.fromisoformat(str(sample["captured_at"]))
                if instant.tzinfo is None:
                    continue
                observations.append((instant, place["label"]))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        observations.sort(key=lambda item: item[0])
        if len(observations) < 2:
            continue
        departure = None
        arrival = None
        for previous, current in zip(observations, observations[1:]):
            if departure is None and previous[1].startswith("家") and current[1] != previous[1]:
                # The last confirmed home point is the conservative departure
                # bound; the first non-home point may be several minutes later.
                departure = previous[0]
            if departure is not None and current[1] == destination_label:
                arrival = current[0]
                break
        if departure is None or arrival is None:
            continue
        minutes = (arrival - departure).total_seconds() / 60
        if not 5 <= minutes <= 180:
            continue
        trips.append({"segment_id": str(segment.get("segment_id", "")),
                      "event_uid": str(segment.get("event_uid", "")),
                      "origin": "home", "destination": destination_label,
                      "departed_at": departure.isoformat(),
                      "arrived_at": arrival.isoformat(),
                      "minutes": round(minutes)})
    if not trips:
        return result
    durations = [item["minutes"] for item in trips]
    route = ["home", trips[-1]["destination"]]
    result.update(route=route, sample_count=len(durations), trips=trips[-12:])
    if len(durations) >= 3:
        result.update(estimate_minutes=round(statistics.median(durations)), confidence="learned")
    return result
