from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from importlib.metadata import PackageNotFoundError

from .adapters import MockAdapter
from .device import DeviceAdapter, authorize, check_versions
from .model import SkillError, bind, read_json, require, route, validate
from .task_router import route_task
from .regression import import_demonstration, review
from .runtime import Runner
from .alarm import AdbAlarm
from .nl import interpret
from .storage import Store, atomic_json, digest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = Path.home() / "AppData" / "Local" / "PhoneSkills" if sys.platform == "win32" else Path.home() / ".local" / "share" / "phone-skills"


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser():
    p = argparse.ArgumentParser(description="Phone Skills PC controller (default: no device access)")
    p.add_argument("--data", type=Path, default=DEFAULT_DATA)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="verify imports/versions only; never enumerate or connect devices")
    for name in ("validate", "plan", "replay", "install", "patch-review"):
        sp = sub.add_parser(name)
        sp.add_argument("skill", type=Path)
        if name in {"plan", "replay"}:
            sp.add_argument("--params", type=Path, help="JSON parameters (not recorded in logs)")
        if name == "replay":
            sp.add_argument("--backend", choices=["mock", "mobilerun"], default="mock")
            sp.add_argument("--fixture", type=Path)
            sp.add_argument("--serial")
            sp.add_argument("--device-config", type=Path)
            sp.add_argument("--execute", action="store_true")
            sp.add_argument("--cancel-file", type=Path)
        if name == "patch-review":
            sp.add_argument("patch", type=Path)
            sp.add_argument("suite", type=Path)
            sp.add_argument("--promote", action="store_true", help="promote local skill revision only; does not authorize device execution")
    sp = sub.add_parser("inspect")
    sp.add_argument("run_id", nargs="?")
    sp = sub.add_parser("route")
    sp.add_argument("text")
    sp.add_argument("skills", type=Path, nargs="+")
    sp = sub.add_parser("task-route", help="route to a computer connector or phone handoff")
    sp.add_argument("text")
    sp = sub.add_parser("interpret")
    sp.add_argument("text")
    sp.add_argument("skills", type=Path, nargs="*")
    sp = sub.add_parser("alarm")
    sp.add_argument("hour", type=int)
    sp.add_argument("minute", type=int)
    sp.add_argument("--serial", required=True)
    sp.add_argument("--label", default="phone-skills")
    sp = sub.add_parser("audio-bridge", help="run the local Android PCM audio receiver")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=18880)
    sp.add_argument("--output-dir", type=Path, default=Path("audio_sessions"))
    sp = sub.add_parser("agent-gateway", help="run the bounded PC phone-agent tool gateway")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=18881)
    sp = sub.add_parser("agent-run", help="run one bounded DeepSeek phone-agent task")
    sp.add_argument("text")
    sp.add_argument("--serial", required=True)
    sp.add_argument("--max-steps", type=int, default=8)
    sp = sub.add_parser("rollback")
    sp.add_argument("skill_id")
    sp.add_argument("revision")
    sp = sub.add_parser("import-demo")
    sp.add_argument("file", type=Path)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    store = Store(args.data / "skills")
    try:
        if args.command == "doctor":
            try:
                versions = check_versions()
                from .compat import load_core
                Mobilerun = load_core()
                require(callable(Mobilerun.connect), "missing connect API")
                device_status = "ready_for_pairing"
            except (PackageNotFoundError, ImportError, SkillError) as exc:
                versions = {}
                device_status = "dependencies_unavailable:" + type(exc).__name__
            emit({"python": sys.version.split()[0], "core": "ready", "device_adapter": device_status,
                  "versions": versions, "device_connected": False, "data_directory": str(args.data),
                  "repository_sync": "disabled_on_windows" if sys.platform == "win32" else "upstream"})
            return 0 if versions else 2
        if args.command == "route":
            result = route(args.text, [validate(read_json(p)) for p in args.skills])
            # Intent text is not logged; only the route result.
            atomic_json(args.data / "last-route.json", result)
            emit(result)
            return 0 if result["route"] == "skill" else 2
        if args.command == "task-route":
            result = route_task(args.text)
            atomic_json(args.data / "last-task-route.json", result)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["route"] != "clarify" else 2
        if args.command == "interpret":
            result = interpret(args.text, [validate(read_json(p)) for p in args.skills])
            atomic_json(args.data / "last-route.json", result)
            emit(result)
            return 0 if result["route"] in {"skill", "alarm"} else 2
        if args.command == "alarm":
            result = AdbAlarm(args.serial).set(args.hour, args.minute, args.label)
            emit(result)
            return 0 if result["status"] == "succeeded" else 2
        if args.command == "audio-bridge":
            from .audio_bridge import serve
            serve(args.host, args.port, str(args.output_dir))
            return 0
        if args.command == "agent-gateway":
            from .agent_gateway import serve
            serve(args.host, args.port)
            return 0
        if args.command == "agent-run":
            from .agent_runner import run_agent
            result = run_agent(args.text, args.serial, args.max_steps)
            emit(result)
            return 0 if result["status"] == "succeeded" else 2
        if args.command == "inspect":
            if args.run_id:
                require(len(args.run_id) == 32 and all(c in "0123456789abcdef" for c in args.run_id), "invalid run id")
                emit(read_json(args.data / "runs" / (args.run_id + ".json")))
            else:
                rows = []
                for file in sorted((args.data / "runs").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
                    run = read_json(file)
                    rows.append({k: run[k] for k in ("run_id", "skill_id", "status", "verification", "llm_calls")})
                emit({"runs": rows})
            return 0
        if args.command == "rollback":
            emit({"revision": store.rollback(args.skill_id, args.revision), "business_actions_undone": False})
            return 0
        if args.command == "import-demo":
            skill = import_demonstration(read_json(args.file))
            emit({"revision": store.commit(skill, "annotated demonstration import"), "scope": "offline_only"})
            return 0
        skill = validate(read_json(args.skill))
        if args.command == "validate":
            emit({"valid": True, "skill_id": skill["id"], "revision": digest(skill)})
        elif args.command == "install":
            emit({"revision": store.commit(skill, "initial local import"), "scope": "local_skill_store_only"})
        elif args.command == "plan":
            bound = bind(skill, read_json(args.params) if args.params else {})
            emit({"skill_id": bound["id"], "revision": digest(bound), "app": bound["app"], "llm_calls": 0,
                  "device_access": False, "steps": [{"id": s["id"], "action": s["action"], "effect": s["effect"],
                  "high_risk": s.get("high_risk", False)} for s in bound["steps"]]})
        elif args.command == "replay":
            params = read_json(args.params) if args.params else {}
            bound = bind(skill, params)
            cancelled = lambda: bool(args.cancel_file and args.cancel_file.exists())
            if args.backend == "mock":
                require(args.fixture is not None, "mock replay requires --fixture")
                adapter = MockAdapter(read_json(args.fixture))
            else:
                require(args.execute and args.serial and args.device_config, "real replay needs --execute, --serial and --device-config")
                config = read_json(args.device_config)
                authorize(config, args.serial, bound)
                check_versions()
                adapter = DeviceAdapter(args.serial, config["operation_timeout_s"], cancelled)
            result = Runner(adapter, args.data, cancelled).run(skill, params)
            emit({k: result[k] for k in ("run_id", "skill_id", "status", "verification", "llm_calls", "elapsed_s", "triage")})
            return 0 if result["status"] == "succeeded" else 2
        elif args.command == "patch-review":
            patch, suite = read_json(args.patch), read_json(args.suite)
            proposed, result = review(skill, patch, suite)
            output = args.data / "patch-reviews" / (digest(patch) + ".json")
            atomic_json(output, result)
            if args.promote:
                require(result["status"] == "passed", "patch rejected by regression gate")
                result["promoted_revision"] = store.commit(proposed, "selector patch; suite=" + result["suite_hash"], expected=digest(skill))
                atomic_json(output, result)
            emit(result)
            return 0 if result["status"] == "passed" else 2
        return 0
    except (SkillError, OSError, ValueError, KeyError, TypeError) as exc:
        # Do not echo exception strings from unknown adapters or file contents.
        emit({"status": "rejected", "error_type": type(exc).__name__,
              "message": str(exc) if isinstance(exc, SkillError) else "Invalid input or unavailable local resource"})
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
