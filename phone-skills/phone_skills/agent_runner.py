"""DeepSeek planner over the bounded phone-agent tool set."""
from __future__ import annotations

import json
import os
import time
import uuid
import urllib.request
from pathlib import Path
from typing import Any

from .agent_gateway import AgentAdb, _assert_state, _node_matches
from .model import SkillError, require
from .storage import atomic_json
from .learning import find_replay


SYSTEM = """你是手机操作规划器。只能输出一个JSON对象，不要Markdown：
{"done":true或false,"message":"给用户的简短中文消息","action":null或{"action":"observe|launch_app|click|long_click|type_text|back|scroll|wait|wait_until|assert|screenshot|deeplink",...},"final_expect":null或{"app":"包名","activity":"可选","present":[{"text":"目标结果"}],"absent":[]}
只能使用这些动作。click必须使用当前控件树中唯一的resource_id/text/content_desc组合。
long_click用同样的target格式，只能选择long_clickable=true的控件。编辑、选择、删除等入口可能通过长按打开；不要把单击等同长按。单击进入详情或没有出现目标操作时，应根据新页面和历史重新判断，可返回列表并尝试长按，不要反复点击已失败或已消失的控件。删除后必须回到列表并同时验证列表标志存在和指定目标不存在，不能用详情页上没有12:00作为删除证据。
type_text必须使用当前控件树中唯一且focused=true的EditText目标，格式为{"action":"type_text","target":{...},"text":"...","clear":false}；只允许安全ASCII文本，不要输入密码、验证码或中文。
每次只规划一步；执行结果会在下一轮提供。不要操作支付、验证码、登录、授权、删除文件或账号、恢复出厂等敏感页面。用户明确要求删除闹钟时，允许在时钟应用中查找并删除指定闹钟，不能设置新闹钟；必须区分12:00和00:00，不能删除不匹配的闹钟。完成后必须观察闹钟列表确认目标项不存在，不能仅凭点击删除或打开时钟报告成功。
不能生成shell、ADB命令、坐标点击或任意代码。assert只能使用app/present/absent结构化条件；screenshot只能取证，不能授权坐标点击；deeplink只能使用http/https且指定已安装包名。若任务包含“看、查询、包裹、物流、订单、搜索、播放、发送”等后续目标，打开应用只是中间步骤，必须继续操作到目标页面并看到结果后才能done=true；不能把仅打开应用当作完成。
任务完成时必须done=true、action=null，并提供非空final_expect。final_expect只能使用app/activity/present/absent，且必须包含activity或present/absent结果条件；它必须描述用户真正要看到的结果，不能只写前台应用。没有final_expect时不得报告完成。present中的文字必须逐字复制当前state.nodes里的可见结果控件，不要概括、拼接或猜测隐藏标题；选择足以证明目标的少量结果条件，不要枚举无关信息。"""


def _plan(text: str, state: dict[str, Any], history: list[dict[str, Any]], apps: list[str] | None = None) -> dict[str, Any]:
    key = os.environ.get("DEEPSEEK_API_KEY")
    require(key, "missing_deepseek_key")
    body = json.dumps({"model": os.environ.get("PHONE_AI_DEEPSEEK_MODEL", "deepseek-chat"),
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": json.dumps(
                                    {"task": text, "state": _planner_state(state, apps or []),
                                     "history": _planner_history(history)},
                                        ensure_ascii=False)}],
                       "temperature": 0.1, "max_tokens": 512,
                       "response_format": {"type": "json_object"}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))
    raw = payload["choices"][0]["message"]["content"]
    result = json.loads(raw)
    require(isinstance(result, dict) and set(result) <= {"done", "message", "action", "final_expect"}
            and {"done", "message", "action"} <= set(result), "invalid planner response")
    require(type(result["done"]) is bool and isinstance(result["message"], str), "invalid planner fields")
    final_expect = result.get("final_expect")
    if final_expect is not None:
        require(_valid_expect(final_expect), "invalid final_expect")
    if result["done"] or result["action"] is None:
        require(_valid_expect(final_expect), "missing final_expect")
    if result["action"] is not None:
        require(isinstance(result["action"], dict) and result["action"].get("action") in
                {"observe", "launch_app", "click", "long_click", "type_text", "back", "scroll", "wait", "wait_until", "assert", "screenshot", "deeplink"},
                "unsupported planner action")
    return result


DEFAULT_MAX_STEPS = 24
MAX_MAX_STEPS = 32
MAX_GOAL_REVIEWS = 2


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _task_root() -> Path:
    configured = os.environ.get("PHONE_AI_TASK_DIR")
    return Path(configured) if configured else _project_root() / "learning" / "tasks"


def _learning_root() -> Path:
    configured = os.environ.get("PHONE_AI_LEARNING_DIR")
    return Path(configured) if configured else _project_root() / "learning"


def _checkpoint_path(text: str) -> Path:
    root = _task_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / (f"{int(time.time() * 1000)}-{uuid.uuid4().hex}.json")


def _write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    atomic_json(path, payload)


def _valid_expect(expect: Any) -> bool:
    if not isinstance(expect, dict) or not expect or set(expect) - {"app", "activity", "present", "absent"}:
        return False
    # A foreground package alone is not proof of the user's goal. Require a
    # result-level signal (activity or present/absent selectors).
    if not any(expect.get(key) for key in ("activity", "present", "absent")):
        return False
    if "app" in expect and (not isinstance(expect["app"], str) or not expect["app"].strip()):
        return False
    if "activity" in expect and not isinstance(expect["activity"], str):
        return False
    for key in ("present", "absent"):
        if key in expect and (not isinstance(expect[key], list)
                              or not all(isinstance(item, dict) and bool(item)
                                         and not set(item) - {"resource_id", "text", "content_desc", "class_name"}
                                         for item in expect[key])):
            return False
    return True


def _goal_assertion(state: dict[str, Any], expect: Any) -> dict[str, Any]:
    """Evaluate user-goal evidence, never just the foreground package."""
    if not _valid_expect(expect) or not isinstance(state, dict) or state.get("blocked"):
        return {"verified": False,
                "evidence": {"app": state.get("app", "") if isinstance(state, dict) else ""},
                "reason": "invalid_or_blocked"}
    try:
        assertion = _assert_state(state, expect)
    except SkillError:
        return {"verified": False,
                "evidence": {"app": state.get("app", "")},
                "reason": "invalid_expectation"}
    verified = bool(assertion.get("verified"))
    if expect.get("activity") and state.get("activity") != expect["activity"]:
        verified = False
    evidence = dict(assertion.get("evidence", {}))
    if not verified:
        nodes = [node for node in state.get("nodes", []) if node.get("visible", True)]
        evidence["missing_present"] = [selector for selector in expect.get("present", [])
            if not any(_node_matches(node, selector) for node in nodes)]
        evidence["unexpected_present"] = [selector for selector in expect.get("absent", [])
            if any(_node_matches(node, selector) for node in nodes)]
        evidence["activity"] = state.get("activity", "")
    return {"verified": verified,
            "evidence": evidence,
            "reason": assertion.get("reason", "")}


def _replay_state_holds(state: dict[str, Any], expect: dict[str, Any]) -> bool:
    """Check the structured evidence attached to a reviewed replay step."""
    if not isinstance(state, dict) or not isinstance(expect, dict):
        return False
    if expect.get("app") and state.get("app") != expect["app"]:
        return False
    if expect.get("activity") and state.get("activity") != expect["activity"]:
        return False
    nodes = [node for node in state.get("nodes", []) if node.get("visible", True)]

    def matches(node: dict[str, Any], selector: dict[str, Any]) -> bool:
        if not isinstance(selector, dict) or not selector:
            return False
        return all(str(node.get(key, "")).strip().casefold() == str(value).strip().casefold()
                   for key, value in selector.items())

    return (all(any(matches(node, selector) for node in nodes) for selector in expect.get("present", []))
            and all(not any(matches(node, selector) for node in nodes) for selector in expect.get("absent", [])))


def _replay_action(skill: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    kind = step.get("action")
    if kind == "launch":
        return {"action": "launch_app", "package": step.get("args", {}).get("package", skill["app"])}
    if kind == "tap":
        selectors = skill.get("selectors", {}).get(step.get("target"), [])
        require(len(selectors) == 1, "replay_target_not_unique")
        return {"action": "click", "target": selectors[0]}
    if kind == "type":
        selectors = skill.get("selectors", {}).get(step.get("target"), [])
        require(len(selectors) == 1, "replay_target_not_unique")
        args = step.get("args", {})
        return {"action": "type_text", "target": selectors[0], "text": args.get("text", ""),
                "clear": bool(args.get("clear", False))}
    if kind == "wait":
        return {"action": "wait", "seconds": 1}
    raise SkillError("replay_action_not_allowed")


def _run_replay(device: AgentAdb, skill: dict[str, Any], state: dict[str, Any],
                checkpoint: Path, checkpoint_data: dict[str, Any]) -> dict[str, Any] | None:
    """Run a reviewed replay through the same observe-act-verify boundary.

    ``None`` means the replay was not applicable before dispatch, so the normal
    planner may take over. Once an action may have been dispatched, failure is
    terminal to avoid repeating a side effect.
    """
    traces: list[dict[str, Any]] = []
    dispatched = False
    for index, step in enumerate(skill.get("steps", []), 1):
        trace = {"step": index, "states": ["planning"], "action": None,
                 "before": state, "after": None, "verified": False, "error_type": None}
        traces.append(trace)
        if step.get("action") != "launch" and not _replay_state_holds(state, step.get("before", {})):
            if not dispatched:
                return None
            trace["states"].append("failed")
            result = {"status": "action_failed", "phase": "failed", "kind": "replay",
                      "steps": index, "message": "回放前页面与已学习状态不一致。", "state": state,
                      "steps_trace": traces}
            checkpoint_data.update(result); _write_checkpoint(checkpoint, checkpoint_data)
            return result
        action = _replay_action(skill, step)
        trace["action"] = action
        trace["states"].append("executing")
        checked = device.act_checked(action, before=state)
        trace["states"].append("verifying")
        trace["after"] = checked.get("after") if isinstance(checked, dict) else None
        trace["verified"] = bool(checked.get("verified")) if isinstance(checked, dict) else False
        if isinstance(checked, dict) and checked.get("after") is not None:
            dispatched = True
        after = checked.get("after") if isinstance(checked, dict) else None
        if not trace["verified"] or not _replay_state_holds(after or {}, step.get("expect", {})):
            trace["error_type"] = (checked.get("error_type", "replay_postcondition_failed")
                                    if isinstance(checked, dict) else "replay_action_failed")
            trace["states"].append("failed")
            if not dispatched:
                return None
            result = {"status": "action_failed", "phase": "failed", "kind": "replay",
                      "steps": index, "message": "学习回放未通过页面验证，已停止。", "state": after or state,
                      "last_action": checked, "steps_trace": traces}
            checkpoint_data.update(result); _write_checkpoint(checkpoint, checkpoint_data)
            return result
        state = after
        checkpoint_data.update({"phase": "planning", "state": state, "steps_trace": traces})
        _write_checkpoint(checkpoint, checkpoint_data)
    if not _replay_state_holds(state, skill.get("final_expect", {})):
        return None if not dispatched else {"status": "action_failed", "phase": "failed", "kind": "replay",
            "steps": len(traces), "message": "学习回放最终页面未通过验证。", "state": state,
            "error_type": "goal_assertion_failed", "goal_verified": False,
            "goal_expect": skill.get("final_expect"), "steps_trace": traces}
    goal = _goal_assertion(state, skill.get("final_expect"))
    result = {"status": "succeeded", "phase": "completed", "kind": "replay",
              "steps": len(traces), "message": "已按已验证的操作轨迹完成。", "state": state,
              "steps_trace": traces, "goal_verified": bool(goal["verified"]),
              "goal_expect": skill.get("final_expect"), "goal_evidence": goal.get("evidence", {})}
    checkpoint_data.update(result); _write_checkpoint(checkpoint, checkpoint_data)
    return result


def run_agent(text: str, serial: str, max_steps: int = DEFAULT_MAX_STEPS) -> dict[str, Any]:
    require(isinstance(text, str) and 0 < len(text.strip()) <= 2000, "invalid task")
    require(1 <= max_steps <= MAX_MAX_STEPS, "invalid max_steps")
    history: list[dict[str, Any]] = []
    visual_history: list[dict[str, Any]] = []
    visual_chat_confirmed = False
    visual_text_entered = False
    transient_retries = 0
    goal_reviews = 0
    transient_errors = {"state_unchanged", "target_not_found", "target_not_unique", "foreground_app_mismatch"}
    checkpoint = _checkpoint_path(text)
    steps_trace: list[dict[str, Any]] = []
    checkpoint_data: dict[str, Any] = {"schema_version": 1, "task": text.strip()[:2000], "serial": serial,
        "phase": "observing", "state": None, "steps_trace": steps_trace, "history": history,
        "learning_mode": "planner", "created_at": int(time.time())}
    _write_checkpoint(checkpoint, checkpoint_data)

    def fail_before_planning(phase: str, exc: Exception) -> dict[str, Any]:
        """Persist an actionable learning checkpoint when setup cannot finish."""
        error_text = str(exc)[:240]
        accessibility_unavailable = "accessibility_service_not_connected" in error_text
        result = {
            "status": "retryable_error" if accessibility_unavailable else "error",
            "phase": phase,
            "message": "手机 Agent 在" + {
                "observing": "观察页面",
                "discovering_apps": "发现应用",
            }.get(phase, "准备") + "阶段失败。",
            "error_type": "accessibility_service_not_connected" if accessibility_unavailable else type(exc).__name__,
            "reason": error_text,
            "retryable": accessibility_unavailable,
            "learning_mode": "infrastructure" if accessibility_unavailable else "planner",
            "steps_trace": steps_trace,
        }
        if accessibility_unavailable:
            # A missing system service is not a failed user task and must not
            # become a candidate for replay or self-learning.
            try:
                checkpoint.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            result["learning_task_file"] = str(checkpoint)
            checkpoint_data.update(result)
            _write_checkpoint(checkpoint, checkpoint_data)
        return result

    try:
        from .phone_relay import create_device
        device = create_device(serial, adb_factory=AgentAdb)
        state = device.observe()
    except Exception as exc:
        return fail_before_planning("observing", exc)
    checkpoint_data.update({"phase": "discovering_apps", "state": state})
    _write_checkpoint(checkpoint, checkpoint_data)
    try:
        apps = device.installed_apps()
    except Exception as exc:
        return fail_before_planning("discovering_apps", exc)
    checkpoint_data.update({"phase": "planning", "state": state})
    _write_checkpoint(checkpoint, checkpoint_data)
    learning_root = _learning_root()
    replay = find_replay(learning_root, text)
    if replay:
        replay_result = _run_replay(device, replay, state, checkpoint, checkpoint_data)
        if replay_result is not None:
            replay_result.setdefault("learning_mode", "replay")
            replay_result["learning_task_file"] = str(checkpoint)
            return replay_result
    for step in range(max_steps):
        trace = {"step": step + 1, "states": ["planning"], "action": None, "before": state,
                 "after": None, "verified": False, "error_type": None}
        steps_trace.append(trace)
        if state.get("blocked") == "ui_unavailable":
            from .visual_agent import visual_plan
            visual = visual_plan(text, serial, visual_history)
            visual_history.append({"step": step + 1, "action": visual})
            if visual["action"] == "finish":
                trace["states"].append("verifying")
                goal_expect = visual.get("final_expect")
                goal = _goal_assertion(state, goal_expect)
                if not goal["verified"]:
                    checkpoint_data.update({"phase": "failed", "status": "failed", "message": "视觉流程缺少可观察证据",
                                            "error_type": "goal_assertion_required" if not _valid_expect(goal_expect) else "goal_assertion_failed",
                                            "goal_verified": False, "goal_expect": goal_expect,
                                            "goal_evidence": goal.get("evidence", {}),
                                            "state": state, "steps_trace": steps_trace, "history": history})
                    _write_checkpoint(checkpoint, checkpoint_data)
                    return {"status": "failed", "phase": "failed", "steps": step + 1,
                            "message": "视觉流程缺少目标验收证据", "error_type": checkpoint_data["error_type"],
                            "goal_verified": False, "goal_expect": goal_expect,
                            "goal_evidence": goal.get("evidence", {}), "state": state, "steps_trace": steps_trace}
                result = {"status": "succeeded", "phase": "completed", "steps": step + 1,
                          "message": visual.get("message", "视觉流程完成"), "state": state, "steps_trace": steps_trace,
                          "goal_verified": True, "goal_expect": goal_expect,
                          "goal_evidence": goal.get("evidence", {})}
                checkpoint_data.update(result); checkpoint_data["history"] = history; _write_checkpoint(checkpoint, checkpoint_data)
                return result
            action = dict(visual)
            kind = action.pop("action")
            if visual.get("confirmed_chat") == "好宝":
                visual_chat_confirmed = True
            if kind == "type_text":
                require(visual_chat_confirmed, "visual_chat_not_confirmed")
                visual_text_entered = True
            if kind == "tap" and ("好宝" in text or "小号" in text):
                require(not visual_text_entered or visual_chat_confirmed, "visual_chat_not_confirmed")
            if kind == "tap":
                action["action"] = "tap"
            elif kind == "type_text":
                action["action"] = "type_text"
            else:
                action["action"] = kind
            try:
                trace["states"].append("executing"); trace["action"] = action
                state = device.visual_act(action)
                trace["states"].append("verifying"); trace["after"] = state; trace["verified"] = not state.get("blocked")
            except SkillError as exc:
                trace.update({"states": trace["states"] + ["failed"], "error_type": type(exc).__name__})
                result = {"status": "rejected", "phase": "failed", "steps": step + 1,
                          "message": str(exc), "state": state, "steps_trace": steps_trace}
                checkpoint_data.update(result); checkpoint_data["history"] = history; _write_checkpoint(checkpoint, checkpoint_data)
                return result
            if state.get("blocked") not in {"ui_unavailable", ""}:
                return {"status": "blocked", "steps": step + 1,
                        "message": "遇到敏感或不可操作页面，已停止。", "state": state}
            checkpoint_data.update({"phase": "verifying", "state": state, "steps_trace": steps_trace, "history": history})
            _write_checkpoint(checkpoint, checkpoint_data)
            continue
        plan = _plan(text, state, history, apps)
        history.append({"step": step + 1, "plan": plan})
        trace["action"] = plan.get("action")
        if goal_reviews and plan.get("action") is not None:
            result = {"status": "failed", "phase": "failed", "steps": step + 1,
                      "message": "目标验收未通过，已停止，未重复执行手机动作。",
                      "error_type": "goal_assertion_failed", "goal_verified": False,
                      "state": state, "steps_trace": steps_trace}
            checkpoint_data.update(result); checkpoint_data["history"] = history; _write_checkpoint(checkpoint, checkpoint_data)
            return result
        if plan["done"] or plan["action"] is None:
            trace["states"].append("verifying")
            goal_expect = plan.get("final_expect")
            goal = _goal_assertion(state, goal_expect)
            if not goal["verified"]:
                error_type = "goal_assertion_required" if not _valid_expect(goal_expect) else "goal_assertion_failed"
                trace["error_type"] = error_type
                history[-1]["goal_verification"] = goal
                if (_valid_expect(goal_expect) and not state.get("blocked")
                        and state.get("app") == goal_expect.get("app", state.get("app"))
                        and goal_reviews < MAX_GOAL_REVIEWS and step + 1 < max_steps):
                    # Review evidence only; never repeat a possibly completed action.
                    goal_reviews += 1
                    history[-1]["review_instruction"] = (
                        "目标验收失败，不是执行成功。仅依据当前state可见结果复核final_expect；"
                        "missing_present不存在于当前可见页面，不可引用历史标题。"
                        "仅在用户目标确有可见证据时返回done=true、action=null；"
                        "此复核阶段不能再次执行手机动作。")
                    try:
                        state = device.observe()
                    except Exception as exc:
                        return fail_before_planning("observing", exc)
                    trace["states"].append("reviewing_goal")
                    checkpoint_data.update({"phase": "reviewing_goal", "state": state,
                                            "steps_trace": steps_trace, "history": history})
                    _write_checkpoint(checkpoint, checkpoint_data)
                    continue
                result = {"status": "failed", "phase": "failed", "steps": step + 1,
                          "message": "模型报告完成，但目标结果未通过验收。", "error_type": error_type,
                          "goal_verified": False, "goal_expect": goal_expect,
                          "goal_evidence": goal.get("evidence", {}), "state": state,
                          "steps_trace": steps_trace}
                checkpoint_data.update(result); checkpoint_data["history"] = history; _write_checkpoint(checkpoint, checkpoint_data)
                return result
            result = {"status": "succeeded", "phase": "completed", "steps": step + 1,
                      "message": plan["message"], "state": state, "steps_trace": steps_trace,
                      "goal_verified": True, "goal_expect": goal_expect,
                      "goal_evidence": goal.get("evidence", {}),
                      "learning_mode": "planner", "learning_task_file": str(checkpoint)}
            checkpoint_data.update(result); checkpoint_data["history"] = history; _write_checkpoint(checkpoint, checkpoint_data)
            from .learning import record_success
            record_success(learning_root, text, serial, result, history)
            from .learning import promote_candidates
            promote_candidates(learning_root)
            return result
        action = dict(plan["action"])
        # Accept the common planner spelling while keeping the executor's
        # canonical schema strict and explicit.
        if action.get("action") == "launch_app" and "package" not in action and "app" in action:
            action["package"] = action.pop("app")
        if action.get("action") in {"click", "long_click"} and "target" not in action:
            target = {key: action.pop(key) for key in ("resource_id", "text", "content_desc") if key in action and action[key]}
            if target:
                action["target"] = target
        if action.get("action") in {"click", "long_click", "type_text"} and "target" in action:
            target = action["target"]
            require(isinstance(target, dict), "invalid planner target")
            action["target"] = {key: target[key] for key in ("resource_id", "text", "content_desc")
                                 if key in target and target[key]}
        if action.get("action") == "type_text":
            alias = "input" if "input" in action else ("value" if "value" in action else None)
            entered_text = action.pop(alias) if alias else action.get("text", "")
            label = action.pop("text", "") if alias else ""
            if "target" not in action:
                target = {key: action.pop(key) for key in ("resource_id", "content_desc")
                          if key in action and action[key]}
                if label:
                    target["text"] = label
                if target:
                    action["target"] = target
            action.pop("value", None)
            action.pop("input", None)
            action["text"] = entered_text
            action.setdefault("clear", False)
        trace["states"].append("executing")
        checked = device.act_checked(action, before=state)
        trace["states"].append("verifying")
        trace["after"] = checked.get("after") if isinstance(checked, dict) else None
        trace["verified"] = bool(checked.get("verified")) if isinstance(checked, dict) else False
        history[-1]["result"] = checked
        if not isinstance(checked, dict) or not isinstance(checked.get("verified"), bool):
            result = {"status": "action_failed", "phase": "failed", "steps": step + 1,
                      "message": "动作结果无效，未确认执行成功。", "state": state,
                      "last_action": checked, "steps_trace": steps_trace}
            checkpoint_data.update(result); checkpoint_data["history"] = history; _write_checkpoint(checkpoint, checkpoint_data)
            return result
        if not checked["verified"]:
            error_type = checked.get("error_type", "verification_failed")
            messages = {
                "target_not_found": "没有找到目标控件",
                "target_not_unique": "目标控件不唯一",
                "state_unchanged": "动作已发送，但页面没有变化",
                "foreground_app_mismatch": "应用没有切换到前台",
                "bridge_unavailable": "手机桥接不可用",
                "phone_relay_offline": "手机远程执行连接离线，不需要连接 USB，请检查手机网络",
                "phone_relay_timeout": "指令未送达手机，本次未执行",
                "phone_relay_result_unknown": "手机已收到指令，但回执丢失；为避免重复操作，本次停止",
                "postcondition_blocked": "动作后页面被安全策略拦截",
            }
            if error_type in transient_errors and transient_retries < 2:
                transient_retries += 1
                # UI providers can publish a stale/partial tree for one frame.
                # Refresh before asking the planner for the next action instead
                # of reporting a permanent failure to the user.
                state = device.observe()
                history[-1]["retry"] = {"count": transient_retries, "error_type": error_type}
                trace["states"].append("retrying"); trace["error_type"] = error_type
                checkpoint_data.update({"phase": "retrying", "state": state, "steps_trace": steps_trace, "history": history})
                _write_checkpoint(checkpoint, checkpoint_data)
                continue
            result = {"status": "action_failed", "phase": "failed", "steps": step + 1,
                      "message": "动作未确认：" + messages.get(error_type, str(error_type)),
                      "state": checked.get("after") or state, "last_action": checked,
                      "steps_trace": steps_trace}
            trace["error_type"] = error_type
            checkpoint_data.update(result); checkpoint_data["history"] = history; _write_checkpoint(checkpoint, checkpoint_data)
            return result
        state = checked["after"]
        checkpoint_data.update({"phase": "planning", "state": state, "steps_trace": steps_trace, "history": history})
        _write_checkpoint(checkpoint, checkpoint_data)
        if state.get("blocked"):
            result = {"status": "blocked", "phase": "blocked", "steps": step + 1, "message": "遇到敏感或不可操作页面，已停止。", "state": state,
                      "last_action": checked, "steps_trace": steps_trace}
            checkpoint_data.update(result); _write_checkpoint(checkpoint, checkpoint_data)
            return result
    result = {"status": "step_limit", "phase": "failed", "steps": max_steps, "message": "操作步骤达到上限，已停止。", "state": state,
              "steps_trace": steps_trace}
    checkpoint_data.update(result); checkpoint_data["history"] = history; _write_checkpoint(checkpoint, checkpoint_data)
    return result


def _planner_state(state: dict[str, Any], apps: list[str] | None = None) -> dict[str, Any]:
    """Send only useful, redacted accessibility labels to the cloud planner."""
    nodes = []
    for node in state.get("nodes", []):
        if not node.get("visible") or not (node.get("text") or node.get("content_desc") or node.get("editable") or node.get("resource_id")):
            continue
        nodes.append({key: node[key] for key in
                      ("resource_id", "text", "content_desc", "class_name", "clickable", "long_clickable", "editable", "focused")
                      if key in node})
    result = {"app": state.get("app", ""), "blocked": state.get("blocked", ""), "nodes": nodes[:300]}
    if apps is not None:
        result["installed_apps"] = apps[:500]
    return result


def _planner_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep decisions and verification failures without resending full UI trees."""
    compact = []
    for item in history[-24:]:
        result = item.get("result") or {}
        entry = {key: item[key] for key in ("step", "plan", "retry", "goal_review") if key in item}
        entry["result"] = {key: result[key] for key in
            ("status", "verified", "error_type", "message") if key in result}
        if isinstance(result.get("after"), dict):
            entry["result"]["after_app"] = result["after"].get("app", "")
        compact.append(entry)
    return compact
