"""Local audio ingress for Phone AI.

The first bridge is intentionally dependency-free: Android sends raw PCM
16-bit mono 16 kHz chunks over HTTP and the PC writes a WAV when the session
is completed. The phone uses the public bridge; USB forwarding is optional.
"""
from __future__ import annotations

import argparse
import json
import threading
import uuid
import wave
import os
import urllib.request
import datetime as _dt
import re
import base64
import time
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from .audio_signal import pcm_signal
from .power_intent import (
    _command_text,
    is_shutdown_request,
    is_bootloader_request,
    is_reboot_request,
    is_shutdown_confirmation,
    is_power_confirmation,
    is_shutdown_cancellation,
    is_power_cancellation,
    power_request_kind,
)
from .task_router import route_task
from .gemini_config import DEFAULT_MODEL, gemini_model
from .model import SkillError
from .rhythm_store import RhythmService, RhythmStore
from .coros_sleep import CorosSleepCache
from .storage import atomic_json


_SHUTDOWN_CONFIRM_TTL = 90.0
_POWER_CONFIRM_TTL = _SHUTDOWN_CONFIRM_TTL


class AudioSessions:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._conversations: dict[str, list[dict[str, str]]] = {}
        self._pending_power_actions: dict[str, dict[str, Any]] = {}
        # Kept as a compatibility view for older diagnostics/tests.
        self._pending_shutdown: dict[str, float] = {}
        self._pending_alarms: dict[str, dict[str, Any]] = {}

    def _alarm_transition(self, conversation_id: str, text: str, llm: dict, context: str) -> dict | None:
        from .alarm import is_alarm_delete_request, parse_alarm_text
        from .alarm_resolution import resolve
        key = self._conversation_key(conversation_id)
        with self._lock:
            pending = self._pending_alarms.pop(key, None)
        if pending and pending["expires_at"] <= time.monotonic():
            pending = None
        route = llm.get("result", {}).get("route")
        if is_alarm_delete_request(text):
            route = "alarm_delete"
        parsed_current = parse_alarm_text(text)
        if pending and (parsed_current.get("status") == "ready" and
                        re.search(r"设置|设个|叫我|喊我|叫醒|唤醒", text) or
                        parsed_current.get("status") == "delete_requested" and
                        ("hour" in parsed_current or parsed_current.get("candidate_hours"))):
            pending = None
        if not pending and route not in {"alarm", "alarm_delete"}:
            return None
        operation = pending["operation"] if pending else route
        resolution = resolve(text, operation, context, pending)
        if resolution["decision"] == "clarify":
            with self._lock:
                self._pending_alarms[key] = {"request": resolution["request"], "operation": operation,
                    "question": resolution["reply"], "expires_at": time.monotonic() + 600}
        if resolution["decision"] == "unrelated":
            return None
        return resolution

    def phone_action_result(self, session_id: str, status: str) -> dict[str, Any]:
        """Resume a failed shortcut from the original request, at most once."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", session_id) or status not in {"deleted", "not_found", "error"}:
            raise ValueError("invalid_action_result")
        path = self.output_dir / (session_id + ".json")
        with self._lock:
            original = json.loads(path.read_text(encoding="utf-8"))
            if original.get("action", {}).get("status") != "pending_phone_delete":
                raise ValueError("no_pending_alarm_delete")
            previous = original.get("phone_receipt")
            if previous:
                return self._compact_phone_receipt(previous)
            receipt = {"status": status, "phase": "running", "reply": "手机 Agent 正在检查，尚未确认完成。"}
            original["phone_receipt"] = receipt
            atomic_json(path, original)
        if status == "deleted":
            receipt.update(phase="completed", reply="已删除匹配的闹钟。")
        else:
            from .agent_runner import run_agent
            text = original.get("asr", {}).get("text", "")
            if not text:
                raise ValueError("missing_original_request")
            resolved = original.get("alarm_resolution", {}).get("action", {})
            if "alarm_id" in resolved:
                text += (f"\n已结合上下文确定目标：ID={resolved['alarm_id']}，"
                         f"{resolved['hour']:02d}:{resolved['minute']:02d}。"
                         "仅删除这条目标；若已不存在或时间变化，不得删除其他时段或其他闹钟。")
            try:
                agent = run_agent(text, os.environ.get("PHONE_AI_SERIAL", "demo-device"), max_steps=12)
            except Exception as exc:
                agent = {"status": "error", "error_type": type(exc).__name__, "reason": str(exc)[:240]}
            # A model saying done is insufficient: require its goal assertion.
            confirmed = agent.get("status") == "succeeded" and agent.get("goal_verified") is True
            receipt.update(phase="completed", agent=agent,
                           reply=(agent.get("message") or "已验证删除闹钟完成。") if confirmed else
                           "快捷删除未完成，手机 Agent 已检查但仍未确认删除成功：" +
                           str(agent.get("reason") or agent.get("error_type") or agent.get("message") or "缺少完成证据")[:240])
        original["phone_receipt"] = receipt
        original["display_reply"] = receipt["reply"]
        atomic_json(path, original)
        return self._compact_phone_receipt(receipt)

    @staticmethod
    def _compact_phone_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
        # Full UI traces stay on disk, not in the APK's bounded HTTP response.
        return {key: receipt[key] for key in ("status", "phase", "reply") if key in receipt}

    @staticmethod
    def _conversation_key(conversation_id: str) -> str:
        return conversation_id or "__default__"

    def _power_transition(self, conversation_id: str, text: str) -> dict[str, Any] | None:
        """Run the fail-closed two-step power state machine.

        A pending confirmation is scoped to the phone conversation and expires
        quickly. Any non-confirmation consumes/cancels it so a later unrelated
        "确认" can never shut down the device.
        """
        key = self._conversation_key(conversation_id)
        now = time.monotonic()
        with self._lock:
            pending = self._pending_power_actions.get(key)
            # Older callers may have written the legacy shutdown map directly.
            if pending is None and key in self._pending_shutdown:
                pending = {"kind": "shutdown", "expires_at": self._pending_shutdown[key]}
            elif pending is not None and pending.get("kind") == "shutdown" and key in self._pending_shutdown:
                # Keep legacy diagnostics/tests authoritative if they update the
                # old expiry map while a shutdown is pending.
                pending = {"kind": "shutdown", "expires_at": self._pending_shutdown[key]}
            if pending is not None:
                kind = pending["kind"]
                if pending["expires_at"] <= now:
                    self._pending_power_actions.pop(key, None)
                    self._pending_shutdown.pop(key, None)
                    pending = None
                else:
                    if is_power_confirmation(text, kind):
                        self._pending_power_actions.pop(key, None)
                        self._pending_shutdown.pop(key, None)
                        return {"status": "confirmed", "kind": kind}
                    if is_power_cancellation(text, kind):
                        self._pending_power_actions.pop(key, None)
                        self._pending_shutdown.pop(key, None)
                        return {"status": "cancelled", "kind": kind, "reason": "user_cancelled"}
                    requested_kind = power_request_kind(text)
                    if requested_kind == kind:
                        expires_at = now + _POWER_CONFIRM_TTL
                        self._pending_power_actions[key] = {"kind": kind, "expires_at": expires_at}
                        if kind == "shutdown":
                            self._pending_shutdown[key] = expires_at
                        return {"status": "pending_confirmation", "kind": kind,
                                "expires_in_seconds": int(_POWER_CONFIRM_TTL)}
                    self._pending_power_actions.pop(key, None)
                    self._pending_shutdown.pop(key, None)
                    return {"status": "cancelled", "kind": kind,
                            "reason": "explicit_confirmation_required"}
            requested_kind = power_request_kind(text)
            if requested_kind:
                expires_at = now + _POWER_CONFIRM_TTL
                self._pending_power_actions[key] = {"kind": requested_kind, "expires_at": expires_at}
                if requested_kind == "shutdown":
                    self._pending_shutdown[key] = expires_at
                return {"status": "pending_confirmation", "kind": requested_kind,
                        "expires_in_seconds": int(_POWER_CONFIRM_TTL)}
            if _command_text(text) in {"确认", "确定", "是", "是的"}:
                return {"status": "not_requested", "kind": "power",
                        "reason": "no_pending_power_request"}
            return None

    def _shutdown_transition(self, conversation_id: str, text: str) -> dict[str, Any] | None:
        """Compatibility wrapper for older callers and tests."""
        return self._power_transition(conversation_id, text)

    def append(self, session_id: str, payload: bytes) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", session_id):
            raise ValueError("invalid session_id")
        if not payload or len(payload) % 2:
            raise ValueError("PCM requires complete 16-bit samples")
        with self._lock:
            item = self._sessions.setdefault(session_id, {"bytes": 0, "chunks": 0, "data": bytearray()})
            if item["bytes"] + len(payload) > 1_920_000:
                raise ValueError("session exceeds 60 seconds")
            item["data"].extend(payload)
            item["bytes"] += len(payload)
            item["chunks"] += 1
            return {"accepted": True, "session_id": session_id, "bytes": item["bytes"], "chunks": item["chunks"]}

    def finish(self, session_id: str, conversation_id: str = "") -> dict[str, Any]:
        with self._lock:
            item = self._sessions.pop(session_id, None)
        if item is None:
            raise KeyError(session_id)
        path = self.output_dir / f"{session_id}.wav"
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(bytes(item["data"]))
        pcm = bytes(item["data"])
        result = {"accepted": True, "session_id": session_id, "bytes": item["bytes"], "chunks": item["chunks"], "file": str(path)}
        signal = pcm_signal(pcm)
        result["audio_signal"] = signal
        # A silent recording must never reach a generative model: otherwise
        # background noise can be hallucinated into an alarm request.
        if not signal["speech_like"]:
            result["asr"] = {"status": "empty", "reason": "silence_or_low_signal"}
            result["llm"] = {"status": "disabled", "reason": "silence_gate"}
            result["reply"] = "没有识别到清晰的人声，请再说一次。"
            (self.output_dir / (session_id + ".json")).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            return result
        # Gemini Flash handles both speech perception and intent classification.
        # If its endpoint is unavailable, large-v3 transcribes locally and
        # DeepSeek performs the same routing step.
        history = self._conversations.get(conversation_id, [])[-6:] if conversation_id else []
        context = "\n".join(f'{row["role"]}：{row["text"]}' for row in history)
        result["asr"], result["llm"] = understand_audio(path, context=context)
        if result["asr"].get("status") == "ok":
            text = result["asr"]["text"]
            power = self._power_transition(conversation_id, text)
            if power is not None:
                with self._lock:
                    self._pending_alarms.pop(self._conversation_key(conversation_id), None)
                result["llm"] = {"status": "ok", "provider": "phone-skills",
                                  "model": "power_confirmation",
                                  "result": {"route": power["kind"], "reply": ""}}
                if power["status"] == "confirmed":
                    result["action"] = execute_phone_action(
                        text, {"status": "ok", "provider": "phone-skills",
                               "model": "power_confirmation",
                               "result": {"route": power["kind"] + "_confirmed", "reply": ""}},
                        self.output_dir,
                    )
                else:
                    result["action"] = power
            else:
                resolution = self._alarm_transition(conversation_id, text, result.get("llm", {}), context)
                if resolution is not None:
                    result["alarm_resolution"] = resolution
                    resolved_route = "alarm_delete" if resolution["decision"] == "delete" else "alarm"
                    result["llm"] = {"status": "ok", "provider": resolution.get("provider", "alarm-resolution"),
                        "model": resolution.get("model", "contextual_alarm"),
                        "result": {"route": resolved_route, "reply": resolution.get("reply", "")}}
                    result["action"] = resolution.get("action", {"status": "clarification_required",
                        "kind": resolved_route, "reply": resolution.get("reply", "已取消待澄清的闹钟请求。")})
                else:
                    result["action"] = execute_phone_action(text, result.get("llm", {}), self.output_dir)
            parsed = result.get("llm", {}).get("result", {})
            # Only the tool-owning agent can decide whether personal context is
            # missing. A router's clarification must not bypass memory lookup.
            if parsed.get("route") in {"complex", "clarify"} or result["llm"].get("status") != "ok":
                result["pc_agent"] = openclaw_answer(
                    result["asr"]["text"], context, session_id
                )
                if result["pc_agent"].get("status") == "ok":
                    result["reply"] = result["pc_agent"]["reply"]
            # The request originates on the phone. Keep that identity explicit
            # so PC execution and RayNeo knowledge ingestion cannot be confused
            # with a command generated by a passive data source.
            parsed = result.get("llm", {}).get("result", {})
            result["command"] = {
                "origin": "phone-agent",
                "request_id": session_id,
                "text": result["asr"]["text"],
                "route": parsed.get("route", "clarify"),
                "execution_plane": "pc-agent",
                "status": result["action"].get("status", "not_executed"),
            }
        # Keep the execution answer untouched for logs and troubleshooting.
        # Only the user-facing copy is optionally polished after OpenClaw has
        # finished its memory/tool work.
        raw_reply = result_reply(result)
        result["reply"] = raw_reply
        result["raw_reply"] = raw_reply
        result["display_reply"] = raw_reply
        if result.get("pc_agent", {}).get("status") == "ok":
            result["display_reply"] = polish_openclaw_reply(
                raw_reply, result["asr"].get("text", ""), context
            )
        if conversation_id and result["asr"].get("status") == "ok":
            turns = self._conversations.setdefault(conversation_id, [])
            turns.extend([{"role": "用户", "text": result["asr"].get("text", "")},
                          {"role": "助手", "text": result["reply"]}])
            del turns[:-12]
        (self.output_dir / (session_id + ".json")).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return result


def openclaw_answer(text: str, context: str, request_id: str) -> dict[str, Any]:
    """Send a phone-originated complex request through the existing OpenClaw agent.

    OpenClaw owns memory, file and WeChat tools; this bridge only transports the
    request and returns its final text. No local model or second tool registry is
    created here.
    """
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
    if not token:
        return {"status": "unavailable", "reason": "missing_openclaw_gateway_token"}
    gateway = os.environ.get("OPENCLAW_GATEWAY", "http://127.0.0.1:18789").rstrip("/")
    model = os.environ.get("PHONE_AI_OPENCLAW_MODEL", "openclaw/wechat-remote")
    prompt = ("你正在处理本人从手机 Phone AI 发起的请求。请直接使用现有 OpenClaw 工具、个人记忆、"
              "微信知识库和本地文件工具完成任务；需要修改文件时遵守工作区规则并返回实际结果。"
              "涉及本人的工作、安排、历史对话或本地资料时，必须实际检索本地资料，"
              "不能因为当前一句未写学校、身份或年份就直接追问。结合会话上下文和当前日期理解指代。"
              "个人时间安排、学生来上课等问题的查询顺序：先读取所问时间范围内的个人日历"
              "（calendar/tasks.ics，正确展开周期事件并核对取消、改期），再检索相关微信原始对话核实。"
              "先依据日历和对话识别具体事件、人物及活动，不得擅自把个人上课或补课问题解释成学校统一返校，"
              "也不得拿假期结束后的安排替代用户询问的假期期间安排。日历无结果时继续查微信，不代表没有安排；"
              "来源冲突时核对较新的明确变更并说明差异，不得静默选一个。"
              "检索摘要不足时继续读取原始通知；日期问题要核对通知日期、年份、对象和调休安排。"
              "回答必须说明实际查到的来源和关键原文，不得把常识或推测当作本地查询结果。"
              "准确按记录的发送人字段归属引文；记录缺少本人发出的消息时不得把对方的‘好/行’说成本人确认，"
              "不得根据对话模式虚构发送人、确认回执或未记录的消息。未完整检索时不能宣称所有安排均已查全。"
              "检索必须保留每条证据的文件来源和消息时间（grep使用-Hn，不使用-h丢失文件名），"
              "最终引用前实际读取所引用文件核对原文。禁止凭记忆拼接来源路径；未验证的路径不要展示。"
              "只有检索后仍存在影响答案的歧义才追问；工具失败或未找到依据时如实说明，不能编造。"
              "不要只回复‘好的’或‘正在查找’，必须在本轮给出查询结果或明确失败原因。"
              "最终回复用简洁中文，先给所问安排再附必要来源与不确定性；不要输出思考过程、英文过渡句，"
              "也不要用已知不符合当前意图的其他问题答案凑篇幅。\n"
              f"电脑当前日期时间：{_dt.datetime.now().isoformat(timespec='seconds')}\n")
    question = ""
    if context:
        question += "同一会话上下文（仅供理解追问）：\n" + context[-4000:] + "\n"
    question += "当前请求：\n" + text
    question += ("\n回复前请核对原始证据的来源、日期和发送人。最终只输出给我的简洁中文答案，"
                 "先结论，再聊天名称、消息日期和必要原文；保留真实不确定性。"
                 "不要附英文思考、不符合当前问题意图的其他安排或未经核实的文件路径。")
    body = json.dumps({"model": model, "user": f"phone:{request_id}", "stream": False,
                       "messages": [{"role": "system", "content": prompt},
                                    {"role": "user", "content": question}]}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        gateway + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
        reply = str(payload.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()
        if not reply:
            return {"status": "error", "reason": "empty_openclaw_reply"}
        return {"status": "ok", "model": model, "reply": reply}
    except Exception as exc:
        return {"status": "error", "reason": type(exc).__name__}


def gemini_polish_reply(raw_reply: str, user_text: str = "", context: str = "") -> str:
    """Turn an OpenClaw answer into readable UI copy without changing facts."""
    raw_reply = str(raw_reply or "").strip()
    if not raw_reply:
        return ""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        return ""
    model = gemini_model()
    prompt = (
        "你是手机助手的最终展示编辑器。把下面 OpenClaw 的原始答复整理成简洁、自然的中文。"
        "只输出给用户看的纯文本，不要 JSON、Markdown 标题、来源清单、模型名、工具名、内部路径、"
        "思考过程或执行步骤。必须保留原文中的事实、时间、数量、最终结果和失败原因；不能补充、推测、"
        "改写成相反结论，也不能把失败写成成功。若原文说明未完成或证据不足，要明确保留。"
        "可以用短段落和项目符号，但不要删掉关键证据。不要回复‘好的’或‘正在处理’这类空话。\n"
        f"用户问题：{user_text}\n"
        f"原始答复：\n{raw_reply}\n"
    )
    if context:
        prompt += "会话上下文仅用于指代理解，不得从中添加原文没有的事实：\n" + context[-2000:]
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1200,
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    with urllib.request.urlopen(
            request, timeout=float(os.environ.get("PHONE_AI_DISPLAY_TIMEOUT", "12"))) as response:
        payload = json.loads(response.read().decode("utf-8"))
    polished = "".join(
        part.get("text", "")
        for part in payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        if not part.get("thought")
    ).strip()
    polished = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", polished).strip()
    if not polished or len(polished) > 6000:
        return ""
    # A model returning a structured object is not a valid display response.
    if polished.startswith("{") and polished.endswith("}"):
        return ""
    return polished


def polish_openclaw_reply(raw_reply: str, user_text: str = "", context: str = "") -> str:
    """Best-effort presentation pass; never hide a valid OpenClaw answer."""
    raw_reply = str(raw_reply or "")
    try:
        polished = gemini_polish_reply(raw_reply, user_text, context)
    except Exception:
        return raw_reply
    return polished.strip() if polished and polished.strip() else raw_reply


def result_reply(result):
    pc_agent = result.get("pc_agent") or {}
    if pc_agent.get("status") == "ok":
        return pc_agent["reply"]
    if pc_agent:
        return ("本地记忆/电脑 Agent 查询失败：" + str(pc_agent.get("reason") or "unknown_error")
                + "。没有取得查询结果，不能据此给出答案。")
    asr = result.get("asr", {})
    if asr.get("reason") == "missing_gemini_key":
        return "Gemini Flash 未配置，语音没有执行；请检查电脑端密钥环境。"
    if asr.get("status") == "empty":
        return "没有识别到清晰的人声，请再说一次。"
    if asr.get("status") != "ok":
        return "语音识别未完成，请检查电脑端识别服务。"
    action = result.get("action", {})
    if action.get("status") == "clarification_required":
        return action["reply"]
    llm = result.get("llm", {})
    parsed = llm.get("result", {})
    power_status = action.get("status")
    power_kind = parsed.get("route")
    if power_kind in {"shutdown", "reboot", "bootloader", "shutdown_confirmed", "reboot_confirmed", "bootloader_confirmed"}:
        base_kind = power_kind.removesuffix("_confirmed")
        labels = {"shutdown": "关机", "reboot": "重启", "bootloader": "重启到 Bootloader"}
        label = labels[base_kind]
        if power_status == "pending_confirmation":
            return f"确认{label}吗？回复“确认”才会执行，回复“取消”可放弃。"
        if power_status == "cancelled":
            return f"已取消{label}。"
        if power_status == "not_requested":
            return "当前没有待确认的电源操作。"
        if power_status in {"error", "rejected", "outcome_unknown"}:
            return f"{label}指令未发送：" + str(action.get("reason") or action.get("error_type") or "手机桥接不可用")
        if power_status == "succeeded":
            return f"已发送{label}指令。"
    if power_status == "not_requested" and action.get("kind") == "power":
        return "当前没有待确认的电源操作。"
    if action.get("status") == "rejected" and parsed.get("route") == "skill":
        return "这个操作没有通过当前执行器校验，手机没有执行。"
    if parsed.get("route") in {"app_launch", "app_task"} and action.get("status") != "succeeded":
        if action.get("kind") == "app_task":
            phase_names = {
                "observing": "观察阶段",
                "discovering_apps": "发现应用阶段",
                "planning": "规划阶段",
                "executing": "执行阶段",
                "verifying": "验证阶段",
                "failed": "执行阶段",
                "blocked": "安全拦截阶段",
            }
            phase = phase_names.get(action.get("phase"), "执行阶段")
            detail = action.get("reason") or action.get("error_type") or "未知错误"
            if action.get("error_type") == "accessibility_service_not_connected":
                return "手机控件服务暂未连接，已停止本次操作；请稍后重试。"
            reply = f"已进入手机 Agent，但在{phase}失败：{detail}"
            if action.get("learning_task_file"):
                reply += "；本次学习任务已记录。"
            return reply
        return "应用没有打开成功：" + str(action.get("error_type") or action.get("reason") or "执行失败")
    if parsed.get("route") == "amap_ai" and action.get("status") != "succeeded":
        return "高德 AI 没有打开成功：" + str(action.get("error_type") or "执行失败")
    if action.get("status") == "succeeded":
        if action.get("skill_id") == "settings_device_info":
            return "已打开关于手机页面，Android 版本信息已确认。"
        if action.get("kind") == "alarm":
            return "闹钟已设置。"
        if action.get("kind") == "alarm_delete":
            return "已删除闹钟。"
        if action.get("kind") == "amap_ai":
            return "已打开高德地图，正在把这句话交给高德 AI 助手继续确认。"
        if action.get("kind") == "app_launch":
            return "应用已打开。"
        if action.get("kind") == "app_task" and action.get("message"):
            return action["message"]
    if llm.get("status") != "ok":
        return "已识别语音，但模型暂时不可用；没有执行手机操作。"
    if parsed.get("route") == "alarm":
        status = result.get("action", {}).get("status")
        if status == "pending_phone":
            return "闹钟参数已返回手机，正在调用系统时钟。"
        if status == "not_executed":
            return "识别到设置闹钟的请求，但电脑端执行开关已关闭，没有设置闹钟。"
        if status in {"error", "rejected", "outcome_unknown"}:
            if status == "rejected" and action.get("reason") == "explicit hour and minute required":
                return "我能帮你设置闹钟，请告诉我具体时间，例如早上七点。"
            if status == "rejected" and action.get("reason") == "morning or evening not specified":
                return "你说的时间还缺少上午或下午，请补充一下时间段。"
            return "设置闹钟未得到系统确认，本次没有报告为成功。"
    if parsed.get("route") == "alarm_delete":
        status = action.get("status")
        if status == "pending_phone_delete":
            return "已识别为删除闹钟请求，正在从手机时钟中删除。"
        if status in {"not_found", "rejected", "error"}:
            return "没有删除闹钟：" + str(action.get("reason") or action.get("error_type") or "系统未找到匹配项")
    reply = parsed.get("reply", "模型未给出有效回答。")
    if parsed.get("route") in {"alarm", "skill", "amap_ai"}:
        reply += "\n手机操作尚未执行。"
    return reply


ROUTE_PROMPT = ("你是中文手机语音助手的意图路由器。理解自动语音识别产生的同音字、错字和断句错误，按整句话的真实语义判断，不要维护或套用固定错字表。"
                "严格只输出JSON对象，字段为route、reply、alarm。"
                "route为alarm、alarm_delete、shutdown、reboot、bootloader、skill、amap_ai、app_launch、app_task、complex、clarify之一。"
                "当route为alarm且时间明确时，alarm必须是结构化对象：相对时间用{\"mode\":\"relative\",\"minutes\":120}；"
                "指定时间用{\"mode\":\"absolute\",\"hour\":6,\"minute\":40,\"day\":\"tomorrow\"}，day只能为today、tomorrow或next。"
                "时间不明确或route不是alarm时alarm必须为null。设置或叫醒意图明确就归alarm，即使缺少时间或未说明上午下午；"
                "删除意图明确就归alarm_delete，即使目标不完整。时段歧义不归clarify或complex，交给后续闹钟解释器结合当前时间、上下文和真实已有闹钟判断，不明确才问一句，不能猜。不要把错字修正结果写回用户原话。"
                "知识问答、个人安排与会话追问归complex，交给拥有本地记忆和工具的Agent检索回答；reply不是最终答案。"
                "设置闹钟归alarm；删除、取消、移除已有闹钟归alarm_delete，绝不能归alarm；明确关机归shutdown，普通重启归reboot，重启到bootloader、fastboot或引导模式归bootloader，三者都必须由上层二次确认，不能直接执行；只读关于手机归skill，导航归amap_ai。app_launch只保留给系统快捷动作；普通应用请求即使只是打开，也必须归app_task，交给完整Agent自主查找应用、观察页面、执行和验证。"
                "不要按关键词判断完成，不要声称其他手机操作不能执行。你只负责路由，绝不能声称已完成。含糊或背景语音归clarify。"
                "不要编造实时事实。用户输入：")


def validate_alarm_spec(value):
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    if value.get("mode") == "relative" and set(value) == {"mode", "minutes"}:
        return isinstance(value["minutes"], int) and not isinstance(value["minutes"], bool) and 1 <= value["minutes"] <= 1440
    if value.get("mode") == "absolute" and set(value) == {"mode", "hour", "minute", "day"}:
        return (isinstance(value["hour"], int) and not isinstance(value["hour"], bool)
                and isinstance(value["minute"], int) and not isinstance(value["minute"], bool)
                and 0 <= value["hour"] <= 23 and 0 <= value["minute"] <= 59
                and value["day"] in {"today", "tomorrow", "next"})
    return False


def parse_reply(raw, provider, model):
    try:
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        value = json.loads(clean)
        if (not isinstance(value, dict) or set(value) not in ({"route", "reply"}, {"route", "reply", "alarm"})
                or value.get("route") not in {"alarm", "alarm_delete", "shutdown", "reboot", "bootloader", "skill", "amap_ai", "app_launch", "app_task", "complex", "clarify"}
                or not isinstance(value.get("reply"), str) or len(value["reply"]) > 4000
                or not validate_alarm_spec(value.get("alarm"))):
            raise ValueError("invalid response")
        return {"status": "ok", "provider": provider, "model": model, "result": value}
    except (ValueError, TypeError):
        return {"status": "error", "provider": provider, "model": model, "error_type": "InvalidResponse"}


def parse_audio_reply(raw: str, provider: str, model: str) -> dict[str, Any]:
    """Validate Gemini's multimodal response without allowing tool commands."""
    try:
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        value = json.loads(clean)
        if (not isinstance(value, dict)
                or set(value) not in ({"transcript", "route", "reply"},
                                      {"transcript", "route", "reply", "alarm"})
                or not isinstance(value["transcript"], str)
                or len(value["transcript"]) > 2000
                or value.get("route") not in {"alarm", "alarm_delete", "shutdown", "reboot", "bootloader", "skill", "amap_ai", "app_launch", "app_task", "complex", "clarify"}
                or not isinstance(value["reply"], str) or len(value["reply"]) > 4000
                or not validate_alarm_spec(value.get("alarm"))):
            raise ValueError("invalid response")
        return {"status": "ok", "provider": provider, "model": model, "result": value}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {"status": "error", "provider": provider, "model": model,
                "error_type": "InvalidResponse"}


_gemini_breaker_lock = threading.Lock()
_gemini_fail_until = 0.0


def understand_audio(audio_path: Path, context: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """Use Gemini first, then local large-v3 ASR plus DeepSeek routing."""
    gemini_asr, gemini_llm = gemini_audio_with_deadline(audio_path, context=context)
    if gemini_asr.get("status") == "ok" and gemini_llm.get("status") == "ok":
        return gemini_asr, gemini_llm
    local_asr = local_asr_transcribe(audio_path)
    local_asr["fallback_from"] = "gemini"
    local_asr["fallback_reason"] = (
        gemini_asr.get("reason") or gemini_asr.get("error_type") or gemini_llm.get("error_type")
    )
    if local_asr.get("status") != "ok":
        return local_asr, {"status": "disabled", "provider": "deepseek",
                           "reason": "local_asr_failed"}
    return local_asr, route_after_local_asr(local_asr["text"], context=context)


def gemini_audio_with_deadline(audio_path: Path, context: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """Bound total Gemini wait time even when DNS tries several blocked addresses."""
    global _gemini_fail_until
    completed: queue.Queue = queue.Queue(maxsize=1)

    def call():
        value = gemini_audio_route(audio_path, context=context)
        try:
            completed.put_nowait(value)
        except queue.Full:
            pass

    threading.Thread(target=call, name="phone-ai-gemini", daemon=True).start()
    deadline = float(os.environ.get("PHONE_AI_GEMINI_DEADLINE", "12"))
    try:
        return completed.get(timeout=deadline)
    except queue.Empty:
        with _gemini_breaker_lock:
            _gemini_fail_until = time.monotonic() + float(
                os.environ.get("PHONE_AI_GEMINI_COOLDOWN", "120")
            )
        model = os.environ.get("PHONE_AI_GEMINI_MODEL") or DEFAULT_MODEL
        return ({"status": "error", "error_type": "GeminiDeadlineExceeded"},
                {"status": "error", "provider": "gemini", "model": model,
                 "error_type": "GeminiDeadlineExceeded"})


def local_asr_transcribe(audio_path: Path) -> dict[str, Any]:
    url = os.environ.get("PHONE_AI_ASR_URL", "http://127.0.0.1:18883/v1/transcribe")
    request = urllib.request.Request(
        url, data=audio_path.read_bytes(),
        headers={"Content-Type": "audio/wav"}, method="POST")
    try:
        timeout = float(os.environ.get("PHONE_AI_ASR_TIMEOUT", "180"))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict) or value.get("status") not in {"ok", "empty", "error"}:
            raise ValueError("invalid local ASR response")
        return value
    except Exception as exc:
        return {"status": "error", "provider": "faster-whisper", "model": "large-v3",
                "error_type": type(exc).__name__, "detail": str(exc)[:240]}


def route_after_local_asr(text: str, context: str = "") -> dict[str, Any]:
    """Route a local transcript without retrying the failed Gemini endpoint."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    model = os.environ.get("PHONE_AI_DEEPSEEK_MODEL", "deepseek-flash")
    if key:
        answer = openai_compatible_route(
            text, "deepseek", "https://api.deepseek.com/chat/completions", model, key,
            context=context,
        )
        if answer.get("status") == "ok":
            return tighten_local_route(text, answer)
    answer = ollama_route(text)
    return tighten_local_route(text, answer)


def tighten_local_route(text: str, answer: dict[str, Any]) -> dict[str, Any]:
    """Keep explicit low-risk actions deterministic after speech recognition."""
    from .alarm import is_alarm_delete_request, parse_alarm_text
    if is_shutdown_request(text):
        return {"status": "ok", "provider": "phone-skills", "model": "shutdown_intent",
                "result": {"route": "shutdown", "reply": "确认关机吗？"}}
    parsed_alarm = parse_alarm_text(text, _dt.datetime.now())
    if is_alarm_delete_request(text) or parsed_alarm.get("status") == "delete_requested":
        return {"status": "ok", "provider": "phone-skills", "model": "alarm_mutation_parser",
                "result": {"route": "alarm_delete", "reply": "正在删除闹钟。"}}
    if parsed_alarm.get("status") == "ready":
        return {"status": "ok", "provider": answer.get("provider", "phone-skills"),
                "model": answer.get("model", "alarm_parser"),
                "result": {"route": "alarm", "reply": "正在设置闹钟。"}}
    if is_device_info_request(text):
        return {"status": "ok", "provider": "phone-skills", "model": "settings_device_info",
                "result": {"route": "skill", "reply": "正在打开关于手机页面并确认 Android 版本信息。",
                           "skill_id": "settings_device_info"}}
    if answer.get("status") == "ok" and answer.get("result", {}).get("route") == "skill":
        answer["result"]["route"] = "app_task"
    return answer


def gemini_audio_route(audio_path: Path, context: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """Send the recorded WAV as inline audio to Gemini Flash."""
    global _gemini_fail_until
    from .alarm import is_alarm_delete_request, parse_alarm_text
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    try:
        model = gemini_model()
    except SkillError as exc:
        return ({"status": "unavailable", "reason": str(exc)},
                {"status": "disabled", "provider": "gemini", "reason": str(exc)})
    if not key:
        return ({"status": "unavailable", "reason": "missing_gemini_key"},
                {"status": "disabled", "provider": "gemini", "model": model})
    with _gemini_breaker_lock:
        remaining = _gemini_fail_until - time.monotonic()
    if remaining > 0:
        return ({"status": "error", "error_type": "CircuitOpen"},
                {"status": "error", "provider": "gemini", "model": model,
                 "error_type": "CircuitOpen", "retry_after_seconds": int(remaining)})
    prompt = ("你是手机语音助手的听写和路由器。只输出一个JSON对象，不要Markdown。"
              "先按原音逐字听写，再根据整句话理解自动语音识别中可能出现的同音字、错字和断句错误；不要靠固定错字表。"
              "transcript仍填写听到的原话，不要为了意图判断而改写；应用名、人名、地名等专有名词不要编造。"
              "字段必须严格为 transcript、route、reply、alarm。route只能是 alarm、alarm_delete、shutdown、reboot、bootloader、skill、amap_ai、app_launch、app_task、complex、clarify。"
              "当route为alarm且时间明确时，alarm填写结构化对象：相对时间用{\"mode\":\"relative\",\"minutes\":120}；"
              "指定时间用{\"mode\":\"absolute\",\"hour\":6,\"minute\":40,\"day\":\"tomorrow\"}，day只能为today、tomorrow或next；其余情况alarm为null。"
              "设置或叫醒意图明确就归alarm，即使缺少时间或未说明上午下午，不要追问是否设置；删除、取消、移除已有闹钟归alarm_delete，即使目标不完整或句子带有时间也绝不能归alarm。"
              "时段歧义不归clarify或complex，交给后续闹钟解释器结合当前时间、上下文和真实已有闹钟判断，不明确才问一句，不能猜。"
              "明确说关机归shutdown，普通重启归reboot，重启到bootloader、fastboot或引导模式归bootloader；这三个路由电脑端都必须先询问一次确认，不能直接执行。只读查看关于手机归skill。app_launch只用于系统快捷动作；所有普通应用请求即使只是打开，也归app_task，把完整原话交给执行Agent自主查找应用、观察页面、执行和验证。"
              "导航交给高德归amap_ai。不要用是否出现某个关键词代替理解用户目的；普通手机操作可以交给Agent，不要声称不在白名单。"
              "知识问题、个人安排和会话追问归complex，交给拥有本地记忆和工具的Agent，不能因缺少学校、身份或年份直接追问。"
              "无法听清当前语音才归clarify。reply只是路由回应，不是知识问题的最终答案，不能声称已经执行或正在执行任何操作。")
    if context:
        prompt += "\n这是同一手机会话最近的上下文，只用于理解追问，不要把历史内容误当成当前指令：\n" + context[-4000:]
    try:
        encoded = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        body = json.dumps({"contents": [{"parts": [
            {"text": prompt}, {"inline_data": {"mime_type": "audio/wav", "data": encoded}}
        ]}], "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048,
                                   "thinkingConfig": {"thinkingLevel": "low"},
                                   "responseMimeType": "application/json"}}).encode("utf-8")
        request = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key}, method="POST")
        timeout = float(os.environ.get("PHONE_AI_GEMINI_TIMEOUT", "15"))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw = "".join(part.get("text", "") for part in payload["candidates"][0]["content"]["parts"]
                      if not part.get("thought")).strip()
        llm = parse_audio_reply(raw, "gemini", model)
        if llm.get("status") != "ok":
            return ({"status": "error", "error_type": "InvalidResponse"}, llm)
        transcript = llm["result"]["transcript"].strip()
        asr = {"status": "ok" if transcript else "empty", "text": transcript,
               "provider": "gemini", "model": model}
        # Deterministic local classification only tightens explicit alarm and
        # allowlisted settings requests; it never performs speech recognition.
        if transcript and is_alarm_delete_request(transcript):
            llm["result"]["route"] = "alarm_delete"
            llm["result"]["reply"] = "正在删除闹钟。"
        elif transcript and power_request_kind(transcript):
            llm["result"]["route"] = power_request_kind(transcript)
            llm["result"]["reply"] = "需要确认后执行电源操作。"
        elif transcript and parse_alarm_text(transcript, _dt.datetime.now()).get("status") == "ready":
            llm["result"]["route"] = "alarm"
            llm["result"]["reply"] = "正在设置闹钟。"
        elif transcript and is_device_info_request(transcript):
            llm.update(provider="phone-skills", model="settings_device_info")
            llm["result"]["route"] = "skill"
            llm["result"]["skill_id"] = "settings_device_info"
            llm["result"]["reply"] = "正在打开关于手机页面并确认 Android 版本信息。"
        elif llm["result"]["route"] == "skill":
            # "skill" is reserved for the explicit read-only Settings skill.
            # Ordinary app actions must reach the general Agent planner; the
            # old fallback incorrectly called them a whitelist violation.
            llm["result"]["route"] = "app_task"
            llm["result"]["reply"] = "正在交给手机 Agent 执行。"
        with _gemini_breaker_lock:
            _gemini_fail_until = 0.0
        return asr, llm
    except urllib.error.HTTPError as exc:
        with _gemini_breaker_lock:
            _gemini_fail_until = time.monotonic() + float(os.environ.get("PHONE_AI_GEMINI_COOLDOWN", "120"))
        return ({"status": "error", "error_type": "GeminiHTTP"},
                {"status": "error", "provider": "gemini", "model": model,
                 "error_type": f"HTTP_{exc.code}"})
    except Exception as exc:
        with _gemini_breaker_lock:
            _gemini_fail_until = time.monotonic() + float(os.environ.get("PHONE_AI_GEMINI_COOLDOWN", "120"))
        return ({"status": "error", "error_type": type(exc).__name__, "detail": str(exc)[:240]},
                {"status": "error", "provider": "gemini", "model": model,
                 "error_type": type(exc).__name__, "detail": str(exc)[:240]})


def route_model(text: str) -> dict[str, Any]:
    # An explicit, unambiguous alarm is a deterministic low-risk action. Do not send it
    # to an LLM that may unnecessarily ask for confirmation or reinterpret the request.
    from .alarm import is_alarm_delete_request, parse_alarm_text
    power_kind = power_request_kind(text)
    if power_kind:
        return {"status": "ok", "provider": "phone-skills", "model": "power_intent",
                "result": {"route": power_kind, "reply": "需要确认后执行电源操作。"}}
    if is_alarm_delete_request(text):
        return {"status": "ok", "provider": "phone-skills", "model": "alarm_mutation_parser",
                "result": {"route": "alarm_delete", "reply": "正在删除闹钟。"}}
    if parse_alarm_text(text, _dt.datetime.now()).get("status") == "ready":
        return {"status": "ok", "provider": "phone-skills", "model": "alarm_parser",
                "result": {"route": "alarm", "reply": "正在设置闹钟。"}}
    if is_device_info_request(text):
        return {"status": "ok", "provider": "phone-skills", "model": "settings_device_info",
                "result": {"route": "skill", "reply": "正在打开关于手机页面并确认 Android 版本信息。",
                           "skill_id": "settings_device_info"}}
    provider = os.environ.get("PHONE_AI_LLM", "auto").lower()
    if provider in {"auto", "gemini"} and (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        answer = gemini_route(text)
        if answer.get("status") == "ok" or provider != "auto":
            return answer
    if provider in {"auto", "deepseek"} and os.environ.get("DEEPSEEK_API_KEY"):
        answer = openai_compatible_route(text, "deepseek", "https://api.deepseek.com/chat/completions",
                                         os.environ.get("PHONE_AI_DEEPSEEK_MODEL", "deepseek-flash"),
                                         os.environ["DEEPSEEK_API_KEY"])
        if answer.get("status") == "ok" or provider != "auto":
            return answer
    if provider in {"auto", "ollama"}:
        return ollama_route(text)
    return {"status": "disabled", "provider": provider}


def maybe_execute_alarm(text: str, llm: dict[str, Any]) -> dict[str, Any]:
    """Execute only a deterministic alarm request explicitly classified as alarm."""
    parsed = llm.get("result", {}) if isinstance(llm, dict) else {}
    if parsed.get("route") != "alarm" or os.environ.get("PHONE_AI_EXECUTE_ALARM", "1") != "1":
        return {"status": "not_executed", "reason": "not_explicit_alarm_or_execution_disabled"}
    from .alarm import is_alarm_delete_request, parse_alarm_text
    if is_alarm_delete_request(text):
        return {"status": "rejected", "reason": "delete_request_cannot_use_set_alarm"}
    alarm = parse_alarm_text(text, _dt.datetime.now())
    if alarm.get("status") != "ready":
        # Unresolved router guesses are not an execution authorization.
        return {"status": "rejected", "reason": alarm.get("reason", "ambiguous")}
    # Return a declarative action to the originating phone. The phone is not
    # necessarily reachable by USB when it uploads audio over the Internet.
    result = {"status": "pending_phone", "hour": alarm["hour"],
              "minute": alarm["minute"], "label": alarm["label"]}
    for key in ("day", "relative_minutes"):
        if key in alarm:
            result[key] = alarm[key]
    return result


def maybe_execute_alarm_delete(text: str, llm: dict[str, Any]) -> dict[str, Any]:
    """Return a declarative delete request for the originating phone.

    The phone performs the provider deletion and reports its own receipt. The
    PC never turns a delete request into ACTION_SET_ALARM.
    """
    from .alarm import parse_alarm_text
    parsed = llm.get("result", {}) if isinstance(llm, dict) else {}
    if parsed.get("route") != "alarm_delete":
        return {"status": "not_executed", "reason": "not_alarm_delete"}
    target = parse_alarm_text(text, _dt.datetime.now())
    if target.get("status") != "delete_requested":
        return {"status": "rejected", "reason": "delete_intent_not_confirmed"}
    if target.get("needs_time_resolution") or "hour" not in target:
        return {"status": "rejected", "reason": "alarm_context_resolution_required"}
    result = {"status": "pending_phone_delete", "target": target.get("target", "latest"),
              "label": "Phone AI"}
    for key in ("hour", "minute", "day"):
        if key in target:
            result[key] = target[key]
    return result


def is_device_info_request(text: str) -> bool:
    """Recognize only a small, explicit set of read-only Settings requests."""
    simplified = str.maketrans({
        "幫": "帮", "開": "开", "關": "关", "於": "于", "機": "机",
        "請": "请", "讀": "读", "閱": "阅", "頁": "页", "見": "见",
    })
    value = re.sub(r"\s+", "", text.translate(simplified)).casefold().strip("，。！？!?、")
    targets = ("关于手机", "手机信息", "手机版本", "安卓版本", "android版本")
    allowed = set(targets)
    for target in targets:
        for prefix in ("打开", "查看", "读取", "帮我打开", "帮我查看", "请打开", "请查看", "帮我读取"):
            allowed.add(prefix + target)
            allowed.add(prefix + "一下" + target)
    return value in allowed


def execute_phone_action(text: str, llm: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    parsed = llm.get("result", {}) if isinstance(llm, dict) else {}
    route = parsed.get("route")
    if route in {"shutdown", "reboot", "bootloader"}:
        return {"status": "pending_confirmation", "kind": route, "reason": "explicit_confirmation_required"}
    if route in {"shutdown_confirmed", "reboot_confirmed", "bootloader_confirmed"}:
        # Power mutations have no safe ADB fallback. They must use the
        # authenticated outbound relay and the Android worker's fixed action.
        power_kind = route.removesuffix("_confirmed")
        from .phone_relay import create_device, relay_enabled
        if not relay_enabled():
            return {"status": "error", "kind": power_kind, "error_type": "phone_relay_offline",
                    "reason": "power_action_requires_phone_relay"}
        try:
            serial = os.environ.get("PHONE_AI_SERIAL", "demo-device")
            device = create_device(serial)
            relay = getattr(device, "relay", None)
            if relay is None:
                return {"status": "error", "kind": power_kind, "error_type": "phone_relay_offline",
                        "reason": "power_action_requires_phone_relay"}
            value = relay.request("power", {"action": power_kind}, timeout=12)
            return {"status": "succeeded", "kind": power_kind, "value": value}
        except Exception as exc:
            return {"status": "error", "kind": power_kind, "error_type": type(exc).__name__,
                    "reason": str(exc)[:240]}
    # This guard is deliberately before the route dispatch: a provider model
    # may still return route=alarm for a deletion, but it must never create one.
    from .alarm import is_alarm_delete_request
    if is_alarm_delete_request(text):
        result = maybe_execute_alarm_delete(text, {**llm, "result": {**parsed, "route": "alarm_delete"}})
        result["kind"] = "alarm_delete"
        return result
    if route == "alarm":
        result = maybe_execute_alarm(text, llm)
        result["kind"] = "alarm"
        return result
    if route == "alarm_delete":
        result = maybe_execute_alarm_delete(text, llm)
        result["kind"] = "alarm_delete"
        return result
    if route == "amap_ai":
        try:
            from .agent_gateway import AgentAdb
            serial = os.environ.get("PHONE_AI_SERIAL", "demo-device")
            from .phone_relay import create_device
            device = create_device(serial, adb_factory=AgentAdb)
            result = device.act({"action": "amap_handoff", "text": text})
            result["status"] = "succeeded"
            result["kind"] = "amap_ai"
            return result
        except Exception as exc:
            return {"status": "error", "kind": "amap_ai", "error_type": type(exc).__name__}
    if route == "app_launch":
        try:
            serial = os.environ.get("PHONE_AI_SERIAL", "demo-device")
            # app_launch is a planner vocabulary item, not an execution
            # shortcut. Every app request must enter observe -> plan -> act ->
            # verify and leave a learning checkpoint on the first run.
            from .agent_runner import run_agent
            result = run_agent(text, serial, max_steps=24)
            result["kind"] = "app_task"
            return result
        except Exception as exc:
            return {"status": "error", "kind": "app_task", "error_type": type(exc).__name__,
                    "reason": str(exc)[:240]}
    if route == "app_task":
        try:
            serial = os.environ.get("PHONE_AI_SERIAL", "demo-device")
            # The general Agent owns the complete first-run workflow. Do not
            # launch the model-suggested package here: that bypasses its
            # installed-app discovery, observation, verification and learning.
            from .agent_runner import run_agent
            result = run_agent(text, serial, max_steps=24)
            result["status"] = "succeeded" if result.get("status") == "succeeded" else result.get("status", "error")
            result["kind"] = "app_task"
            return result
        except Exception as exc:
            return {"status": "error", "kind": "app_task", "error_type": type(exc).__name__, "reason": str(exc)[:240]}
    if route != "skill":
        return {"status": "not_requested"}
    # Model output alone never grants permission to select an arbitrary phone skill.
    if (llm.get("provider") != "phone-skills" or llm.get("model") != "settings_device_info"
            or parsed.get("skill_id") != "settings_device_info" or not is_device_info_request(text)):
        return {"status": "rejected", "reason": "skill_not_in_read_only_allowlist"}
    from .adb_readonly import AdbReadonlyAdapter
    from .phone_relay import relay_enabled
    if relay_enabled():
        from .agent_runner import run_agent
        result = run_agent(text, os.environ.get("PHONE_AI_SERIAL", "demo-device"))
        result["kind"] = "app_task"
        return result
    from .model import read_json, validate
    from .runtime import Runner
    skill_path = Path(__file__).resolve().parents[1] / "examples" / "settings_device_info.json"
    skill = validate(read_json(skill_path))
    serial = os.environ.get("PHONE_AI_SERIAL", "demo-device")
    run = Runner(AdbReadonlyAdapter(serial), Path(output_dir) / "phone_skill_runs").run(skill)
    return {"status": run.get("status"), "skill_id": skill["id"], "run_id": run.get("run_id"),
            "verification": run.get("verification"), "triage": run.get("triage")}


def gemini_route(text: str) -> dict[str, Any]:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    try:
        model = gemini_model()
    except SkillError as exc:
        return {"status": "disabled", "provider": "gemini", "reason": str(exc)}
    prompt = ROUTE_PROMPT + text
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {
        "temperature": 0.1, "maxOutputTokens": 2048,
        "thinkingConfig": {"thinkingLevel": "low"}, "responseMimeType": "application/json"}}).encode("utf-8")
    request = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key}, method="POST")
    try:
        with urllib.request.urlopen(
                request, timeout=float(os.environ.get("PHONE_AI_LLM_TIMEOUT", "20"))) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw = str(payload["candidates"][0]["content"]["parts"][0].get("text", "")).strip()
        return parse_reply(raw, "gemini", model)
    except Exception as exc:
        return {"status": "error", "provider": "gemini", "error_type": type(exc).__name__}


def openai_compatible_route(text: str, provider: str, url: str, model: str, key: str,
                            context: str = "") -> dict[str, Any]:
    prompt = ROUTE_PROMPT
    if context:
        prompt += "同一会话最近上下文（仅帮助理解追问，不要当成当前命令）：\n" + context[-4000:] + "\n当前输入："
    prompt += text
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0.1, "max_tokens": 1024}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
    try:
        with urllib.request.urlopen(
                request, timeout=float(os.environ.get("PHONE_AI_LLM_TIMEOUT", "20"))) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw = str(payload["choices"][0]["message"].get("content", "")).strip()
        return parse_reply(raw, provider, model)
    except Exception as exc:
        return {"status": "error", "provider": provider, "error_type": type(exc).__name__}


def ollama_route(text: str) -> dict[str, Any]:
    """Ask the configured local Ollama model for a structured next step."""
    model = os.environ.get("PHONE_AI_OLLAMA_MODEL", "qwen3-vl:30b")
    prompt = ROUTE_PROMPT + text
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "think": False,
                       "keep_alive": 0,
                       "options": {"num_predict": 512}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434") + "/api/generate",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw = str(payload.get("response", "")).strip()
        return parse_reply(raw, "ollama", model)
    except Exception as exc:
        return {"status": "error", "provider": "ollama", "error_type": type(exc).__name__}


def make_handler(sessions: AudioSessions, rhythm_service: RhythmService | None = None, relay=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PhoneAudioBridge/1.0"

        def _json(self, code: int, value: dict[str, Any]):
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health":
                self._json(200, {"ok": True, "service": "phone-audio-bridge"})
            elif self.path == "/v1/rhythm/plan" and rhythm_service is not None:
                self._json(200, rhythm_service.plan())
            elif self.path == "/v1/app/version":
                self._json(200, {"version": "0.4.4", "version_code": 2026100704,
                                 "download": "/v1/app/phone-ai.apk"})
            elif self.path == "/v1/app/phone-ai.apk":
                apk = Path(__file__).resolve().parents[2] / "phone-assistant" / "release" / "PhoneAI-0.4.4-signed.apk"
                if not apk.is_file():
                    self._json(404, {"error": "apk_not_built"})
                    return
                raw = apk.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", 'attachment; filename="PhoneAI-0.4.4-signed.apk"')
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            else:
                self._json(404, {"error": "not_found"})

        def do_POST(self):
            if self.path == "/v1/phone/action-result":
                if relay is None or not relay.authorized(self.headers.get("Authorization", "").removeprefix("Bearer ")):
                    self._json(401, {"error": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError("invalid content length")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    self._json(200, sessions.phone_action_result(payload["session_id"], payload["status"]))
                except (KeyError, ValueError, OSError) as exc:
                    self._json(400, {"error": str(exc)})
                return
            if self.path.startswith("/v1/phone/relay/"):
                if relay is None:
                    self._json(503, {"error": "phone_relay_not_configured"})
                    return
                if not relay.authorized(self.headers.get("Authorization", "").removeprefix("Bearer ")):
                    self._json(401, {"error": "unauthorized"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 6_000_000:
                        raise ValueError("invalid content length")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if self.path.endswith("/poll"):
                        result = relay.poll(payload["device_id"])
                    elif self.path.endswith("/result"):
                        result = relay.complete(payload["device_id"], payload["id"], payload["result"])
                    elif self.path.endswith("/status"):
                        result = relay.status()
                    elif self.path.endswith("/request"):
                        result = relay.request(payload["op"], payload.get("payload", {}))
                    else:
                        self._json(404, {"error": "not_found"})
                        return
                    self._json(200, result)
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
                return
            if self.path == "/v1/rhythm/samples" and rhythm_service is not None:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 65536:
                        raise ValueError("invalid content length")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    self._json(200, rhythm_service.store.update(payload))
                except (ValueError, UnicodeError, TypeError) as exc:
                    self._json(400, {"error": str(exc)})
                return
            if self.path not in ("/v1/audio/chunk", "/v1/audio/finish"):
                self._json(404, {"error": "not_found"})
                return
            session_id = self.headers.get("X-Session-Id", "")
            try:
                origin = self.headers.get("X-Command-Origin", "phone-agent")
                if origin != "phone-agent":
                    raise ValueError("invalid_command_origin")
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 2_000_000:
                    raise ValueError("invalid content length")
                payload = self.rfile.read(length)
                if self.path.endswith("/chunk"):
                    result = sessions.append(session_id, payload)
                else:
                    conversation_id = self.headers.get("X-Conversation-Id", "")
                    if len(conversation_id) > 100 or not re.fullmatch(r"[A-Za-z0-9_-]*", conversation_id):
                        raise ValueError("invalid_conversation_id")
                    result = sessions.finish(session_id, conversation_id)
                self._json(200, result)
            except KeyError:
                self._json(404, {"error": "unknown_session", "session_id": session_id})
            except (ValueError, OSError) as exc:
                self._json(400, {"error": str(exc)})

        def log_message(self, fmt, *args):
            print("[audio-bridge] " + (fmt % args), flush=True)

    return Handler


def serve(host: str = "127.0.0.1", port: int = 18880, output_dir: str = "audio_sessions"):
    sessions = AudioSessions(Path(output_dir))
    data_root = Path(os.environ.get("PHONE_AI_RHYTHM_DATA", str(Path(os.environ.get("LOCALAPPDATA", ".")) / "PhoneSkills" / "rhythm.json")))
    calendar_path = Path(os.environ.get("PHONE_AI_CALENDAR_ICS", r"calendar/tasks.ics"))
    rhythm = RhythmService(RhythmStore(data_root), calendar_path,
                           sleep_cache=CorosSleepCache(data_root.with_name("coros-sleep.json")))
    from .phone_relay import activate_relay
    relay = activate_relay()
    server = ThreadingHTTPServer((host, port), make_handler(sessions, rhythm, relay))
    print(json.dumps({"listening": f"http://{host}:{port}", "output_dir": str(Path(output_dir).resolve())}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv=None):
    p = argparse.ArgumentParser(description="Phone AI local PCM audio bridge")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=18880)
    p.add_argument("--output-dir", default="audio_sessions")
    args = p.parse_args(argv)
    serve(args.host, args.port, args.output_dir)


if __name__ == "__main__":
    main()
