"""Read validated COROS main-sleep durations without retaining MCP payloads."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable


REFRESH_INTERVAL = timedelta(hours=6)
MAX_CACHE_AGE = timedelta(hours=24)
COROS_QUERY_TIMEOUT_SECONDS = 45
DAY_LINE = re.compile(r"(?m)^(\d{4}-\d{2}-\d{2})\s*$")
MAIN_SLEEP = re.compile(r"(?m)^Main Sleep \(asleep\): (?:(\d+)h )?(\d+)min\s*$")


def query_coros() -> str:
    executable = shutil.which("coros-mcp.cmd" if os.name == "nt" else "coros-mcp")
    if executable:
        command = [executable]
    else:
        bundled = Path(__file__).resolve().parents[1] / ".coros-cli" / "node_modules" / "coros-mcp" / "dist" / "cli.js"
        node = shutil.which("node")
        if not node or not bundled.is_file():
            raise RuntimeError("COROS MCP CLI unavailable")
        command = [node, str(bundled)]
    completed = subprocess.run(
        [*command, "call-tool", "--tool", "querySleepOverview", "--arguments-json", "{}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=COROS_QUERY_TIMEOUT_SECONDS, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError("COROS sleep query failed")
    return completed.stdout


def parse_sleep_overview(raw: str, now: datetime) -> list[dict]:
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("isError") is not False:
            return []
        text = next(item["text"] for item in payload["content"] if item.get("type") == "text")
        if isinstance(text, str) and text.startswith('"'):
            text = json.loads(text)
        if not isinstance(text, str):
            return []
    except (ValueError, KeyError, TypeError, StopIteration):
        return []
    markers = list(DAY_LINE.finditer(text))
    records = []
    for index, marker in enumerate(markers):
        try:
            wake_day = date.fromisoformat(marker.group(1))
        except ValueError:
            continue
        if not timedelta(0) <= now.date() - wake_day <= timedelta(days=7):
            continue
        section = text[marker.end():markers[index + 1].start() if index + 1 < len(markers) else len(text)]
        if "not available yet" in section or "Sleep Score: 0" in section:
            continue
        match = MAIN_SLEEP.search(section)
        if not match:
            continue
        minutes = int(match.group(1) or 0) * 60 + int(match.group(2))
        if 30 <= minutes <= 960 and int(match.group(2)) < 60:
            records.append({"wake_date": wake_day.isoformat(), "main_sleep_minutes": minutes})
    return sorted({item["wake_date"]: item for item in records}.values(),
                  key=lambda item: item["wake_date"])


class CorosSleepCache:
    def __init__(self, path: Path, query: Callable[[], str] = query_coros):
        self.path = Path(path)
        self.query = query
        self._lock = threading.Lock()
        self._refreshing = False
        self._last_attempt: datetime | None = None

    def get(self, now: datetime) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            queried_at = datetime.fromisoformat(data["queried_at"])
            if (data.get("schema") != 1 or queried_at.tzinfo is None
                    or not timedelta(0) <= now - queried_at <= MAX_CACHE_AGE):
                raise ValueError("stale cache")
            records = [item for item in data["records"]
                       if isinstance(item, dict) and isinstance(item.get("main_sleep_minutes"), int)
                       and 30 <= item["main_sleep_minutes"] <= 960
                       and date.fromisoformat(item["wake_date"]) <= now.date()]
            if records:
                return {"status": "records_ok", "queried_at": queried_at.isoformat(),
                        "records": records[-7:]}
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            pass
        return {"status": "unavailable"}

    def refresh(self, now: datetime) -> None:
        with self._lock:
            self._last_attempt = now
        try:
            records = parse_sleep_overview(self.query(), now)
            if not records:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="coros-sleep-", suffix=".tmp", delete=False) as output:
                temp_path = Path(output.name)
                json.dump({"schema": 1, "queried_at": now.isoformat(), "records": records}, output)
            try:
                os.replace(temp_path, self.path)
            finally:
                temp_path.unlink(missing_ok=True)
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError):
            pass

    def maybe_refresh(self, now: datetime) -> None:
        with self._lock:
            last = self._last_attempt
            if last is None:
                try:
                    cached = json.loads(self.path.read_text(encoding="utf-8"))
                    last = datetime.fromisoformat(cached["queried_at"])
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            if (self._refreshing or (last is not None and last.tzinfo is not None
                                     and timedelta(0) <= now - last < REFRESH_INTERVAL)):
                return
            self._refreshing = True

        def run() -> None:
            try:
                self.refresh(now)
            finally:
                with self._lock:
                    self._refreshing = False

        threading.Thread(target=run, name="coros-sleep-refresh", daemon=True).start()
