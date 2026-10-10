"""Weighted candidate ranking with separate evidence and ambiguity gates."""
from dataclasses import dataclass, field
import unicodedata
from .model import FIELDS, IDENTITY, WEIGHTS


@dataclass
class Node:
    key: str
    resource_id: str = ""
    text: str = ""
    content_desc: str = ""
    class_name: str = ""
    parent: str = ""
    sibling: str = ""
    visible: bool = True
    enabled: bool = True
    clickable: bool = False
    editable: bool = False
    bounds: list = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class State:
    app: str
    activity: str
    nodes: list[Node]
    blocked: str = ""


class MatchError(Exception):
    code = "no_match"


class AmbiguousMatch(MatchError):
    code = "ambiguous_match"


def _normalize(value):
    value = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(value.split()).casefold()


def matches(node, candidate, field):
    expected = _normalize(candidate[field])
    observed = _normalize(getattr(node, field))
    mode = candidate.get("text_mode" if field == "text" else "desc_mode", "exact")
    return bool(expected) and (expected in observed if field in {"text", "content_desc"} and mode == "contains"
                               else expected == observed)


def exact_match(node, candidate):
    return node.visible and all(matches(node, candidate, f) for f in FIELDS & candidate.keys())


def holds(state, expect):
    if state.app != expect["app"] or ("activity" in expect and state.activity != expect["activity"]):
        return False
    return (all(any(exact_match(n, c) for n in state.nodes) for c in expect.get("present", [])) and
            all(not any(exact_match(n, c) for n in state.nodes) for c in expect.get("absent", [])))


def resolve(nodes, candidates, action="tap"):
    ranked = {}
    for node in nodes:
        if not node.visible or not node.enabled or (action == "tap" and not node.clickable) or (action == "type" and not node.editable):
            continue
        for candidate in candidates:
            if not candidate.get("enabled", True):
                continue
            active = FIELDS & candidate.keys()
            hit = {f for f in active if matches(node, candidate, f)}
            if not hit & IDENTITY:
                continue
            # The reference must contain identity + independent contextual evidence.
            if len(hit) < 2:
                continue
            score = sum(WEIGHTS[f] for f in hit) / sum(WEIGHTS[f] for f in active)
            if score < .3:
                continue
            degraded = not ("resource_id" in hit and len(hit) >= 2 and score >= .5)
            previous = ranked.get(node.key)
            if previous is None or score > previous[0]:
                ranked[node.key] = (score, degraded, node)
    # Accessibility providers may expose a physical control more than once.
    # Bounds are stable across provider-generated keys, so collapse exact
    # duplicates before applying the ambiguity gate.
    physical = {}
    for result in ranked.values():
        node = result[2]
        identity = ("bounds", tuple(node.bounds)) if len(node.bounds) == 4 else ("key", node.key)
        previous = physical.get(identity)
        if previous is None or result[0] > previous[0]:
            physical[identity] = result
    results = sorted(physical.values(), key=lambda r: r[0], reverse=True)
    if not results:
        raise MatchError("no sufficiently identified visible target")
    if len(results) > 1 and results[0][0] - results[1][0] < .1 + 1e-9:
        raise AmbiguousMatch("top targets are not distinguishable")
    score, degraded, node = results[0]
    return node, {"score": round(score, 4), "degraded": degraded}
