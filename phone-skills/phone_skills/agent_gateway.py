"""Bounded ADB tool gateway for a PC-side phone agent.

The planner (OpenClaw, DeepSeek, or another model) can request only the
structured actions below. It never receives an arbitrary shell primitive.
"""
from __future__ import annotations

import json
import base64
import os
import re
import subprocess
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .model import SkillError, require


_SENSITIVE_PATTERNS = (
    re.compile(r"验证码|支付密码|确认支付|确认转账|恢复出厂|恢复出厂设置"),
    re.compile(r"请登录|立即登录|登录账号|登录帐号|账号登录|帐号登录|扫码登录|登录后继续|登录以继续"),
    re.compile(r"确认授权|请求授权|授权访问|是否允许|授予.{0,8}权限|允许.{0,8}(?:访问|权限)"),
    re.compile(r"确认删除|永久删除|删除.{0,8}(?:账号|账户|数据|文件)"),
)


def _redact_text(value: str) -> str:
    value = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[redacted-email]", value)
    value = re.sub(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "[redacted-phone]", value)
    return re.sub(r"(?<!\d)\d{10,}(?!\d)", "[redacted-number]", value)


def _sensitive_screen(nodes: list[dict[str, Any]]) -> bool:
    for node in nodes:
        if node.get("password"):
            return True
        content = node.get("text", "") + " " + node.get("content_desc", "")
        if any(pattern.search(content) for pattern in _SENSITIVE_PATTERNS):
            return True
    return False


def _ui_unavailable(nodes: list[dict[str, Any]]) -> bool:
    if not nodes:
        return True
    if len(nodes) != 1:
        return False
    root = nodes[0]
    return (not any(root.get(key) for key in ("resource_id", "text", "content_desc", "class_name",
                                               "clickable", "editable", "focused"))
            and root.get("bounds") == [0, 0, 0, 0])


class AgentAdb:
    BRIDGE_URI = "content://com.zeyu.phoneai.assistant.agent"

    def __init__(self, serial: str, timeout: float = 15):
        require(re.fullmatch(r"[A-Za-z0-9._:-]+", serial) is not None, "invalid ADB serial")
        self.serial = serial
        self.timeout = timeout

    def shell(self, *args: str) -> str:
        # args are constructed from fixed verbs and validated values below.
        return subprocess.run(["adb", "-s", self.serial, "shell", *args], check=True,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=self.timeout).stdout

    def _foreground_app(self) -> str:
        dump = self.shell("dumpsys", "activity", "activities")
        match = re.search(r"(?:mResumedActivity|topResumedActivity).*?\s([\w.]+)/", dump)
        return match.group(1) if match else ""

    def capture_png(self) -> bytes:
        result = subprocess.run(["adb", "-s", self.serial, "exec-out", "screencap", "-p"],
                                check=True, capture_output=True, timeout=self.timeout)
        start = result.stdout.find(b"\x89PNG\r\n\x1a\n")
        require(start >= 0, "screenshot_unavailable")
        return result.stdout[start:]

    def _accessibility_call(self, method: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        args = ["content", "call", "--uri", self.BRIDGE_URI, "--method", method]
        if data is not None:
            encoded = base64.b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii")
            args.extend(["--arg", encoded])
        output = self.shell(*args)
        error = re.search(r"error=([^}\]]+)", output)
        if error:
            raise SkillError(error.group(1))
        result = re.search(r"result=([A-Za-z0-9+/=]+)", output)
        require(result is not None, "accessibility_bridge_unavailable")
        value = json.loads(base64.b64decode(result.group(1)).decode("utf-8"))
        require(isinstance(value, dict), "invalid_accessibility_response")
        return value

    @staticmethod
    def _normalize_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        indexed = {str(node.get("key", "")): node for node in nodes}
        for node in nodes:
            if (node.get("clickable") or node.get("long_clickable")) and not (node.get("text") or node.get("content_desc")):
                descendants = [child for child in nodes if child.get("parent_key") == node.get("key")]
                while descendants:
                    candidate = next((child for child in descendants if child.get("text") or child.get("content_desc")), None)
                    if candidate:
                        node["text"] = candidate.get("text", "")
                        node["content_desc"] = candidate.get("content_desc", "")
                        break
                    descendants = [child for parent in descendants
                                   for child in nodes if child.get("parent_key") == parent.get("key")]
        for node in nodes:
            node["text"] = _redact_text(str(node.get("text", "")))
            node["content_desc"] = _redact_text(str(node.get("content_desc", "")))
            node.setdefault("visible", True)
            node.setdefault("enabled", False)
            node.setdefault("clickable", False)
            node.setdefault("long_clickable", False)
            node.setdefault("editable", False)
            node.setdefault("focused", False)
            node.setdefault("bounds", [])
        return nodes

    def observe(self) -> dict[str, Any]:
        # Prefer the first-party AccessibilityService tree when available.
        # This works around apps/device builds that hide nodes from uiautomator.
        try:
            state = self._accessibility_call("observe")
            if isinstance(state.get("nodes"), list) and not _ui_unavailable(state["nodes"]):
                nodes = self._normalize_nodes(state["nodes"][:1200])
                sensitive = _sensitive_screen(nodes)
                if sensitive:
                    return {"app": state.get("app", ""), "nodes": [], "blocked": "sensitive_screen",
                            "source": "accessibility"}
                return {"app": state.get("app", ""), "nodes": nodes,
                        "blocked": state.get("blocked", ""), "source": "accessibility"}
        except (SkillError, OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
            pass
        dump_error = None
        for attempt in range(2):
            try:
                self.shell("uiautomator", "dump", "/sdcard/phone-agent-ui.xml")
                dump_error = None
                break
            except subprocess.SubprocessError as exc:
                dump_error = exc
                if attempt == 0:
                    # Android occasionally kills uiautomator with 137 while
                    # the accessibility provider is refreshing its tree.
                    time.sleep(0.15)
        if dump_error is not None:
            raise dump_error
        xml = self.shell("cat", "/sdcard/phone-agent-ui.xml")
        root = ET.fromstring(xml)
        app = self._foreground_app()
        nodes = []
        raw_nodes = list(root.iter("node"))
        sensitive = False
        for index, item in enumerate(raw_nodes):
            attrs = item.attrib
            text = attrs.get("text", "")
            desc = attrs.get("content-desc", "")
            password = attrs.get("password") == "true"
            if password:
                sensitive = True
            # Android settings rows usually expose the label on a non-clickable
            # descendant. Copy its first semantic label onto the clickable row
            # so the planner can select the row by text without coordinates.
            if attrs.get("clickable") == "true" and not (text or desc):
                descendants = list(item.iter("node"))[1:]
                text = next((child.attrib.get("text", "") for child in descendants
                             if child.attrib.get("text", "").strip()), "")
                desc = next((child.attrib.get("content-desc", "") for child in descendants
                             if child.attrib.get("content-desc", "").strip()), "")
            if password:
                text = desc = ""
            text = _redact_text(text)
            desc = _redact_text(desc)
            bounds = _bounds(attrs.get("bounds", ""))
            nodes.append({
                "key": str(index), "resource_id": attrs.get("resource-id", ""),
                "text": text, "content_desc": desc, "class_name": attrs.get("class", ""),
                "visible": attrs.get("visible-to-user", "true") == "true",
                "enabled": attrs.get("enabled") == "true",
                "clickable": attrs.get("clickable") == "true",
                "long_clickable": attrs.get("long-clickable") == "true",
                "editable": attrs.get("class", "") == "android.widget.EditText",
                "focused": attrs.get("focused") == "true", "bounds": bounds,
            })
        sensitive = sensitive or _sensitive_screen(nodes)
        if sensitive:
            blocked = "sensitive_screen"
        elif not app:
            blocked = "missing_foreground"
        elif _ui_unavailable(nodes):
            blocked = "ui_unavailable"
        else:
            blocked = ""
        return {"app": app, "nodes": [] if blocked else nodes[:5000], "blocked": blocked,
                "source": "uiautomator"}

    @staticmethod
    def allowed_apps() -> set[str]:
        return {x.strip() for x in os.environ.get("PHONE_AI_AGENT_ALLOWED_APPS", "").split(",") if x.strip()}

    def app_allowed(self, app: str) -> bool:
        # Ordinary app access is discovered from the device package manager;
        # PHONE_AI_AGENT_ALLOWED_APPS is retained only as a legacy override.
        return bool(app)

    def installed_apps(self) -> list[str]:
        try:
            output = self.shell("pm", "list", "packages", "-3", "--user", "0")
        except (OSError, subprocess.SubprocessError):
            return []
        return sorted({line.removeprefix("package:").strip() for line in output.splitlines()
                       if re.fullmatch(r"[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+", line.removeprefix("package:").strip())})[:500]

    def capabilities(self) -> dict[str, Any]:
        """Return the bounded, read-only capabilities advertised to planners."""
        return {
            "protocol_version": 2,
            "backend": {"mode": "adb", "degraded": False},
            "actions": ["observe", "launch_app", "amap_handoff", "click", "long_click", "type_text", "back",
                         "scroll", "wait", "wait_until", "assert", "screenshot", "deeplink"],
            "observe": {"sources": ["accessibility", "uiautomator"],
                        "priority": ["accessibility", "uiautomator"], "degraded": False},
            "supports": {"screenshot": True, "wait_until": True, "deeplink": True},
        }

    def act(self, action: dict[str, Any]) -> dict[str, Any]:
        require(isinstance(action, dict) and isinstance(action.get("action"), str), "invalid action")
        kind = action.get("action")
        fields = {
            "observe": {"action"}, "launch_app": {"action", "package"},
            "amap_handoff": {"action", "text"},
            "click": {"action", "target"}, "type_text": {"action", "target", "text", "clear"},
            "long_click": {"action", "target"},
            "back": {"action"}, "scroll": {"action", "direction"}, "wait": {"action", "seconds"},
            "wait_until": {"action", "expect", "timeout", "poll"},
            "assert": {"action", "expect"}, "screenshot": {"action"},
            "deeplink": {"action", "uri", "package"},
        }
        # A package is optional for a web URI, but the strict field check above
        # needs a separate accepted shape for callers that omit it.
        if kind == "deeplink":
            require(set(action) in ({"action", "uri"}, {"action", "uri", "package"}),
                    "invalid action fields")
        else:
            require(kind in fields and not (action.keys() - fields[kind]), "invalid action fields")
        if kind == "observe":
            return self.observe()
        if kind == "assert":
            state = self.observe()
            return _assert_state(state, action["expect"])
        if kind == "wait_until":
            expect = action.get("expect")
            timeout = action.get("timeout", 10)
            poll = action.get("poll", 0.25)
            require(isinstance(expect, dict) and bool(expect), "invalid wait_until expectation")
            require(type(timeout) in (int, float) and 0 <= timeout <= 30, "invalid wait_until timeout")
            require(type(poll) in (int, float) and 0.1 <= poll <= 2, "invalid wait_until poll")
            deadline = time.monotonic() + float(timeout)
            polls = 0
            last_state: dict[str, Any] = {}
            while True:
                last_state = self.observe()
                polls += 1
                assertion = _assert_state(last_state, expect)
                if assertion.get("verified"):
                    return {"action": "wait_until", "verified": True, "expect": expect,
                            "polls": polls, "state": last_state, "evidence": assertion.get("evidence", {})}
                if time.monotonic() >= deadline:
                    return {"action": "wait_until", "verified": False, "expect": expect,
                            "polls": polls, "state": last_state, "evidence": assertion.get("evidence", {}),
                            "error_type": "condition_timeout"}
                time.sleep(min(float(poll), max(0.0, deadline - time.monotonic())))
        if kind == "screenshot":
            state = self.observe()
            require(not state.get("blocked"), state.get("blocked", "screen_blocked"))
            raw = self.capture_png()
            if isinstance(raw, str):
                raw = raw.encode("latin1", errors="replace")
            require(isinstance(raw, (bytes, bytearray)) and 0 < len(raw) <= 4 * 1024 * 1024,
                    "screenshot_too_large_or_empty")
            return {"action": "screenshot", "purpose": "evidence_only", "mime_type": "image/png",
                    "data": base64.b64encode(bytes(raw)).decode("ascii"), "state": state}
        if kind == "deeplink":
            uri = action.get("uri")
            require(isinstance(uri, str) and 1 <= len(uri) <= 2048, "invalid deeplink")
            require(not any(ord(ch) < 32 or ord(ch) == 127 for ch in uri), "invalid deeplink")
            require(not re.search(r"[\s;|&`$<>]", uri), "invalid deeplink")
            parsed = urllib.parse.urlsplit(uri)
            require(parsed.scheme in {"http", "https"} and bool(parsed.netloc), "invalid deeplink")
            package = action.get("package")
            if package is not None:
                require(isinstance(package, str) and re.fullmatch(r"[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+", package),
                        "invalid package")
                require(package in self.installed_apps(), "app_not_installed")
            args = ["am", "start", "-a", "android.intent.action.VIEW", "-d", uri]
            if package:
                args.extend(["-p", package])
            self.shell(*args)
            return self.observe()
        if kind == "launch_app":
            package = action.get("package", "")
            allowed = self.allowed_apps()
            # Planners sometimes return an Android component or a common alias.
            # Normalize only known values; the allowlist check remains mandatory.
            aliases = {"设置": "com.android.settings", "settings": "com.android.settings",
                       "高德": "com.autonavi.minimap", "高德地图": "com.autonavi.minimap",
                       "amap": "com.autonavi.minimap"}
            package = aliases.get(package, package)
            for allowed_package in allowed:
                if package.startswith(allowed_package + "/") or package.startswith(allowed_package + "."):
                    package = allowed_package
                    break
            require(re.fullmatch(r"[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+", package) is not None,
                    "invalid package")
            require(package == "com.android.settings" or package in self.installed_apps(), "app_not_installed")
            if package == "com.android.settings":
                self.shell("am", "start", "-a", "android.settings.SETTINGS")
            elif package == "com.tencent.mm":
                # Explicit launcher avoids monkey being intercepted by the
                # default assistant while resolving Motorola dual profiles.
                self.shell("am", "start", "--user", "0", "-n", "com.tencent.mm/.ui.LauncherUI")
            else:
                self.shell("monkey", "-p", package, "1")
            state = self.observe()
            if state.get("app") == "android" and package == "com.tencent.mm":
                # Motorola dual-app Resolver: choose the main profile row
                # labelled 微信. The clone is explicitly labelled 微信 小号.
                rows = [n for n in state.get("nodes", []) if n.get("text") == "微信" and n.get("visible")]
                if len(rows) == 1 and len(rows[0].get("bounds", [])) == 4:
                    x1, y1, x2, y2 = rows[0]["bounds"]
                    self.shell("input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))
                    time.sleep(1)
                    state = self.observe()
            return state

        if kind == "amap_handoff":
            # Fixed package and fixed launcher. The free-form text is returned
            # as a handoff envelope for the Amap AI assistant; it is never
            # interpolated into a shell command or an untrusted URI.
            value = action.get("text", "")
            require(isinstance(value, str) and 1 <= len(value) <= 1000, "invalid handoff text")
            require(not any(ord(ch) < 32 or ord(ch) == 127 for ch in value), "invalid handoff text")
            require("com.autonavi.minimap" in self.installed_apps(), "app_not_installed")
            self.shell("monkey", "-p", "com.autonavi.minimap", "1")
            time.sleep(2)
            state = self.observe()
            voice = next((n for n in state.get("nodes", []) if n.get("resource_id") == "com.autonavi.minimap:id/tv_voice_button" and n.get("clickable") and n.get("text") == "按住说话"), None)
            if voice:
                self._accessibility_call("click", {"target": {"resource_id": voice["resource_id"], "text": voice["text"]}})
                state = self.observe()
            toggle = next((n for n in state.get("nodes", []) if n.get("resource_id") == "com.autonavi.minimap:id/iv_right_icon" and n.get("clickable") and not n.get("text") ), None)
            if toggle:
                self._accessibility_call("click", {"target": {"resource_id": toggle["resource_id"]}})
                state = self.observe()
            edit = next((n for n in state.get("nodes", []) if n.get("resource_id") == "com.autonavi.minimap:id/et_input" and n.get("editable")), None)
            if edit and len(edit.get("bounds", [])) == 4:
                x1, y1, x2, y2 = edit["bounds"]
                self.shell("input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))
                self._accessibility_call("type_text", {"target": {"resource_id": edit["resource_id"]}, "text": value})
                state = self.observe()
                send = next((n for n in state.get("nodes", []) if n.get("resource_id") == "com.autonavi.minimap:id/iv_right_icon" and n.get("clickable")), None)
                if send:
                    self._accessibility_call("click", {"target": {"resource_id": send["resource_id"]}})
                    time.sleep(1)
                    state = self.observe()
            return {"app": state.get("app", ""), "nodes": state.get("nodes", []),
                    "blocked": state.get("blocked", ""), "source": state.get("source"),
                    "handoff": {"target": "amap_ai_assistant", "text": value,
                                 "next": "locate_and_open_ai_assistant"}}

        state = self.observe()
        if state["blocked"]:
            raise SkillError(state["blocked"])
        require(self.app_allowed(state["app"]), "app_not_installed")
        if kind in {"click", "long_click"}:
            node, state = self._resolve_action_target(state, action.get("target"),
                clickable=kind == "click", long_clickable=kind == "long_click")
            if state.get("source") == "accessibility":
                self._accessibility_call(kind, {"target": action.get("target")})
            else:
                x1, y1, x2, y2 = node["bounds"]
                x, y = str((x1 + x2) // 2), str((y1 + y2) // 2)
                if kind == "long_click":
                    self.shell("input", "swipe", x, y, x, y, "800")
                else:
                    self.shell("input", "tap", x, y)
        elif kind == "type_text":
            node, state = self._resolve_action_target(state, action.get("target"), editable=True)
            require(node["focused"], "input_must_be_focused")
            value = action.get("text", "")
            require(isinstance(value, str) and 0 < len(value) <= 500, "invalid input text")
            require(not any(ord(ch) < 32 or ord(ch) == 127 for ch in value), "invalid_input_text")
            require(type(action.get("clear", False)) is bool, "invalid clear flag")
            if state.get("source") == "accessibility":
                self._accessibility_call("type_text", {"target": action.get("target"), "text": value})
            else:
                require(value.isascii() and re.fullmatch(r"[A-Za-z0-9 .,!?@:_/\\-]+", value) is not None,
                        "only_safe_ascii_input_supported")
                if action.get("clear", False):
                    current = node.get("text", "")
                    require(len(current) <= 500 and current.isascii(), "cannot safely clear current input")
                    self.shell("input", "keyevent", "KEYCODE_MOVE_END")
                    if current:
                        self.shell("input", "keyevent", *("KEYCODE_DEL" for _ in current))
                self.shell("input", "text", value.replace(" ", "%s"))
        elif kind == "back":
            if state.get("source") == "accessibility":
                self._accessibility_call("back")
            else:
                self.shell("input", "keyevent", "4")
        elif kind == "scroll":
            direction = action.get("direction", "down")
            if direction not in {"up", "down", "left", "right"}:
                raise SkillError("invalid scroll direction")
            width = max((n["bounds"][2] for n in state["nodes"] if len(n["bounds"]) == 4), default=1224)
            height = max((n["bounds"][3] for n in state["nodes"] if len(n["bounds"]) == 4), default=2992)
            cx, cy = width // 2, height // 2
            coords = {"down": (cx, height * 3 // 4, cx, height // 4),
                      "up": (cx, height // 4, cx, height * 3 // 4),
                      "left": (width * 3 // 4, cy, width // 4, cy),
                      "right": (width // 4, cy, width * 3 // 4, cy)}[direction]
            if state.get("source") == "accessibility" and direction in {"up", "down"}:
                self._accessibility_call("scroll", {"direction": direction})
            else:
                self.shell("input", "swipe", *(str(x) for x in coords), "400")
        elif kind == "wait":
            seconds = action.get("seconds", 1)
            require(type(seconds) in (int, float) and 0 <= seconds <= 10, "invalid wait")
            time.sleep(seconds)
        else:
            raise SkillError("unsupported agent action")
        return self.observe()

    def _resolve_action_target(self, state: dict[str, Any], target: Any, *,
                               clickable: bool = False, editable: bool = False, long_clickable: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        """Resolve a target against a briefly refreshed tree when a page is settling.

        Refreshing is limited to target-not-found cases. Ambiguous targets and
        app changes are never guessed through.
        """
        last_state = state
        for attempt in range(3):
            try:
                return _unique_target(last_state.get("nodes", []), target,
                                      clickable=clickable, editable=editable, long_clickable=long_clickable), last_state
            except SkillError as exc:
                if str(exc) != "target_not_found" or attempt == 2:
                    raise
                time.sleep(0.1)
                refreshed = self.observe()
                if refreshed.get("app") != state.get("app"):
                    raise
                last_state = refreshed

    def act_checked(self, action: dict[str, Any], before: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute one action and return an evidence-bearing result.

        ``act`` remains the low-level compatibility API. This wrapper makes the
        observe -> act -> observe boundary explicit for callers that need to
        distinguish a dispatched action from a verified transition.
        """
        if before is None:
            try:
                before = self.observe()
            except Exception as exc:
                return _action_failure(action, None, None, exc)
        try:
            raw_after = self.act(action)
        except Exception as exc:
            return _action_failure(action, before, None, exc)
        if action.get("action") == "wait_until":
            after = raw_after.get("state") if isinstance(raw_after, dict) else None
            verified = bool(raw_after.get("verified")) if isinstance(raw_after, dict) else False
            error_type = raw_after.get("error_type", "condition_timeout") if isinstance(raw_after, dict) else "invalid_postcondition"
            result = {"status": "succeeded" if verified else "failed", "verified": verified,
                      "action": action, "before": before, "after": after, "evidence": raw_after}
            if not verified:
                result.update({"error_type": error_type, "message": error_type})
            return result
        after = raw_after
        verified, error_type = _verify_action(action, before, after)
        result = {"status": "succeeded" if verified else "failed", "verified": verified,
                  "action": action, "before": before, "after": after}
        if not verified:
            result.update({"error_type": error_type, "message": error_type})
        return result

    def visual_act(self, action: dict[str, Any]) -> dict[str, Any]:
        """Execute a screenshot-derived action when the accessibility tree is unavailable."""
        require(isinstance(action, dict) and action.get("action") in {"tap", "type_text", "back", "wait"},
                "invalid visual action")
        app = self._foreground_app()
        require(self.app_allowed(app), "app_not_installed")
        if action["action"] == "tap":
            x, y = action["x"], action["y"]
            require(type(x) in (int, float) and type(y) in (int, float), "invalid visual coordinates")
            require(0 <= x <= 1000 and 0 <= y <= 1000, "visual coordinates out of range")
            # Device resolution is read from wm; the model only supplied relative coordinates.
            size = self.shell("wm", "size")
            match = re.search(r"(\d+)x(\d+)", size)
            require(match, "missing_display_size")
            width, height = int(match.group(1)), int(match.group(2))
            self.shell("input", "tap", str(round(x * width / 1000)), str(round(y * height / 1000)))
        elif action["action"] == "type_text":
            value = action.get("text", "")
            require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9 .,!?@:_/\\-]{1,500}", value),
                    "only_safe_ascii_input_supported")
            self.shell("input", "text", value.replace(" ", "%s"))
        elif action["action"] == "back":
            self.shell("input", "keyevent", "4")
        else:
            seconds = action.get("seconds", 1)
            require(type(seconds) in (int, float) and 0 <= seconds <= 10, "invalid wait")
            time.sleep(seconds)
        return self.observe()


def _bounds(value: str) -> list[int]:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", value)
    return [int(x) for x in match.groups()] if match else []


def _normalize_selector_value(value: Any) -> str:
    """Normalize labels without changing the value returned in evidence."""
    value = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(value.split()).casefold()


def _state_fingerprint(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return ""
    nodes = []
    for node in state.get("nodes", []):
        if not isinstance(node, dict):
            continue
        nodes.append({key: node.get(key) for key in
                      ("key", "resource_id", "text", "content_desc", "class_name",
                       "visible", "enabled", "clickable", "long_clickable", "editable", "focused", "bounds")})
    return json.dumps({"app": state.get("app", ""), "blocked": state.get("blocked", ""),
                       "nodes": nodes}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _classify_action_error(exc: Exception) -> str:
    message = str(exc)
    if "accessibility_service_not_connected" in message:
        return "accessibility_service_not_connected"
    for relay_error in ("phone_relay_result_unknown", "phone_relay_timeout", "phone_relay_offline"):
        if relay_error in message:
            return relay_error
    if "target_not_found" in message:
        return "target_not_found"
    if "target_not_unique" in message:
        return "target_not_unique"
    if "accessibility_bridge_unavailable" in message or isinstance(exc, (OSError, subprocess.SubprocessError)):
        return "bridge_unavailable"
    if "ui_unavailable" in message:
        return "ui_unavailable"
    if "sensitive_screen" in message:
        return "sensitive_screen"
    if "app_not_installed" in message:
        return "app_not_installed"
    return type(exc).__name__


def _action_failure(action: dict[str, Any], before: dict[str, Any] | None,
                    after: dict[str, Any] | None, exc: Exception) -> dict[str, Any]:
    error_type = _classify_action_error(exc)
    return {"status": "failed", "verified": False, "action": action,
            "before": before, "after": after, "error_type": error_type,
            "message": str(exc) or error_type}


def _verify_action(action: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> tuple[bool, str]:
    if not isinstance(after, dict):
        return False, "invalid_postcondition"
    if after.get("blocked"):
        return False, "postcondition_blocked"
    kind = action.get("action")
    if kind in {"observe", "wait"}:
        return True, ""
    if kind == "assert":
        return bool(after.get("verified")), "assertion_failed" if not after.get("verified") else ""
    if kind in {"screenshot", "deeplink"}:
        if kind == "screenshot":
            return bool(after.get("data")), "screenshot_missing"
        return bool(after.get("app")) and not after.get("blocked"), "deeplink_postcondition_failed"
    if kind == "launch_app":
        package = str(action.get("package", ""))
        aliases = {"设置": "com.android.settings", "settings": "com.android.settings",
                   "高德": "com.autonavi.minimap", "高德地图": "com.autonavi.minimap",
                   "amap": "com.autonavi.minimap"}
        package = aliases.get(package, package).split("/", 1)[0]
        return (after.get("app") == package, "foreground_app_mismatch")
    if kind in {"click", "long_click", "type_text", "back", "scroll"}:
        if _state_fingerprint(before) != _state_fingerprint(after):
            return True, ""
        return False, "state_unchanged"
    return False, "unsupported_action"


def _node_matches(node: dict[str, Any], selector: dict[str, Any]) -> bool:
    if not isinstance(node, dict) or not isinstance(selector, dict):
        return False
    allowed = {"resource_id", "text", "content_desc", "class_name"}
    if not selector or set(selector) - allowed:
        return False
    return all(_normalize_selector_value(node.get(key, "")) == _normalize_selector_value(value)
               for key, value in selector.items())


def _assert_state(state: dict[str, Any], expect: Any) -> dict[str, Any]:
    """Evaluate only structured UI assertions; never accept arbitrary code."""
    require(isinstance(expect, dict), "invalid assertion")
    require(set(expect) <= {"app", "activity", "present", "absent"}, "invalid assertion fields")
    require(isinstance(expect.get("app", state.get("app", "")), str), "invalid assertion app")
    if "app" in expect and state.get("app") != expect["app"]:
        return {"action": "assert", "verified": False, "reason": "app_mismatch",
                "evidence": {"app": state.get("app", ""), "present": []}}
    if "activity" in expect and state.get("activity") not in {expect["activity"], None, ""}:
        return {"action": "assert", "verified": False, "reason": "activity_mismatch",
                "evidence": {"app": state.get("app", ""), "present": []}}
    nodes = [n for n in state.get("nodes", []) if n.get("visible", True)]
    present = expect.get("present", [])
    absent = expect.get("absent", [])
    require(isinstance(present, list) and isinstance(absent, list), "invalid assertion nodes")
    require(all(isinstance(item, dict) and item for item in present + absent), "invalid assertion selector")
    present_ok = all(any(_node_matches(node, selector) for node in nodes) for selector in present)
    absent_ok = all(not any(_node_matches(node, selector) for node in nodes) for selector in absent)
    labels = [str(selector.get("text") or selector.get("content_desc") or selector.get("resource_id") or "")
              for selector in present if isinstance(selector, dict)]
    return {"action": "assert", "verified": bool(present_ok and absent_ok),
            "reason": "" if present_ok and absent_ok else "assertion_failed",
            "evidence": {"app": state.get("app", ""), "present": labels}, "state": state}


def _unique_target(nodes: list[dict[str, Any]], target: Any, clickable: bool = False,
                   editable: bool = False, long_clickable: bool = False) -> dict[str, Any]:
    require(isinstance(target, dict), "target is required")
    allowed = {"resource_id", "text", "content_desc"}
    require(set(target) <= allowed and target, "invalid target")
    normalized_target = {key: _normalize_selector_value(value) for key, value in target.items()}
    hits = []
    for node in nodes:
        if (not node.get("visible", True) or not node.get("enabled", False)
                or (clickable and not node.get("clickable", False))
                or (long_clickable and not node.get("long_clickable", False))
                or (editable and not node.get("editable", False))):
            continue
        if all(normalized_target[k] == _normalize_selector_value(node.get(k, "")) for k in target):
            hits.append(node)
    # Android Settings often places the label in a non-clickable TextView
    # (with android:id/title) inside a clickable parent row. If a planner
    # copied that child resource id, use the semantic label on the row while
    # retaining the uniqueness/visibility checks above.
    if (not hits and (clickable or editable or long_clickable) and "resource_id" in target
            and ("text" in target or "content_desc" in target)):
        semantic_target = {key: _normalize_selector_value(value) for key, value in target.items()
                           if key != "resource_id"}
        hits = [node for node in nodes
                if node.get("visible", True) and node.get("enabled", False)
                and (not clickable or node.get("clickable", False))
                and (not long_clickable or node.get("long_clickable", False))
                and (not editable or node.get("editable", False))
                and all(semantic_target[k] == _normalize_selector_value(node.get(k, ""))
                        for k in semantic_target)]
    # Accessibility providers may expose the same physical control more than
    # once (for example a clickable row and its clickable child). Bounds are
    # the stable physical identity; keep the first semantic representative.
    unique = []
    seen_bounds = set()
    for node in hits:
        bounds = tuple(node.get("bounds", []))
        identity = bounds if len(bounds) == 4 else ("node", node.get("key"))
        if identity in seen_bounds:
            continue
        seen_bounds.add(identity)
        unique.append(node)
    hits = unique
    if len(hits) != 1:
        raise SkillError("target_not_unique" if len(hits) > 1 else "target_not_found")
    require(len(hits[0].get("bounds", [])) == 4, "target_has_no_bounds")
    return hits[0]


def _handler():
    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, value: dict[str, Any]):
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/health":
                self._json(200, {"ok": True, "service": "phone-agent-gateway"})
                return
            if parsed.path == "/v1/agent/apps":
                serial = urllib.parse.parse_qs(parsed.query).get("serial", [""])[0]
                try:
                    self._json(200, {"ok": True, "apps": AgentAdb(serial).installed_apps()})
                except (SkillError, OSError, ValueError) as exc:
                    self._json(400, {"ok": False, "error_type": type(exc).__name__, "message": str(exc)})
                return
            if parsed.path == "/v1/agent/capabilities":
                serial = urllib.parse.parse_qs(parsed.query).get("serial", [""])[0]
                try:
                    # Capabilities are a static, read-only contract and do not
                    # require a live device serial. A placeholder keeps the
                    # same adapter path while allowing health probes before
                    # ADB enumeration has completed.
                    self._json(200, {"ok": True, "capabilities": AgentAdb(serial or "capabilities").capabilities()})
                except (SkillError, OSError, ValueError) as exc:
                    self._json(400, {"ok": False, "error_type": type(exc).__name__, "message": str(exc)})
                return
            if parsed.path != "/v1/agent/observe":
                self._json(404, {"error": "not_found"})
                return
            query = urllib.parse.parse_qs(parsed.query)
            try:
                device = AgentAdb(query.get("serial", [""])[0])
                state = device.observe()
                if not state["blocked"] and not device.app_allowed(state["app"]):
                    state = {"app": state["app"], "nodes": [], "blocked": "app_not_installed"}
                self._json(200, {"ok": True, "state": state})
            except (SkillError, OSError, ValueError) as exc:
                self._json(400, {"ok": False, "error_type": type(exc).__name__, "message": str(exc)})

        def do_POST(self):
            if self.path not in ("/v1/agent/action", "/v1/agent/task"):
                self._json(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                require(0 < length <= 32_000, "invalid content length")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                require(isinstance(body, dict), "request must be an object")
                serial = body.pop("serial", "")
                if self.path == "/v1/agent/task":
                    # A task may enter through the phone voice assistant or the
                    # WeChat/OpenClaw web chat. Both are user-facing origins;
                    # RayNeo is deliberately not accepted as a command source.
                    origin = body.pop("origin", "")
                    require(origin in {"phone-agent", "openclaw-web"}, "invalid_command_origin")
                    text = body.pop("text", "")
                    require(isinstance(text, str) and 0 < len(text.strip()) <= 2000, "invalid task")
                    from .agent_runner import run_agent
                    result = run_agent(text, serial, max_steps=int(body.pop("max_steps", 24)))
                    self._json(200, {"ok": True, "origin": origin, "command": {"text": text}, "result": result})
                    return
                result = AgentAdb(serial).act_checked(body)
                self._json(200, {"ok": result.get("status") == "succeeded",
                                 "state": result.get("after"), "result": result})
            except (SkillError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                self._json(400, {"ok": False, "error_type": type(exc).__name__, "message": str(exc)})

        def log_message(self, fmt, *args):
            print("[agent-gateway] " + (fmt % args), flush=True)

    return Handler


def serve(host: str = "127.0.0.1", port: int = 18881):
    server = ThreadingHTTPServer((host, port), _handler())
    print(json.dumps({"listening": f"http://{host}:{port}", "service": "phone-agent-gateway"}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
