"""Atomic local artifacts and OS-held locks (released automatically on crash)."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from .model import ID, SkillError, read_json, require, validate


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        # OneDrive can briefly hold the destination while scanning a newly
        # written file. Retry the atomic switch instead of losing a task
        # checkpoint to a transient Windows sharing violation.
        for attempt in range(6):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)


class Lock:
    def __init__(self, directory, name):
        self.path = Path(directory) / (hashlib.sha256(name.encode()).hexdigest() + ".lock")
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise SkillError("resource busy: another task holds its lock") from None
        return self

    def __exit__(self, *_):
        if self.handle:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


class Store:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, skill_id):
        require(isinstance(skill_id, str) and ID.fullmatch(skill_id), "invalid skill id")
        return self.root / skill_id

    def load(self, skill_id, revision=None):
        folder = self.path(skill_id)
        if revision is None:
            revision = read_json(folder / "current.json")["revision"]
        require(isinstance(revision, str) and len(revision) == 64 and all(c in "0123456789abcdef" for c in revision), "invalid revision")
        doc = read_json(folder / "revisions" / (revision + ".json"))
        require(digest(doc["skill"]) == revision, "revision content hash mismatch")
        return validate(doc["skill"])

    def commit(self, skill, reason, expected=None):
        validate(skill)
        folder = self.path(skill["id"])
        with Lock(self.root / ".locks", skill["id"]):
            pointer = folder / "current.json"
            current = read_json(pointer)["revision"] if pointer.exists() else None
            require(current == expected, "revision conflict; review against current version again")
            if current:
                require(skill["version"] == self.load(skill["id"])["version"] + 1, "version must increment by one")
            revision = digest(skill)
            record = folder / "revisions" / (revision + ".json")
            if not record.exists():
                atomic_json(record, {"skill": skill, "parent": current, "reason": reason, "created_at": time.time()})
            # Single atomic pointer switches the entire skill + selectors together.
            atomic_json(pointer, {"revision": revision, "previous": current})
            return revision

    def rollback(self, skill_id, revision):
        self.load(skill_id, revision)
        with Lock(self.root / ".locks", skill_id):
            folder = self.path(skill_id)
            previous = read_json(folder / "current.json")["revision"]
            atomic_json(folder / "current.json", {"revision": revision, "previous": previous,
                                                    "rollback_at": time.time()})
        return revision
