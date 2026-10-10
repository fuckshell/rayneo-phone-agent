"""Amap Web Service location candidates and district conditions."""
from __future__ import annotations

import json
import math
import os
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


CHINA_TZ = timezone(timedelta(hours=8))


def _fetch(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _success(data: dict) -> bool:
    return isinstance(data, dict) and str(data.get("status")) == "1" and str(data.get("infocode")) == "10000"


def _sample_time(sample: dict, now: datetime) -> datetime | None:
    try:
        lat, lon, accuracy = (float(sample[key]) for key in ("lat", "lon", "accuracy_m"))
        captured = datetime.fromisoformat(str(sample["captured_at"]))
        if (sample.get("coordinate_system") != "WGS84" or captured.tzinfo is None
                or not all(math.isfinite(value) for value in (lat, lon, accuracy))
                or not -90 <= lat <= 90 or not -180 <= lon <= 180
                or not 0 <= accuracy <= 100 or not timedelta(0) <= now - captured <= timedelta(hours=12)):
            return None
        return captured
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


class AmapClient:
    def __init__(self, key: str, fetch: Callable[[str, float], dict] = _fetch):
        self.key = key
        self.fetch = fetch

    def _request(self, path: str, params: dict) -> dict:
        if not self.key:
            return {}
        query = urllib.parse.urlencode({**params, "key": self.key, "output": "JSON"})
        try:
            return self.fetch("https://restapi.amap.com" + path + "?" + query, 5)
        except (OSError, TimeoutError, ValueError, TypeError):
            return {}

    def resolve(self, sample: dict, now: datetime) -> dict:
        captured = _sample_time(sample, now)
        if captured is None:
            return {"status": "unavailable", "reason": "invalid_location"}
        if not self.key:
            return {"status": "unavailable", "reason": "key_unavailable"}
        lon, lat = float(sample["lon"]), float(sample["lat"])
        converted = self._request("/v3/assistant/coordinate/convert",
                                  {"locations": f"{lon:g},{lat:g}", "coordsys": "gps"})
        if not _success(converted):
            return {"status": "unavailable", "reason": "convert_failed"}
        try:
            pair = str(converted["locations"]).split(",")
            if len(pair) != 2:
                raise ValueError()
            gcj_lon, gcj_lat = map(float, pair)
            if not math.isfinite(gcj_lon) or not math.isfinite(gcj_lat) or not -180 <= gcj_lon <= 180 or not -90 <= gcj_lat <= 90:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            return {"status": "unavailable", "reason": "invalid_conversion"}
        data = self._request("/v3/geocode/regeo",
                             {"location": f"{gcj_lon:g},{gcj_lat:g}", "extensions": "base"})
        if not _success(data):
            return {"status": "unavailable", "reason": "reverse_failed"}
        try:
            geocode = data["regeocode"]
            component = geocode["addressComponent"]
            adcode = str(component["adcode"])
            district = component["district"]
            if not adcode.isdigit() or len(adcode) != 6 or not isinstance(district, str) or not district:
                raise ValueError()
            address = geocode.get("formatted_address", "")
            return {"status": "ok", "adcode": adcode, "district": district,
                    "address": address if isinstance(address, str) else "",
                    "sample_at": captured.isoformat(), "queried_at": now.isoformat(), "source": "Amap"}
        except (KeyError, TypeError, ValueError):
            return {"status": "unavailable", "reason": "invalid_district"}

    def weather(self, adcode: str, now: datetime) -> dict:
        if not adcode.isdigit() or len(adcode) != 6 or not self.key:
            return {"status": "unavailable", "reason": "invalid_adcode_or_key"}
        data = self._request("/v3/weather/weatherInfo", {"city": adcode, "extensions": "base"})
        if not _success(data):
            return {"status": "unavailable", "reason": "weather_failed"}
        try:
            live = data["lives"][0]
            if str(live["adcode"]) != adcode:
                raise ValueError()
            temperature = float(live["temperature"])
            reported = datetime.strptime(live["reporttime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHINA_TZ)
            if not math.isfinite(temperature) or not -80 <= temperature <= 60 or not timedelta(0) <= now - reported <= timedelta(hours=2):
                raise ValueError()
            return {"status": "ok", "adcode": adcode, "temperature_c": temperature,
                    "condition": str(live.get("weather") or ""), "valid_at": reported.isoformat(),
                    "fetched_at": now.isoformat(), "source": "Amap current"}
        except (KeyError, IndexError, TypeError, ValueError):
            return {"status": "unavailable", "reason": "invalid_or_stale_weather"}


class AmapCache:
    def __init__(self, path: Path):
        self.path = Path(path)

    def resolve(self, sample: dict, now: datetime, client: AmapClient) -> dict:
        if _sample_time(sample, now) is None:
            return {"status": "unavailable", "reason": "invalid_location"}
        sample_key = {key: sample.get(key) for key in ("lat", "lon", "accuracy_m", "captured_at", "coordinate_system")}
        try:
            cached = json.loads(self.path.read_text(encoding="utf-8"))
            if cached.get("sample") == sample_key and cached.get("result", {}).get("status") == "ok":
                return cached["result"]
        except (OSError, ValueError, TypeError):
            pass
        result = client.resolve(sample, now)
        if result.get("status") == "ok":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="amap-", suffix=".tmp", delete=False) as output:
                temp = Path(output.name)
                json.dump({"sample": sample_key, "result": result}, output, ensure_ascii=False)
            try:
                os.replace(temp, self.path)
            finally:
                temp.unlink(missing_ok=True)
        return result
