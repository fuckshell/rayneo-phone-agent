"""Deterministic Android alarm helper used by the PC-side acceptance test.

This is intentionally separate from UI skills: Android exposes SET_ALARM as a
public intent, so no OCR, tapping, or model call is needed.
"""
from __future__ import annotations

import datetime as _dt
import re
import subprocess
import time

from .model import SkillError, require


_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_ALARM_DELETE_RE = re.compile(
    r"(?:删除|删掉|取消|移除|清除|撤销|关掉|关闭|不要)"
    r"[^。！？!?]{0,32}(?:闹钟|提醒|叫醒|起床)|"
    r"(?:闹钟|提醒|叫醒|起床)[^。！？!?]{0,32}(?:删除|删掉|取消|移除|清除|撤销|关掉|关闭|不要)"
)


def _parse_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if "十" not in value:
        return _CN_DIGITS[value]
    tens, _, ones = value.partition("十")
    return (_CN_DIGITS[tens] if tens else 1) * 10 + (_CN_DIGITS[ones] if ones else 0)


def is_alarm_delete_request(text: str) -> bool:
    """Recognize an alarm mutation that removes an existing alarm.

    This is an intent guard, not an app-specific command list. It runs before
    time parsing so a sentence such as "删除早上6点那个闹钟" can never become
    a SET_ALARM request merely because it contains a valid time.
    """
    value = " ".join(str(text or "").strip().replace("點", "点").split())
    return bool(_ALARM_DELETE_RE.search(value))


def parse_alarm_text(text: str, now: _dt.datetime | None = None) -> dict:
    """Parse only unambiguous Chinese alarm requests; never guess a time."""
    now = now or _dt.datetime.now()
    value = " ".join(text.strip().replace("點", "点").split())
    if is_alarm_delete_request(value):
        # Preserve an explicit time when present so the phone can select the
        # matching row; omitted/ambiguous times require contextual resolution.
        target = {"status": "delete_requested", "operation": "delete", "llm_calls": 0}
        match = re.search(
            r"(?:(明天|今天)\s*)?(早上|上午|中午|下午|晚上|凌晨)?\s*"
            r"(\d{1,2}|[零〇一二两三四五六七八九十]{1,3})\s*点\s*"
            r"(?:半|([0-5]?\d|[零〇一二两三四五六七八九十]{1,3})\s*分?)?", value
        )
        if match:
            day, period, hour, minute = match.groups()
            hour = _parse_number(hour)
            minute = 30 if "半" in match.group(0) else (_parse_number(minute) if minute else 0)
            ambiguous_period = period is None and 1 <= hour <= 12 or period == "上午" and hour == 12
            if period in {"下午", "晚上"} and 1 <= hour <= 11:
                hour += 12
            elif period in {"早上", "凌晨", "晚上"} and hour == 12:
                hour = 0
            elif period == "中午" and 1 <= hour <= 11:
                hour += 12
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                if ambiguous_period:
                    target.update({"candidate_hours": [hour % 12, hour % 12 + 12],
                                   "minute": minute, "needs_time_resolution": True})
                else:
                    target.update({"hour": hour, "minute": minute, "day": day or "any"})
        if "刚刚" in value or "最近" in value or "刚" in value:
            target["target"] = "latest"
        else:
            target.setdefault("target", "matching_time" if match else "unspecified")
        return target
    alarm_cue = re.search(r"闹钟|叫我|喊我|叫醒|唤醒|起床", value, re.IGNORECASE)
    asr_dropped_wake_cue = re.fullmatch(
        r"(?:(?:明天|今天)\s*)?(?:早上|上午|凌晨)\s*(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})\s*点\s*(?:半|(?:[0-5]?\d|[零〇一二两三四五六七八九十]{1,3})\s*分?)?",
        value,
    )
    if not alarm_cue and not asr_dropped_wake_cue:
        return {"status": "ambiguous", "reason": "explicit alarm intent required", "llm_calls": 0}
    relative = re.search(
        r"(半|\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百]+)\s*"
        r"(个?小时|钟头|分钟)\s*(?:后|之后|以后)", value
    )
    if relative:
        number_text, unit = relative.groups()
        try:
            if number_text == "半":
                amount = 0.5
            elif re.fullmatch(r"\d+(?:\.\d+)?", number_text):
                amount = float(number_text)
            else:
                amount = float(_parse_cn_quantity(number_text))
        except (KeyError, ValueError):
            return {"status": "ambiguous", "reason": "invalid relative duration", "llm_calls": 0}
        minutes = round(amount * 60) if unit in {"个小时", "小时", "钟头"} else round(amount)
        if minutes < 1 or minutes > 24 * 60:
            return {"status": "ambiguous", "reason": "relative duration out of range", "llm_calls": 0}
        target = now + _dt.timedelta(minutes=minutes)
        if target.second or target.microsecond:
            target = target.replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)
        return {"status": "ready", "day": "today" if target.date() == now.date() else "tomorrow",
                "hour": target.hour, "minute": target.minute, "label": "phone-skills",
                "relative_minutes": minutes, "target_iso": target.isoformat(timespec="minutes"),
                "llm_calls": 0}
    # Accept common speech-recognition output such as “早上七点喊我”.
    match = re.search(r"(?:(明天|今天)\s*)?(早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2}|[零〇一二两三四五六七八九十]{1,3})\s*点\s*(?:半|([0-5]?\d|[零〇一二两三四五六七八九十]{1,3})\s*分?)?", value)
    if not match:
        return {"status": "ambiguous", "reason": "explicit hour and minute required", "llm_calls": 0}
    day, period, hour, minute = match.groups()
    if "半" in match.group(0):
        minute = "30"
    hour = _parse_number(hour)
    minute = _parse_number(minute) if minute else 0
    if minute > 59:
        return {"status": "ambiguous", "reason": "minute out of range", "llm_calls": 0}
    if period is None and hour <= 12:
        return {"status": "ambiguous", "reason": "morning or evening not specified", "llm_calls": 0}
    if period == "上午" and hour == 12:
        return {"status": "ambiguous", "reason": "noon or midnight not specified", "llm_calls": 0}
    if period in {"早上", "上午", "凌晨", "下午", "晚上"} and hour > 12:
        return {"status": "ambiguous", "reason": "hour out of range", "llm_calls": 0}
    if period in {"下午", "晚上"} and 1 <= hour <= 11:
        hour += 12
    elif period in {"早上", "凌晨", "晚上"} and hour == 12:
        hour = 0
    elif period == "中午" and 1 <= hour <= 11:
        hour += 12
    if hour > 23:
        return {"status": "ambiguous", "reason": "hour out of range", "llm_calls": 0}
    if day == "今天" and now is not None:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            return {"status": "ambiguous", "reason": "explicit today time is in the past", "llm_calls": 0}
    if day is None and now is not None:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        day = "today" if candidate > now else "tomorrow"
    return {"status": "ready", "day": day or "today", "hour": hour, "minute": minute,
            "label": "phone-skills", "llm_calls": 0}


def _parse_cn_quantity(value: str) -> int:
    """Parse the small positive Chinese quantities used by relative alarms."""
    if value in _CN_DIGITS:
        return _CN_DIGITS[value]
    if value == "百":
        return 100
    if "百" in value:
        hundreds, _, rest = value.partition("百")
        result = (_CN_DIGITS[hundreds] if hundreds else 1) * 100
        return result + (_parse_number(rest) if rest else 0)
    return _parse_number(value)


class AdbAlarm:
    """Minimal SET_ALARM dispatcher with a read-only dumpsys receipt."""
    def __init__(self, serial: str, timeout: float = 15):
        require(re.fullmatch(r"[A-Za-z0-9._:-]+", serial) is not None, "invalid ADB serial")
        self.serial, self.timeout = serial, timeout

    def _shell(self, *args) -> str:
        return subprocess.run(["adb", "-s", self.serial, "shell", *args], check=True,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=self.timeout).stdout

    def set(self, hour: int, minute: int, label: str = "phone-skills") -> dict:
        require(0 <= hour <= 23 and 0 <= minute <= 59, "invalid alarm time")
        require(re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", label) is not None, "invalid alarm label")
        self._shell("am", "start", "-a", "android.intent.action.SET_ALARM",
                     "--ei", "android.intent.extra.alarm.HOUR", str(hour),
                     "--ei", "android.intent.extra.alarm.MINUTES", str(minute),
                     "--es", "android.intent.extra.alarm.MESSAGE", label,
                     "--ez", "android.intent.extra.alarm.SKIP_UI", "true")
        marker = f"{hour:02d}:{minute:02d}:00.000"
        dump = ""
        for _ in range(10):
            dump = self._shell("dumpsys", "alarm")
            if marker in dump and "com.motorola.cn.deskclock" in dump:
                break
            time.sleep(.2)
        return {"status": "succeeded" if marker in dump and "com.motorola.cn.deskclock" in dump else "outcome_unknown",
                "hour": hour, "minute": minute, "label": label, "verification": "dumpsys_alarm",
                "llm_calls": 0}
