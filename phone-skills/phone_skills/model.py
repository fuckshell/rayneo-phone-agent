"""Strict data contracts; executable code and shell commands are not skills."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path


class SkillError(ValueError):
    pass


FIELDS = {"resource_id", "text", "content_desc", "class_name", "parent", "sibling"}
IDENTITY = {"resource_id", "text", "content_desc"}
WEIGHTS = dict(resource_id=.40, text=.20, content_desc=.15,
               class_name=.10, parent=.10, sibling=.05)
ID = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
PACKAGE = re.compile(r"[a-zA-Z][\w]*(?:\.[a-zA-Z][\w]*)+\Z")
VARIABLE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")


def require(condition, message):
    if not condition:
        raise SkillError(message)


def keys(obj, allowed, required=()):
    require(isinstance(obj, dict), "expected object")
    require(not (obj.keys() - set(allowed)), f"unknown fields: {sorted(obj.keys() - set(allowed))}")
    require(set(required) <= obj.keys(), f"missing fields: {sorted(set(required) - obj.keys())}")


def read_json(path):
    path = Path(path)
    require(path.stat().st_size <= 2_000_000, "input file too large")
    def unique(pairs):
        result = {}
        for k, v in pairs:
            require(k not in result, f"duplicate JSON key: {k}")
            result[k] = v
        return result
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique,
                      parse_constant=lambda v: (_ for _ in ()).throw(SkillError(f"invalid number: {v}")))


def selector(value):
    keys(value, FIELDS | {"enabled", "locked", "text_mode", "desc_mode"})
    require(any(value.get(f) for f in IDENTITY), "selector needs an identity attribute, not just class/structure")
    for field in FIELDS & value.keys():
        require(isinstance(value[field], str) and 0 < len(value[field]) <= 512, f"invalid selector {field}")
    for field in ("enabled", "locked"):
        require(field not in value or type(value[field]) is bool, f"invalid {field}")
    for field in ("text_mode", "desc_mode"):
        require(value.get(field, "exact") in {"exact", "contains"}, "unsupported matching mode")
    return value


def assertion(value):
    keys(value, {"app", "activity", "present", "absent"}, {"app"})
    require(isinstance(value["app"], str) and PACKAGE.fullmatch(value["app"]), "invalid expected app")
    require("activity" not in value or isinstance(value["activity"], str) and bool(value["activity"]), "invalid activity")
    for field in ("present", "absent"):
        if field in value:
            require(isinstance(value[field], list) and 0 < len(value[field]) <= 20, "invalid assertion list")
            for item in value[field]:
                selector(item)
    return value


def validate(skill):
    keys(skill, {"schema_version", "id", "version", "app", "description", "intents", "params",
                 "selectors", "steps", "final_expect", "timeout_s"},
         {"schema_version", "id", "version", "app", "selectors", "steps", "final_expect"})
    require(skill["schema_version"] == 1, "unsupported schema version")
    require(isinstance(skill["id"], str) and ID.fullmatch(skill["id"]), "invalid skill id")
    require(type(skill["version"]) is int and skill["version"] > 0, "invalid version")
    require(isinstance(skill["app"], str) and PACKAGE.fullmatch(skill["app"]), "invalid app")
    require(isinstance(skill.get("description", ""), str), "invalid description")
    require(isinstance(skill.get("intents", []), list), "invalid intents")
    require(all(isinstance(i, str) and 0 < len(i) <= 200 for i in skill.get("intents", [])), "invalid intent")
    require(type(skill.get("timeout_s", 30)) in (int, float) and 0 < skill.get("timeout_s", 30) <= 300,
            "timeout must be in (0, 300]")
    params = skill.get("params", {})
    require(isinstance(params, dict), "invalid params")
    for name, rule in params.items():
        require(ID.fullmatch(name), "invalid parameter name")
        keys(rule, {"type", "max_length", "sensitive"}, {"type"})
        require(rule["type"] == "text", "v1 supports text parameters only")
        require(type(rule.get("max_length", 200)) is int and 1 <= rule.get("max_length", 200) <= 1000, "invalid parameter length")
        require(type(rule.get("sensitive", True)) is bool, "invalid sensitivity")
    require(isinstance(skill["selectors"], dict), "invalid bundles")
    for name, candidates in skill["selectors"].items():
        require(ID.fullmatch(name), "invalid bundle name")
        require(isinstance(candidates, list) and 0 < len(candidates) <= 50, "invalid candidates")
        for c in candidates:
            selector(c)
    require(isinstance(skill["steps"], list) and 0 < len(skill["steps"]) <= 100, "invalid steps")
    seen = set()
    for step in skill["steps"]:
        keys(step, {"id", "action", "effect", "before", "expect", "target", "args", "timeout_s", "high_risk"},
             {"id", "action", "effect", "before", "expect"})
        require(isinstance(step["id"], str) and ID.fullmatch(step["id"]) and step["id"] not in seen, "invalid or duplicate step id")
        seen.add(step["id"])
        require(step["action"] in {"launch", "tap", "type", "wait"}, "action not allowed")
        require(step["effect"] in {"read_only", "idempotent", "non_idempotent"}, "invalid effect")
        require(type(step.get("high_risk", False)) is bool, "invalid high_risk")
        require(type(step.get("timeout_s", 5)) in (int, float) and 0 < step.get("timeout_s", 5) <= 60, "invalid step timeout")
        assertion(step["before"])
        assertion(step["expect"])
        require(step["expect"]["app"] == skill["app"], "expected app differs from skill app")
        require(step["action"] == "launch" or step["before"]["app"] == skill["app"], "before app differs from skill app")
        args = step.get("args", {})
        if step["action"] == "launch":
            keys(args, {"package", "intent"})
            require(bool(args.get("package") or args.get("intent")), "launch needs package or intent")
            if "package" in args:
                require(args["package"] == skill["app"], "launch outside skill app")
            if "intent" in args:
                require(isinstance(args["intent"], str) and re.fullmatch(r"[a-zA-Z0-9_.]+", args["intent"]), "invalid Android intent")
        elif step["action"] == "type":
            keys(args, {"text", "clear"}, {"text", "clear"})
            require(isinstance(args["text"], str) and len(args["text"]) <= 2000 and type(args["clear"]) is bool, "invalid text args")
        else:
            keys(args, set())
        if step["action"] in {"tap", "type"}:
            require(step.get("target") in skill["selectors"], "target bundle missing")
        else:
            require("target" not in step, "unexpected target")
    assertion(skill["final_expect"])
    require(skill["final_expect"]["app"] == skill["app"], "final app mismatch")
    require(bool(skill["final_expect"].get("present") or skill["final_expect"].get("activity")), "final result must be more specific than app")
    # Parameters are substitutions in values, never executable expressions or keys.
    used = set(VARIABLE.findall(json.dumps(skill, ensure_ascii=False)))
    require(used <= params.keys(), "undeclared parameters")
    return skill


def bind(skill, params):
    validate(skill)
    rules = skill.get("params", {})
    require(isinstance(params, dict) and params.keys() == rules.keys(), "supply exactly the declared parameters")
    for name, rule in rules.items():
        value = params[name]
        require(isinstance(value, str) and 0 < len(value) <= rule.get("max_length", 200), f"invalid parameter: {name}")
        require(not any(ord(c) < 32 for c in value), "control characters not allowed")
        require("{{" not in value and "}}" not in value, "nested parameter expression")
    def replace(value):
        if isinstance(value, str):
            return VARIABLE.sub(lambda m: params[m[1]], value)
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return value
    return validate(replace(copy.deepcopy(skill)))


def normalized_intent(text):
    return " ".join(text.strip().casefold().split())


def route(text, skills):
    matches = [s for s in skills if normalized_intent(text) in
               [normalized_intent(i) for i in s.get("intents", [])]]
    return {"route": "skill" if len(matches) == 1 else "ambiguous" if matches else "explore_required",
            "skill_ids": [s["id"] for s in matches], "llm_calls": 0}
