"""Gemini screenshot fallback for apps that expose no accessibility tree."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.request
from typing import Any

from .model import SkillError, require
from .gemini_config import gemini_model


VISUAL_SYSTEM = """你是受限手机视觉执行器。只输出一个JSON对象，不要Markdown：
{"action":"tap|type_text|back|wait|finish","x":0,"y":0,"text":"","confirmed_chat":"","message":""}
x和y是当前截图的0到1000相对坐标。每次只做一步。只能点击当前截图中明确可见的目标。
任务涉及发消息时，只有看见聊天标题严格为“好宝”后才能点击发送；发送前可先返回confirmed_chat="好宝"。
不能操作支付、验证码、登录、授权、删除、恢复出厂等敏感页面。type_text只允许ASCII文本。"""


def capture_png(serial: str) -> bytes:
    from .phone_relay import create_device
    return create_device(serial).capture_png()


def visual_plan(task: str, serial: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    require(key, "missing_gemini_key")
    model = gemini_model()
    image = base64.b64encode(capture_png(serial)).decode("ascii")
    prompt = json.dumps({"task": task, "history": history[-6:]}, ensure_ascii=False)
    body = json.dumps({"contents": [{"parts": [
        {"text": VISUAL_SYSTEM + "\n当前任务和历史：" + prompt},
        {"inline_data": {"mime_type": "image/png", "data": image}},
    ]}], "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048,
                                 "thinkingConfig": {"thinkingLevel": "low"},
                                 "responseMimeType": "application/json"}}).encode("utf-8")
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key}, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    raw = payload["candidates"][0]["content"]["parts"][0].get("text", "")
    result = json.loads(raw)
    require(isinstance(result, dict), "invalid visual response")
    require(result.get("action") in {"tap", "type_text", "back", "wait", "finish"},
            "unsupported visual action")
    if result["action"] == "tap":
        require(type(result.get("x")) in (int, float) and type(result.get("y")) in (int, float),
                "invalid visual coordinates")
        require(0 <= result["x"] <= 1000 and 0 <= result["y"] <= 1000, "visual coordinates out of range")
    if result["action"] == "type_text":
        value = result.get("text", "")
        require(isinstance(value, str) and 0 < len(value) <= 500, "invalid visual input")
        require(re.fullmatch(r"[A-Za-z0-9 .,!?@:_/\\-]+", value) is not None,
                "only_safe_ascii_input_supported")
    if result.get("confirmed_chat"):
        require(result["confirmed_chat"] == "好宝", "visual_chat_target_mismatch")
    return result
