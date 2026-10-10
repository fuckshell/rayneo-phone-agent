"""Pinned Mobilerun public API behind a killable, bounded RPC process."""
from __future__ import annotations

import multiprocessing
import time
from importlib.metadata import version

from .model import keys, require
from .selector import Node, State
from .storage import digest

PINNED = {"mobilerun-core": "1.6.2", "mobilerun-core-local": "0.6.0", "mobilerun-sdk": "5.5.0"}
STOP_WORDS = ("验证码", "支付密码", "确认支付", "确认转账", "删除账号", "恢复出厂", "允许访问", "授权", "登录", "sign in", "verification code", "confirm payment", "factory reset")


def check_versions():
    actual = {name: version(name) for name in PINNED}
    require(actual == PINNED, "device dependency versions differ from audited pins")
    return actual


def authorize(config, serial, skill):
    keys(config, {"schema_version", "serial", "approved_revisions", "allowed_apps", "operation_timeout_s"},
         {"schema_version", "serial", "approved_revisions", "allowed_apps", "operation_timeout_s"})
    require(config["schema_version"] == 1, "invalid device config schema")
    require(isinstance(serial, str) and serial and not any(c.isspace() for c in serial), "explicit device serial required")
    require(config["serial"] == serial, "serial is not paired in config")
    require(isinstance(config["approved_revisions"], list) and digest(skill) in config["approved_revisions"], "skill revision not reviewed for this device")
    require(skill["app"] in config["allowed_apps"], "app not allowed")
    require(type(config["operation_timeout_s"]) in (int, float) and 0 < config["operation_timeout_s"] <= 30, "invalid RPC timeout")
    require(not any(s.get("high_risk") or s["effect"] == "non_idempotent" for s in skill["steps"]), "v1 real-device mode blocks high-risk/non-idempotent skills")


def normalize(ui):
    ps = ui.get("phone_state", {})
    app = ps.get("app_id") or ps.get("package_name") or ""
    activity = ps.get("activity_name") or ""
    blocked = "dead_tree" if ui.dead_tree else ""
    nodes = []
    if len(ui.nodes) > 5000:
        return State(app, activity, [], "oversized_tree")
    for i, raw in enumerate(ui.nodes):
        text = raw.get("text") or ""
        desc = raw.get("contentDescription") or raw.get("content_description") or ""
        if raw.get("password") or raw.get("isPassword") or any(w in (text + " " + desc).casefold() for w in STOP_WORDS):
            blocked = "sensitive_screen"
        node_pkg = raw.get("packageName") or raw.get("package_name")
        if node_pkg and node_pkg != app and node_pkg not in {"com.android.systemui"}:
            blocked = "foreign_window"
        # Unknown visibility is not permission to click. Calibrate OEM flags at pairing.
        visibility_flags = [raw[k] for k in ("isVisibleToUser", "visibleToUser", "visible_to_user") if k in raw]
        visible = (any(v is True for v in visibility_flags) or raw.get("hidden") is False)
        visible = visible and not any(v is False for v in visibility_flags) and not raw.get("offscreen", False) and not raw.get("hidden", False)
        nodes.append(Node(key=str(i), resource_id=raw.get("resourceId") or raw.get("resource_id") or "",
                          text=text, content_desc=desc, class_name=raw.get("className") or raw.get("class_name") or "",
                          visible=visible, enabled=raw.get("isEnabled", raw.get("enabled")) is True,
                          clickable=raw.get("isClickable", raw.get("clickable")) is True,
                          editable=raw.get("isEditable", raw.get("editable")) is True,
                          raw=dict(raw)))
    return State(app, activity, nodes, blocked or ("missing_foreground" if not app else ""))


def _worker(pipe, serial):
    # Lazy imports and connection: no device side effects on module import or doctor.
    try:
        check_versions()
        from .local_connection import connect_readonly_input
        device = connect_readonly_input(serial)
        pipe.send((True, "ready"))
        while True:
            command, payload = pipe.recv()
            if command == "observe":
                pipe.send((True, normalize(device.ui())))
            elif command == "act":
                step, node = payload
                if step["action"] == "launch":
                    device.start_app(step["args"]["package"])
                elif step["action"] == "tap":
                    device.tap_node(node.raw, stealth=False)
                elif step["action"] == "type":
                    # Separate focus from typing: no implicit tap+type compound action.
                    require(node.raw.get("isFocused", node.raw.get("focused")) is True, "input must already be focused")
                    device.type(step["args"]["text"], clear=step["args"]["clear"], stealth=False)
                else:
                    raise ValueError("unsupported action")
                pipe.send((True, None))
            else:
                break
    except BaseException as exc:
        try:
            pipe.send((False, type(exc).__name__))
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        pipe.close()


class DeviceAdapter:
    kind = "mobilerun"

    def __init__(self, serial, timeout=10, cancel=None):
        self.device_id = serial
        self.timeout = timeout
        self.cancel = cancel or (lambda: False)
        self.deadline = float("inf")
        self.process = self.pipe = None

    def _receive(self):
        until = min(self.deadline, time.monotonic() + self.timeout)
        while time.monotonic() < until:
            if self.cancel():
                self.close()
                from .runtime import Stop
                raise Stop("cancelled")
            if self.pipe.poll(.05):
                try:
                    ok, result = self.pipe.recv()
                except EOFError:
                    raise ConnectionError("device worker disconnected") from None
                if not ok:
                    raise ConnectionError("device worker failed")
                return result
        self.close()
        raise TimeoutError("device operation deadline")

    def open(self):
        context = multiprocessing.get_context("spawn")
        self.pipe, child = context.Pipe()
        self.process = context.Process(target=_worker, args=(child, self.device_id), daemon=True)
        self.process.start()
        child.close()
        self._receive()

    def observe(self):
        self.pipe.send(("observe", None))
        return self._receive()

    def act(self, step, target):
        self.pipe.send(("act", (step, target)))
        return self._receive()

    def close(self):
        if self.process:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=2)
            self.process.close()
            self.process = None
        if self.pipe:
            self.pipe.close()
            self.pipe = None
