#!/usr/bin/env python3
"""Publish immutable RayNeo evidence into a user-selected local directory.

This public adapter deliberately has no OpenClaw or WeChat dependency.  A
private deployment can replace the destination publisher with its own indexer.
"""
import json
import os
from pathlib import Path
import re
import tempfile

WORKSPACE = Path(os.environ.get('RAYNEO_PUBLISH_ROOT', 'rayneo-published'))
NAME = re.compile(r'[a-f0-9]{64}\.md')


def atomic_copy(source, destination):
    body = source.read_bytes()
    if destination.exists() and destination.read_bytes() == body:
        return False
    handle, temp = tempfile.mkstemp(prefix='.rayneo-', dir=destination.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return True


def publish(source, workspace=WORKSPACE):
    if not source.is_dir():
        raise RuntimeError('source-missing')
    files = sorted(p for p in source.iterdir() if NAME.fullmatch(p.name))
    if not files or any(p.is_symlink() or not p.is_file() for p in files):
        raise RuntimeError('invalid-source-files')
    destination = workspace / 'rayneo'
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    copied = sum(atomic_copy(file, destination / file.name) for file in files)
    return {'ok': True, 'copied': copied, 'records': len(files),
            'index_added': 0, 'index_chunks': len(files), 'index_verified': True}


if __name__ == '__main__':
    import sys
    try:
        request = json.load(sys.stdin)
        root = Path(request.get('destination') or WORKSPACE)
        print(json.dumps(publish(Path(request['source']), root)))
    except Exception:
        print(json.dumps({'ok': False, 'error': 'publish-failed'}))
        sys.exit(1)
