"""Small deterministic intent router; unknown or ambiguous text is rejected."""
from __future__ import annotations

from .alarm import parse_alarm_text
from .model import normalized_intent


def interpret(text, skills):
    alarm = parse_alarm_text(text)
    if alarm["status"] == "ready":
        return {"route": "alarm", "action": alarm, "llm_calls": 0}
    result = [s for s in skills if normalized_intent(text) in [normalized_intent(i) for i in s.get("intents", [])]]
    if len(result) == 1:
        return {"route": "skill", "skill_ids": [result[0]["id"]], "llm_calls": 0}
    if len(result) > 1:
        return {"route": "ambiguous", "skill_ids": [s["id"] for s in result], "llm_calls": 0}
    return {"route": "explore_required", "llm_calls": 0}
