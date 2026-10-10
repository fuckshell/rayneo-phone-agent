"""Selector-only candidate patches. Tests are local simulations, not phone replay."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from .adapters import MockAdapter
from .model import keys, require, selector, validate
from .runtime import Runner
from .storage import digest


def candidate(skill, patch):
    validate(skill)
    keys(patch, {"op", "base_revision", "step", "bundle", "candidates", "reason"},
         {"op", "base_revision", "step", "bundle", "candidates", "reason"})
    require(patch["op"] == "append_selectors", "only append_selectors is allowed")
    require(patch["base_revision"] == digest(skill), "stale patch")
    require(isinstance(patch["reason"], str) and 0 < len(patch["reason"]) <= 500, "invalid patch reason")
    steps = [s for s in skill["steps"] if s["id"] == patch["step"]]
    require(len(steps) == 1 and steps[0].get("target") == patch["bundle"], "patch step/bundle mismatch")
    old = skill["selectors"][patch["bundle"]]
    require(not any(c.get("locked", False) for c in old), "manually locked bundle")
    require(isinstance(patch["candidates"], list) and 0 < len(patch["candidates"]) <= 10, "invalid patch candidates")
    result = deepcopy(skill)
    for item in patch["candidates"]:
        selector(item)
        require(item.get("enabled", True), "new candidate must be enabled")
        if item not in result["selectors"][patch["bundle"]]:
            result["selectors"][patch["bundle"]].append(deepcopy(item))
    require(result != skill, "patch is a no-op")
    result["version"] += 1
    return validate(result)


def review(skill, patch, cases):
    proposed = candidate(skill, patch)
    require(isinstance(cases, list) and len(cases) >= 2, "need at least a baseline case and a changed-page case")
    results = []
    with TemporaryDirectory(prefix="phone-skills-regression-") as temp:
        for index, case in enumerate(cases):
            keys(case, {"name", "fixture", "params", "expected"}, {"name", "fixture", "expected"})
            require(case["expected"] in {"succeeded", "ambiguous_match", "wrong_page", "blocked", "no_match", "outcome_unknown"}, "invalid regression expectation")
            statuses = []
            for label, version in (("base", skill), ("candidate", proposed)):
                adapter = MockAdapter(case["fixture"])
                run = Runner(adapter, Path(temp) / str(index) / label, poll_s=.001).run(version, case.get("params", {}))
                statuses.append(run["status"])
            results.append({"name": case["name"], "base": statuses[0], "candidate": statuses[1],
                            "expected": case["expected"], "pass": statuses[1] == case["expected"]})
    # A repair needs an existing positive baseline AND a newly repaired case.
    baseline_ok = any(r["base"] == "succeeded" and r["expected"] == "succeeded" for r in results)
    improvement = any(r["base"] != "succeeded" and r["candidate"] == "succeeded" and r["expected"] == "succeeded" for r in results)
    no_regressions = all(r["candidate"] == "succeeded" for r in results if r["base"] == "succeeded")
    negative_case = any(r["expected"] != "succeeded" for r in results)
    passed = baseline_ok and improvement and negative_case and no_regressions and all(r["pass"] for r in results)
    return proposed, {"status": "passed" if passed else "rejected", "scope": "offline_only",
                      "base_revision": digest(skill), "candidate_revision": digest(proposed),
                      "suite_hash": digest(cases), "results": results, "baseline_ok": baseline_ok,
                      "improvement": improvement, "no_regressions": no_regressions,
                      "negative_case": negative_case, "device_verified": False}


def import_demonstration(document):
    """Accept only an annotated, verified trace; never guess semantic targets."""
    keys(document, {"skill", "fixture", "params"}, {"skill", "fixture"})
    skill = validate(document["skill"])
    with TemporaryDirectory(prefix="phone-skills-import-") as temp:
        report = Runner(MockAdapter(document["fixture"]), temp).run(skill, document.get("params", {}))
    require(report["status"] == "succeeded", "demonstration does not satisfy its assertions")
    return skill
