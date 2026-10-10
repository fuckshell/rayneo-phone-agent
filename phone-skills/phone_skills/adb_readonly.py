"""Small, read-only ADB adapter for deterministic Android settings checks."""
from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET

from .model import require
from .selector import Node, State


class AdbReadonlyAdapter:
    kind = "adb-readonly"

    def __init__(self, serial: str, timeout: float = 15):
        require(re.fullmatch(r"[A-Za-z0-9._:-]+", serial) is not None, "invalid ADB serial")
        self.device_id = serial
        self.timeout = timeout
        self.deadline = float("inf")

    def _run(self, *args):
        return subprocess.run(["adb", "-s", self.device_id, "shell", *args], check=True,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=self.timeout).stdout

    def open(self):
        state = subprocess.run(["adb", "-s", self.device_id, "get-state"], check=True,
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=self.timeout).stdout.strip()
        require(state == "device", "ADB device is not online")

    def close(self):
        pass

    def observe(self):
        self._run("uiautomator", "dump", "/sdcard/phone-skills-ui.xml")
        xml = self._run("cat", "/sdcard/phone-skills-ui.xml")
        root = ET.fromstring(xml)
        nodes = []
        for i, item in enumerate(root.iter("node")):
            visible_flag = item.get("visible-to-user", "true")
            nodes.append(Node(key=str(i), resource_id=item.get("resource-id", ""),
                              text=item.get("text", ""), content_desc=item.get("content-desc", ""),
                              class_name=item.get("class", ""), visible=visible_flag == "true",
                              enabled=item.get("enabled") == "true", clickable=item.get("clickable") == "true",
                              editable=item.get("class", "") in {"android.widget.EditText"}, raw=item.attrib.copy()))
        activity = self._run("dumpsys", "activity", "activities")
        match = re.search(r"mFocusedApp=.*?\s([\w.]+)/(?:\.?[\w.$]+)", activity)
        app = match.group(1) if match else ""
        if app == "com.android.settings":
            blocked = ""
        else:
            blocked = "foreign_window"
        return State(app, "", nodes, blocked)

    def act(self, step, target):
        require(step["action"] == "launch", "ADB read-only adapter permits launch intents only")
        intent = step.get("args", {}).get("intent")
        require(intent, "read-only launch requires an explicit Android intent")
        self._run("am", "start", "-a", intent)
