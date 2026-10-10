"""Bounded, at-most-once RPC over the phone's outbound bridge connection."""
from __future__ import annotations

import base64
import hmac
import json
import os
import threading
import time
import uuid
from pathlib import Path

from .agent_gateway import AgentAdb, _sensitive_screen
from .model import SkillError


def config_path() -> Path:
    return Path(os.environ.get("PHONE_AI_RELAY_CONFIG", str(
        Path(os.environ.get("LOCALAPPDATA", ".")) / "PhoneSkills" / "relay.json")))


class PhoneRelay:
    def __init__(self, config: dict):
        self.device_id = config["device_id"]
        self.token = config["token"]
        if len(self.token) < 32:
            raise ValueError("relay_token_too_short")
        self.condition = threading.Condition()
        self.commands: dict[str, dict] = {}
        self.last_seen = 0.0
        self.operation_lock = threading.Lock()

    def authorized(self, token: str) -> bool:
        return hmac.compare_digest(self.token, token)

    def status(self) -> dict:
        with self.condition:
            age = time.monotonic() - self.last_seen if self.last_seen else None
            return {"transport": "phone_outbound", "connected": age is not None and age < 45,
                    "last_seen_seconds": round(age, 1) if age is not None else None,
                    "pending": len(self.commands)}

    def poll(self, device_id: str, timeout: float = 20) -> dict:
        if device_id != self.device_id:
            raise ValueError("unknown_device")
        deadline = time.monotonic() + timeout
        with self.condition:
            self.last_seen = time.monotonic()
            self.condition.notify_all()
            while True:
                for command in self.commands.values():
                    if not command["dispatched"] and command["expires_at"] > time.time():
                        command["dispatched"] = True
                        return {"command": {key: command[key] for key in
                                ("id", "op", "payload", "expires_at")}}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.last_seen = time.monotonic()
                    return {"command": None}
                self.condition.wait(remaining)

    def complete(self, device_id: str, command_id: str, result: dict) -> dict:
        if device_id != self.device_id:
            raise ValueError("unknown_device")
        with self.condition:
            self.last_seen = time.monotonic()
            command = self.commands.get(command_id)
            if command is None or not command["dispatched"] or command["expires_at"] <= time.time():
                return {"accepted": False}
            if command["result"] is None:
                command["result"] = result
                self.condition.notify_all()
            return {"accepted": True}

    def request(self, op: str, payload: dict, timeout: float = 35) -> dict:
        # `power` is a fixed operation, not a general shell escape hatch. The
        # Android worker accepts only these three action names and maps each to
        # its own hard-coded root command.
        if op not in {"accessibility", "shell", "screenshot", "power", "alarms"} or not isinstance(payload, dict):
            raise ValueError("invalid_relay_request")
        if op == "alarms" and payload:
            raise ValueError("alarm_inventory_is_read_only")
        if op == "power" and payload.get("action") not in {"shutdown", "reboot", "bootloader"}:
            raise ValueError("invalid_power_action")
        # Serialize execution, including observation, to avoid overlapping commands.
        with self.operation_lock:
            with self.condition:
                connect_deadline = time.monotonic() + 30
                while not self.last_seen or time.monotonic() - self.last_seen >= 45:
                    remaining = connect_deadline - time.monotonic()
                    if remaining <= 0:
                        raise SkillError("phone_relay_offline")
                    self.condition.wait(remaining)
                command_id = uuid.uuid4().hex
                command = {"id": command_id, "op": op, "payload": payload,
                           "expires_at": time.time() + timeout, "dispatched": False, "result": None}
                self.commands[command_id] = command
                self.condition.notify_all()
                deadline = time.monotonic() + timeout
                try:
                    while command["result"] is None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            # Never redispatch a possibly executed mutation, or fall back to ADB.
                            raise SkillError("phone_relay_result_unknown" if command["dispatched"]
                                             else "phone_relay_timeout")
                        self.condition.wait(remaining)
                    result = command["result"]
                    if not result.get("ok"):
                        raise SkillError(str(result.get("error", "phone_relay_failed")))
                    return result["value"]
                finally:
                    self.commands.pop(command_id, None)


class AgentRemote(AgentAdb):
    def __init__(self, serial: str, relay: PhoneRelay):
        super().__init__(serial)
        self.relay = relay

    def shell(self, *args: str) -> str:
        return self.relay.request("shell", {"args": list(args)})["stdout"]

    def capture_png(self) -> bytes:
        return base64.b64decode(self.relay.request("screenshot", {})["data"], validate=True)

    def _accessibility_call(self, method: str, data: dict | None = None) -> dict:
        return self.relay.request("accessibility", {"method": method, "data": data or {}})

    def observe(self) -> dict:
        state = self._accessibility_call("observe")
        nodes = self._normalize_nodes(state.get("nodes", [])[:1200])
        blocked = "sensitive_screen" if _sensitive_screen(nodes) else state.get("blocked", "")
        return {"app": state.get("app", ""), "nodes": [] if blocked == "sensitive_screen" else nodes,
                "blocked": blocked, "source": "accessibility"}

    def capabilities(self) -> dict:
        value = super().capabilities()
        value["backend"] = {"mode": "phone_outbound", "degraded": False}
        value["observe"] = {"sources": ["accessibility"], "priority": ["accessibility"], "degraded": False}
        return value


_active_relay: PhoneRelay | None = None


def activate_relay() -> PhoneRelay | None:
    global _active_relay
    path = config_path()
    if path.is_file():
        _active_relay = PhoneRelay(json.loads(path.read_text(encoding="utf-8")))
    return _active_relay


def create_device(serial: str, adb_factory=AgentAdb):
    # Only standalone legacy CLI commands retain ADB. The running bridge pins the
    # outbound transport even when the phone is offline: never silently require USB.
    return AgentRemote(serial, _active_relay) if _active_relay is not None else adb_factory(serial)


def relay_enabled() -> bool:
    return _active_relay is not None
