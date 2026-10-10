"""Time-matched Open-Meteo forecast from a recent approximate position."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Callable


def _fetch(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def forecast_for_departure(location: dict, departure_at: datetime, now: datetime,
                           fetch: Callable = _fetch, max_location_age_hours: int = 2) -> dict:
    unavailable = {"status": "unavailable"}
    if now.tzinfo is None or departure_at.tzinfo is None:
        return unavailable
    try:
        lat, lon = float(location["lat"]), float(location["lon"])
        accuracy = float(location["accuracy_m"])
        captured = datetime.fromisoformat(str(location["captured_at"]))
        if (captured.tzinfo is None or not -90 <= lat <= 90 or not -180 <= lon <= 180
                or accuracy < 0 or accuracy > 100
                or not timedelta(0) <= now - captured <= timedelta(hours=max_location_age_hours)):
            return unavailable
        query = urllib.parse.urlencode({"latitude": round(lat, 2), "longitude": round(lon, 2),
                                        "hourly": "temperature_2m,precipitation_probability",
                                        "timezone": "Asia/Shanghai", "forecast_days": 3})
        data = fetch("https://api.open-meteo.com/v1/forecast?" + query, 5)
        hourly = data["hourly"]
        choices = []
        for index, text_time in enumerate(hourly["time"]):
            at = datetime.fromisoformat(text_time)
            if at.tzinfo is None:
                at = at.replace(tzinfo=departure_at.tzinfo)
            distance = abs((at - departure_at).total_seconds())
            if distance <= 1800:
                choices.append((distance, index, at))
        if not choices:
            return unavailable
        _, index, valid = min(choices)
        temperature = float(hourly["temperature_2m"][index])
        rain = int(hourly["precipitation_probability"][index])
        if not -80 <= temperature <= 60 or not 0 <= rain <= 100:
            return unavailable
        clothing = "穿厚外套" if temperature < 10 else ("带件外套" if temperature < 18 else "穿轻薄衣物")
        advice = clothing + ("，带伞防雨" if rain >= 40 else "")
        return {"status": "ok", "temperature_c": temperature, "rain_probability": rain,
                "advice": advice, "valid_at": valid.isoformat(), "fetched_at": now.isoformat(),
                "target_at": departure_at.isoformat(),
                "target_delta_minutes": round(abs((valid - departure_at).total_seconds()) / 60),
                "forecast_age_minutes": 0,
                "timezone": "Asia/Shanghai", "source": "Open-Meteo"}
    except (KeyError, IndexError, TypeError, ValueError, OSError, TimeoutError,
            urllib.error.URLError, json.JSONDecodeError):
        return unavailable


def weather_for_reminder(location: dict | None, reminder_at: datetime,
                         expected_departure_at: datetime | None, now: datetime,
                         amap, cache, fetch: Callable = _fetch) -> dict:
    unavailable = {"status": "unavailable"}
    if not location or not amap or not cache or now.tzinfo is None:
        return unavailable
    try:
        captured = datetime.fromisoformat(str(location["captured_at"]))
        if (location.get("coordinate_system") != "WGS84" or captured.tzinfo is None
                or not timedelta(0) <= now - captured <= timedelta(hours=12)):
            return unavailable
        place = cache.resolve(location, now, amap)
        if place.get("status") != "ok":
            return unavailable
        target = expected_departure_at or reminder_at
        district = str(place["district"])
        location_age = "older" if now - captured > timedelta(hours=2) else "recent"
        forecast = forecast_for_departure(location, target, now, fetch=fetch,
                                          max_location_age_hours=12)
        if forecast.get("status") == "ok":
            return {"status": "ok", "district": district, "location_age": location_age,
                    "temperature_c": forecast["temperature_c"],
                    "rain_probability": forecast["rain_probability"],
                    "advice": forecast["advice"], "valid_at": forecast["valid_at"],
                    "valid_until": (datetime.fromisoformat(forecast["valid_at"]) + timedelta(hours=1)).isoformat(),
                    "fetched_at": forecast["fetched_at"], "target_at": target.isoformat(),
                    "target_delta_minutes": forecast.get("target_delta_minutes", 0),
                    "forecast_age_minutes": forecast.get("forecast_age_minutes", 0),
                    "location_captured_at": captured.isoformat(),
                    "timezone": forecast.get("timezone", "Asia/Shanghai"),
                    "source": "Open-Meteo forecast"}
        current = amap.weather(str(place["adcode"]), now)
        if current.get("status") == "ok" and abs((target - now).total_seconds()) <= 3600:
            return {"status": "ok", "district": district, "location_age": location_age,
                    "temperature_c": current["temperature_c"],
                    "condition": current.get("condition", ""), "valid_at": current["valid_at"],
                    "valid_until": (datetime.fromisoformat(current["valid_at"]) + timedelta(hours=2)).isoformat(),
                    "fetched_at": current["fetched_at"], "target_at": target.isoformat(),
                    "target_delta_minutes": round(abs((target - now).total_seconds()) / 60),
                    "forecast_age_minutes": round(max(0, (now - datetime.fromisoformat(current["fetched_at"])).total_seconds()) / 60),
                    "location_captured_at": captured.isoformat(),
                    "timezone": "Asia/Shanghai", "source": "Amap current"}
        return {"status": "partial", "district": district, "location_age": location_age,
                "source": "Amap location"}
    except (KeyError, TypeError, ValueError, OverflowError):
        return unavailable
