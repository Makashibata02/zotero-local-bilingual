"""Atomic JSON snapshots that tolerate Windows readers and brief file scanners."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time


def read_json(path: Path):
    # Hold the filesystem handle only while reading, not during JSON parsing.
    # Windows replacement can still race with this brief read; the writer
    # therefore retries sharing/permission failures after readers close.
    with path.open("rb") as stream:
        payload = stream.read()
    return json.loads(payload.decode("utf8"))


def write_json_atomic(path: Path, value, *, indent=None):
    """Retry only filesystem replacement, never a translation request.

    Keep the previous complete snapshot on failure. Never truncate the live
    audit or reset its budget to work around permissions.
    """
    payload = json.dumps(value, ensure_ascii=False, indent=indent)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                return
            except OSError as exc:
                transient = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32, 33}
                if not transient or attempt == 9:
                    raise
                time.sleep(min(.02 * 2 ** attempt, .2))
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # A scanner may still hold this orphan; the live audit is intact.
