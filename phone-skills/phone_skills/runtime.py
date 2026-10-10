"""Observe-act-verify supervisor. Never retries an action automatically."""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from .evidence import difference, snapshot, triage
from .model import bind
from .selector import MatchError, holds, resolve
from .storage import Lock, atomic_json, digest, read_json


class Stop(Exception):
    def __init__(self, code):
        self.code = code


class Runner:
    def __init__(self, adapter, data, cancel=None, poll_s=.1):
        self.adapter = adapter
        self.data = Path(data)
        self.cancel = cancel or (lambda: False)
        self.poll_s = poll_s

    def run(self, skill, params=None):
        skill = bind(skill, params or {})
        run_id = uuid.uuid4().hex
        revision = digest(skill)
        run_path = self.data / "runs" / (run_id + ".json")
        baseline_path = self.data / "baselines" / (revision + ".json")
        started = time.monotonic()
        deadline = started + skill.get("timeout_s", 30)
        if hasattr(self.adapter, "deadline"):
            self.adapter.deadline = deadline
        report = {"run_id": run_id, "skill_id": skill["id"], "version": skill["version"],
                  "revision": revision, "backend": self.adapter.kind, "verification": "offline_simulation" if self.adapter.kind == "mock" else "device_runtime",
                  "status": "running", "llm_calls": 0, "events": []}
        state = None
        dispatched = False
        unresolved = False
        step_id = None

        def check():
            if self.cancel():
                raise Stop("cancelled")
            if time.monotonic() >= deadline:
                raise Stop("timeout")

        def observe():
            check()
            result = self.adapter.observe()
            check()
            if result.blocked:
                raise Stop("blocked")
            return result

        def event(status, **extra):
            report["events"].append({"step": step_id, "status": status, **extra})
            atomic_json(run_path, report)

        with Lock(self.data / "locks", "device:" + self.adapter.device_id):
            try:
                # Initialize the real connection only after holding the device lock.
                check()
                self.adapter.open()
                atomic_json(run_path, report)
                # A launch step is the boundary that establishes its own app;
                # do not require the caller to already be in that app.
                if (not skill["steps"] or skill["steps"][0]["action"] != "launch"
                        or self.adapter.kind != "adb-readonly"):
                    state = observe()
                for step in skill["steps"]:
                    step_id = step["id"]
                    dispatched = False
                    unresolved = False
                    check()
                    if step.get("high_risk", False):
                        raise Stop("blocked")
                    if step["action"] == "launch" and self.adapter.kind == "adb-readonly":
                        state = None
                    else:
                        state = observe()
                        if not holds(state, step["before"]):
                            raise Stop("wrong_page")
                    target = None
                    match = {}
                    if "target" in step:
                        target, match = resolve(state.nodes, skill["selectors"][step["target"]], step["action"])
                    event("prepared", **match)
                    if step["action"] != "wait":
                        # Durable dispatch intent is written BEFORE touching the device.
                        event("dispatch_intent")
                        dispatched = unresolved = True
                        self.adapter.act(step, target)
                    stop_at = min(deadline, time.monotonic() + step.get("timeout_s", 5))
                    while True:
                        state = observe()
                        if holds(state, step["expect"]):
                            unresolved = False
                            event("verified")
                            break
                        if time.monotonic() >= stop_at:
                            raise Stop("outcome_unknown" if dispatched and step["effect"] == "non_idempotent" else "expect_failed")
                        time.sleep(min(self.poll_s, max(0, stop_at - time.monotonic())))
                state = observe()
                if not holds(state, skill["final_expect"]):
                    raise Stop("expect_failed")
                report["status"] = "succeeded"
            except MatchError as exc:
                report["status"] = exc.code
            except Stop as exc:
                report["status"] = "outcome_unknown" if unresolved and dispatched else exc.code
                report["stop_reason"] = exc.code
            except (ConnectionError, TimeoutError, OSError) as exc:
                report["status"] = "outcome_unknown" if unresolved else "disconnected"
                report["error_type"] = type(exc).__name__
            except KeyboardInterrupt:
                report["status"] = "outcome_unknown" if unresolved else "cancelled"
            except Exception as exc:
                report["status"] = "outcome_unknown" if unresolved else "adapter_error"
                report["error_type"] = type(exc).__name__
            finally:
                try:
                    self.adapter.close()
                except Exception:
                    report["cleanup_error"] = True
            current = snapshot(state)
            baseline = read_json(baseline_path) if baseline_path.exists() else None
            report.update({"elapsed_s": round(time.monotonic() - started, 3), "failed_step": step_id if report["status"] != "succeeded" else None,
                           "triage": "OK" if report["status"] == "succeeded" else triage(report["status"]),
                           "evidence": current, "diff_vs_last_success": difference(baseline, current)})
            atomic_json(run_path, report)
            if report["status"] == "succeeded":
                atomic_json(baseline_path, current)
        return report
