"""Conservative trajectory storage and reviewable Skill replay promotion."""
from __future__ import annotations

import hashlib
import json
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from .model import validate
from .storage import atomic_json

_SENSITIVE_INTENT = re.compile(
    r"发送消息|发消息|删除|支付|付款|转账|登录|验证码|授权|密码|确认购买|下单|卸载|清空|send message|delete|pay|transfer|login|password|code",
    re.IGNORECASE,
)
_ACTION_FIELDS = {"action", "package", "target", "direction", "clear", "seconds", "text", "uri"}
_REPLAY_ACTIONS = {"launch_app", "click", "wait"}
_GENERIC_TOKENS = {
    "打开", "启动", "进入", "查看", "看看", "帮我", "请", "把", "一下", "页面", "应用",
    "open", "launch", "start", "please", "the", "app", "application",
}


def _normalize_task(task: str) -> str:
    return " ".join(str(task).strip().casefold().split())[:500]


def _task_tokens(task: str) -> set[str]:
    """Extract stable content words for alias matching without an LLM call."""
    normalized = _normalize_task(task)
    tokens = re.findall(r"[a-z0-9][a-z0-9_.-]*|[\u4e00-\u9fff]{2,}", normalized)
    return {token for token in tokens if token not in _GENERIC_TOKENS and len(token) > 1}


def _family_key(task: str, app: str, actions: list[dict[str, Any]]) -> str:
    launch = next((action for action in actions if action.get("action") == "launch_app"), None)
    package = str((launch or {}).get("package") or app or "")
    shape = ">".join(str(action.get("action", "")) for action in actions)
    return f"{package}|{shape}" if package else f"task|{shape}|{_normalize_task(task)}"


def _action_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    plan = item.get("plan", {}) if isinstance(item, dict) else {}
    action = plan.get("action") if isinstance(plan, dict) else None
    if not isinstance(action, dict):
        action = item.get("action") if isinstance(item, dict) else None
    if not isinstance(action, dict):
        return None
    return {key: deepcopy(value) for key, value in action.items() if key in _ACTION_FIELDS}


def _trajectory(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in history:
        action = _action_from_item(item)
        checked = item.get("result", {}) if isinstance(item, dict) else {}
        before = item.get("before") or checked.get("before")
        after = item.get("after") or checked.get("after")
        verified = item.get("verified") if isinstance(item, dict) else None
        if verified is None:
            verified = checked.get("verified") if isinstance(checked, dict) else False
        if action:
            result.append({"action": action, "before": before or {}, "after": after or {},
                           "verified": bool(verified)})
    return result


def _action_fingerprint(actions: list[dict[str, Any]]) -> str:
    return json.dumps(actions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_low_risk_task(task: str, actions: list[dict[str, Any]]) -> bool:
    # Replay eligibility is determined by the verified action contract, not by
    # a hand-written list of natural-language verbs. This lets the agent learn
    # new phrasings and apps while retaining a hard boundary around sensitive
    # requests and unsupported side effects.
    if _SENSITIVE_INTENT.search(task):
        return False
    # Waiting alone is not a learned operation. It is usually a planner
    # timeout/no-op and replaying it can mask the real task on the next run.
    return (bool(actions) and any(action.get("action") != "wait" for action in actions)
            and all(action.get("action") in _REPLAY_ACTIONS for action in actions))


def record_success(root: Path, task: str, serial: str, result: dict[str, Any], history: list[dict[str, Any]]) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    safe_task = " ".join(str(task).split())[:500]
    digest = hashlib.sha256((safe_task + "\0" + serial + "\0" + str(time.time_ns())).encode()).hexdigest()
    trajectory = _trajectory(history)
    actions = [item["action"] for item in trajectory]
    goal_expect = deepcopy(result.get("goal_expect"))
    goal_verified = bool(result.get("goal_verified")) and bool(goal_expect)
    payload = {"schema_version": 2, "status": "candidate", "task": safe_task,
               "normalized_task": _normalize_task(safe_task), "serial": serial,
               "app": result.get("state", {}).get("app", ""), "actions": actions,
               "family_key": _family_key(safe_task, result.get("state", {}).get("app", ""), actions),
               "task_tokens": sorted(_task_tokens(safe_task)),
               "trajectory": trajectory, "goal_verified": goal_verified,
               "goal_expect": goal_expect,
               "goal_evidence": deepcopy(result.get("goal_evidence", {})),
               "verified": bool(trajectory) and all(item["verified"] for item in trajectory) and goal_verified,
               "result": str(result.get("message", ""))[:500], "created_at": int(time.time())}
    path = root / (digest + ".json")
    atomic_json(path, payload)
    return path


def _selector_from_node(node: dict[str, Any]) -> dict[str, str] | None:
    for key in ("resource_id", "text", "content_desc"):
        if node.get(key):
            selector = {key: str(node[key])}
            if key != "text" and node.get("text"):
                selector["text"] = str(node["text"])
            return selector
    return None


def _expectation(state: dict[str, Any], fallback_app: str) -> dict[str, Any] | None:
    app = state.get("app") or fallback_app
    if not re.fullmatch(r"[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+", str(app)):
        return None
    present = []
    for node in state.get("nodes", [])[:20]:
        if isinstance(node, dict) and node.get("visible", True):
            selector = _selector_from_node(node)
            if selector:
                present.append(selector)
        if len(present) >= 3:
            break
    if not present:
        return {"app": app, "activity": "observed"}
    return {"app": app, "present": present}


def _to_skill(record: dict[str, Any], skill_id: str, intents: list[str] | None = None) -> dict[str, Any] | None:
    trajectory = record.get("trajectory", [])
    app = record.get("app", "")
    if not re.fullmatch(r"[A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+", str(app)):
        return None
    selectors: dict[str, list[dict[str, str]]] = {}
    steps = []
    for index, item in enumerate(trajectory, 1):
        if not item.get("verified"):
            return None
        action = item.get("action", {})
        kind = action.get("action")
        before = _expectation(item.get("before", {}), app)
        after = _expectation(item.get("after", {}), app)
        if not before or not after:
            return None
        step = {"id": f"step_{index}", "effect": "read_only" if kind in {"observe", "scroll"} else "idempotent",
                "before": before, "expect": after}
        if kind == "launch_app":
            step.update({"action": "launch", "args": {"package": action.get("package", app)}})
        elif kind == "click":
            target = action.get("target")
            if not isinstance(target, dict) or not target:
                return None
            bundle = f"target_{index}"
            selector = {key: str(value) for key, value in target.items() if key in {"resource_id", "text", "content_desc"} and value}
            if not selector:
                return None
            selectors[bundle] = [selector]
            step.update({"action": "tap", "target": bundle})
        elif kind == "type_text":
            target = action.get("target")
            value = action.get("text", "")
            if not isinstance(target, dict) or not isinstance(value, str) or not value.isascii() or not value:
                return None
            bundle = f"target_{index}"
            selector = {key: str(v) for key, v in target.items() if key in {"resource_id", "text", "content_desc"} and v}
            if not selector:
                return None
            selectors[bundle] = [selector]
            step.update({"action": "type", "target": bundle, "args": {"text": value, "clear": bool(action.get("clear", False))}})
        elif kind == "wait":
            step.update({"action": "wait", "args": {}})
        elif kind == "scroll":
            # The v1 replay schema has no scroll action. Do not silently turn
            # a learned scroll into a one-second wait and claim it replayed.
            return None
        else:
            return None
        steps.append(step)
    final_expect = record.get("goal_expect")
    if not final_expect:
        return None
    skill = {"schema_version": 1, "id": skill_id, "version": 1,
             "description": "从两次一致的低风险已验证轨迹生成，仍可人工审核",
             "app": app, "intents": list(dict.fromkeys((intents or [record.get("task", "")])[:20])), "params": {},
             "timeout_s": 30, "selectors": selectors, "steps": steps,
             "final_expect": final_expect}
    try:
        return validate(skill)
    except Exception:
        return None


def promote_candidates(root: Path, min_successes: int = 2) -> dict[str, Any]:
    root = Path(root)
    groups: dict[tuple[str, str, str], list[tuple[Path, dict[str, Any]]]] = {}
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        actions = data.get("actions", [])
        if (data.get("status") != "candidate" or not data.get("verified")
                or not data.get("goal_verified") or not data.get("goal_expect")
                or not data.get("goal_evidence")
                or not _is_low_risk_task(data.get("task", ""), actions)):
            continue
        family = data.get("family_key") or _family_key(data.get("task", ""), str(data.get("app", "")), actions)
        key = (family, str(data.get("app", "")), _action_fingerprint(actions))
        groups.setdefault(key, []).append((path, data))
    promoted = 0
    replay_dir = root / "replay"
    for key, entries in groups.items():
        if len(entries) < max(2, int(min_successes)):
            continue
        record = entries[0][1]
        skill_id = "replay_" + hashlib.sha256((key[0] + "\0" + key[1] + "\0" + key[2]).encode()).hexdigest()[:16]
        intents = [entry[1].get("task", "")[:200] for entry in entries if entry[1].get("task")]
        skill = _to_skill(record, skill_id, intents=intents)
        if not skill:
            continue
        replay_dir.mkdir(parents=True, exist_ok=True)
        replay_path = replay_dir / (skill_id + ".json")
        atomic_json(replay_path, skill)
        for path, data in entries:
            data["status"] = "promoted"
            data["replay_skill"] = skill_id
            atomic_json(path, data)
        promoted += 1
    return {"promoted": promoted, "groups": len(groups), "replay_dir": str(replay_dir)}


def find_replay(root: Path, task: str) -> dict[str, Any] | None:
    """Return a reviewed replay skill only for an exact normalized intent.

    Replay is deliberately exact: a near match can perform a valid action for
    the wrong target. Invalid or tampered files are ignored and never reach the
    device executor.
    """
    normalized = _normalize_task(task)
    if not normalized:
        return None
    root = Path(root) / "replay"
    if not root.is_dir():
        return None
    candidates: list[tuple[int, dict[str, Any]]] = []
    requested_tokens = _task_tokens(task)
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            skill = validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError):
            continue
        # Ignore legacy/test artifacts that contain no meaningful operation.
        # They must not short-circuit the planner even if they passed schema
        # validation before the no-op promotion guard was added.
        if not any(step.get("action") != "wait" for step in skill.get("steps", [])):
            continue
        intents = skill.get("intents", [])
        if any(_normalize_task(intent) == normalized for intent in intents):
            return skill
        # A learned family may have several natural-language aliases. A
        # replay is eligible only when the new request shares a content token
        # with an observed alias; generic verbs alone never select a skill.
        if requested_tokens:
            observed = set().union(*(_task_tokens(intent) for intent in intents)) if intents else set()
            overlap = len(requested_tokens & observed)
            # Only a request made entirely from already observed content
            # words can be an alias. Extra goal words such as "播放" must
            # return to the planner rather than replaying an "打开" skill.
            if overlap and requested_tokens <= observed:
                candidates.append((overlap, skill))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    return None
