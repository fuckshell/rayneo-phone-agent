"""Project-local import boundary for audited Mobilerun on Windows.

Core 1.6.2 imports POSIX-only repository sync eagerly. We do not use that
feature: replace only that module with explicit fail-closed operations.
Never emulate fcntl or edit installed packages. Call before importing core.
"""
import sys
import threading
from importlib.metadata import version
from types import ModuleType

_LOCK = threading.RLock()


def _unsupported_sync(*args, **kwargs):
    raise NotImplementedError(
        "Mobilerun repository sync is disabled by Phone Skills on Windows; "
        "use the reviewed local skill store instead."
    )


def load_core():
    with _LOCK:
        if sys.platform == "win32":
            if version("mobilerun-core") != "1.6.2":
                raise ImportError("Windows compatibility boundary requires mobilerun-core 1.6.2")
            name = "mobilerun_core.sync"
            existing = sys.modules.get(name)
            if existing is not None and not getattr(existing, "_phone_skills_disabled", False):
                raise ImportError("Mobilerun sync was loaded outside the audited import boundary")
            if existing is None:
                module = ModuleType(name, "Repository sync disabled on Windows by Phone Skills")
                module._phone_skills_disabled = True
                module.sync_scripts = _unsupported_sync
                module.sync_skills = _unsupported_sync
                sys.modules[name] = module
        from mobilerun_core import Mobilerun
        return Mobilerun
