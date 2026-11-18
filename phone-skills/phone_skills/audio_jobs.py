"""Durable finish requests, independent of the lifetime of an HTTP client.

An interrupted running task is never replayed automatically: it may have made
calendar/device changes before the process died.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from .storage import atomic_json


class AudioJobs:
    WAIT_SECONDS = 180
    WAIT_REPLY = "这件事还在处理中，鲸鲸再潜一会儿～做好了就来叫你，不用重复布置。"

    def __init__(self, sessions):
        self.sessions = sessions
        self.root = sessions.output_dir / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.events = {}
        for path in self.root.glob("*.json"):
            state = json.loads(path.read_text(encoding="utf-8"))
            if state["status"] == "running":
                receipt = self._receipt(state["session_id"])
                if receipt is not None:
                    state.update(status="completed", result=receipt, finished_at=time.time())
                else:
                    state["status"] = "needs_reconciliation"
                atomic_json(path, state)
            elif state["status"] == "queued":
                self._launch(state["session_id"])

    def _path(self, session_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", session_id):
            raise ValueError("invalid session_id")
        return self.root / (session_id + ".json")

    def _launch(self, session_id):
        self.events[session_id] = threading.Event()
        threading.Thread(target=self._run, args=(session_id,), daemon=True).start()

    def _receipt(self, session_id):
        try:
            result = json.loads((self.sessions.output_dir / (session_id + ".json")).read_text(encoding="utf-8"))
            if result.get("session_id") == session_id and isinstance(result.get("reply"), str):
                return result
        except (OSError, ValueError, AttributeError):
            pass
        return None

    def _run(self, session_id):
        path = self._path(session_id)
        try:
            with self.lock:
                state = json.loads(path.read_text(encoding="utf-8"))
                if state["status"] != "queued":
                    return
                state["status"] = "running"
                atomic_json(path, state)
            # Restore only a job which had never started, including after boot.
            with self.sessions._lock:
                self.sessions._sessions.pop(session_id, None)
            self.sessions.append(session_id, path.with_suffix(".pcm").read_bytes())
            result = self.sessions.finish(session_id, state["conversation_id"])
            state.update(status="completed", result=result, finished_at=time.time())
        except Exception:
            # No blind retry, even if failure happened while saving a receipt.
            state = json.loads(path.read_text(encoding="utf-8"))
            state["status"] = "needs_reconciliation"
        finally:
            try:
                with self.lock:
                    current = json.loads(path.read_text(encoding="utf-8"))
                    for key in ("notify", "notice_acks"):
                        if key in current:
                            state[key] = current[key]
                    atomic_json(path, state)
            finally:
                self.events[session_id].set()

    def _notice(self, state, now):
        if not state.get("notify"):
            return None
        elapsed = now - state.get("created_at", now)
        status = state["status"]
        if status in ("queued", "running"):
            if elapsed < self.WAIT_SECONDS:
                return None
            phase, text, success = "waiting", self.WAIT_REPLY, False
        elif status == "needs_reconciliation":
            phase, text, success = "attention", "鲸鲸遇到中断了，这次结果还没核对清楚，先不重复执行。请回来看看。", False
        elif status == "completed":
            if not state.get("notify_fast") and state.get("finished_at", now) - state.get("created_at", now) < self.WAIT_SECONDS:
                return None
            result = state.get("result") or {}
            phase = "done"
            action = result.get("action") or {}
            success = action.get("status") not in ("error", "failed", "needs_reconciliation", "pending_phone", "pending_phone_delete")
            if any(isinstance(result.get(key), dict) and result[key].get("status") == "error"
                   for key in ("pc_agent", "llm", "asr")):
                success = False
            if (result.get("task_status") in {"partial", "needs_reconciliation"}
                    or (result.get("pc_agent") or {}).get("task_status") in {"partial", "needs_reconciliation"}):
                success = False
            reply = result.get("display_reply") or result.get("reply") or "这次没有收到完整结果，请回来核对。"
            if str(action.get("status", "")).startswith("pending_phone"):
                reply = "电脑结果已收回，手机操作尚未确认。请回来核对系统时钟，未自动重复操作。"
            text = ("鲸鲸回来啦，处理结果：\n" if success else "鲸鲸回来啦，这件事还需要你看看：\n") + reply
        else:
            return None
        if phase in state.get("notice_acks", []):
            return None
        return {"id": state["session_id"] + ":" + phase, "session_id": state["session_id"],
                "conversation_id": state.get("conversation_id", ""), "phase": phase,
                "text": text[:16000], "success": success, "terminal": phase != "waiting"}

    def notifications(self, acknowledgements=(), now=None):
        """Read an outbox; ACK only after the paired phone persisted the message.

        Terminal state supersedes an undelivered waiting message. Notifications
        contain text only and can never replay a calendar/device operation.
        """
        now = time.time() if now is None else now
        ack_ids = set(str(item) for item in acknowledgements if isinstance(item, str))
        notices, size = [], 0
        with self.lock:
            for path in sorted(self.root.glob("*.json")):
                state = json.loads(path.read_text(encoding="utf-8"))
                if not state.get("notify"):
                    continue
                acked = set(state.get("notice_acks", []))
                updated = acked | {phase for phase in ("waiting", "done", "attention")
                                   if state["session_id"] + ":" + phase in ack_ids}
                if updated != acked:
                    state["notice_acks"] = sorted(updated)
                    atomic_json(path, state)
                notice = self._notice(state, now)
                if notice:
                    encoded = len(json.dumps(notice, ensure_ascii=False).encode("utf-8"))
                    if size + encoded <= 58000 and len(notices) < 4:
                        notices.append(notice)
                        size += encoded
        return notices

    def finish(self, session_id, conversation_id="", wait=25, notify=False):
        path = self._path(session_id)
        with self.lock:
            if path.exists():
                state = json.loads(path.read_text(encoding="utf-8"))
                if state["conversation_id"] != conversation_id:
                    raise ValueError("session conversation conflict")
                if notify and not state.get("notify"):
                    state["notify"] = True
                    atomic_json(path, state)
            else:
                with self.sessions._lock:
                    item = self.sessions._sessions.get(session_id)
                    if item is None:
                        # Recover a receipt written before deployment/restart.
                        receipt = self._receipt(session_id)
                        if receipt is not None:
                            return receipt
                        raise KeyError(session_id)
                    pcm = path.with_suffix(".pcm")
                    temp = pcm.with_suffix(".pcm.tmp")
                    with temp.open("wb") as stream:
                        stream.write(bytes(item["data"]))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temp, pcm)
                state = {"session_id": session_id, "conversation_id": conversation_id,
                         "status": "queued", "created_at": time.time(), "notify": notify}
                atomic_json(path, state)
                self._launch(session_id)
            event = self.events.get(session_id)
        if event:
            event.wait(wait)
        state = json.loads(path.read_text(encoding="utf-8"))
        if state["status"] == "completed":
            result = dict(state["result"])
            notice = self._notice(state, time.time())
            if notice:
                result["notices"] = [notice]
            # Even an ACKed result remains identifiable to avoid a duplicate bubble.
            if state.get("notify") and state.get("finished_at", 0) - state.get("created_at", 0) >= self.WAIT_SECONDS:
                result["notification_id"] = session_id + ":done"
            return result
        if state["status"] == "needs_reconciliation":
            result = {"accepted": True, "session_id": session_id, "task_status": state["status"],
                      "reply": "任务执行中断，结果需要核对，未自动重复执行。请让电脑端核对这次任务记录。"}
            notice = self._notice(state, time.time())
            if notice:
                result["notices"] = [notice]
            if state.get("notify"):
                result["notification_id"] = session_id + ":attention"
            return result
        result = {"accepted": True, "session_id": session_id, "task_status": state["status"],
                  "reply": "任务已保存，电脑仍在处理；请稍候，无需重复布置。"}
        notice = self._notice(state, time.time())
        if notice:
            result["notices"] = [notice]
        return result
