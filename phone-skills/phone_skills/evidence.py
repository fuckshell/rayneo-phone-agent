"""No raw UI, screenshots, parameter values or exception messages in run logs."""
import hashlib
from .storage import digest


def masked(text):
    return "[redacted]" if text else ""


def snapshot(state):
    if state is None:
        return None
    nodes = []
    for node in state.nodes[:150]:
        nodes.append({"key": hashlib.sha256(node.key.encode()).hexdigest()[:12],
                      "resource_id": masked(node.resource_id), "text": masked(node.text),
                      "content_desc": masked(node.content_desc), "class_name": node.class_name[:120],
                      "visible": node.visible, "enabled": node.enabled,
                      "clickable": node.clickable, "editable": node.editable})
    # Dynamic text is deliberately excluded from stored fingerprints.
    structure = [(n.class_name, n.visible, n.enabled, n.clickable, n.editable) for n in state.nodes]
    return {"app": state.app, "activity": state.activity, "nodes": nodes,
            "node_count": len(state.nodes), "truncated": len(state.nodes) > 150,
            "fingerprint": digest([state.app, state.activity, structure])}


def difference(previous, current):
    if not previous or not current:
        return {"baseline_available": False}
    return {"baseline_available": True,
            "structure_changed": previous["fingerprint"] != current["fingerprint"],
            "node_count_delta": current["node_count"] - previous["node_count"],
            "app_changed": previous["app"] != current["app"]}


def triage(code):
    return {"ambiguous_match": "HUMAN_REQUIRED", "no_match": "TARGET_NOT_FOUND",
            "blocked": "HUMAN_REQUIRED", "wrong_page": "WRONG_PAGE",
            "disconnected": "CONNECTION_LOST", "outcome_unknown": "HUMAN_REQUIRED",
            "timeout": "TIMEOUT", "cancelled": "CANCELLED",
            "expect_failed": "RESULT_NOT_CONFIRMED"}.get(code, "HUMAN_REQUIRED")
