"""Shared write path for the local JSONL logs.

These logs record whole hook payloads, so they carry tool inputs: shell
commands, and the full contents of every file an agent writes. Two properties
keep that from becoming a liability, and both belong here rather than at each
call site:

* owner-only permissions, repaired in place for logs written by older versions;
* a size cap, so a long-lived install cannot grow an unbounded plaintext copy
  of everything the agents did.

Standard library only: the hook process imports this on every agent event and
must keep working with PyObjC absent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 3
FILE_MODE = 0o600
DIR_MODE = 0o700


def secure_path_mode(path: Path, mode: int) -> None:
    """Tighten permissions in place, best effort."""
    try:
        if (path.stat().st_mode & 0o777) == mode:
            return
        path.chmod(mode)
    except OSError:
        pass


def rotate_log(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> None:
    """Roll ``path`` over once it reaches ``max_bytes``, keeping N backups.

    Rotation runs before the append rather than after, so a record is never
    split across two files; the live log may exceed the cap by one record.
    """
    if max_bytes <= 0 or backup_count <= 0:
        return

    try:
        if path.stat().st_size < max_bytes:
            return
    except OSError:
        return

    def backup(index: int) -> Path:
        return path.with_name(f"{path.name}.{index}")

    try:
        backup(backup_count).unlink()
    except OSError:
        pass
    for index in range(backup_count - 1, 0, -1):
        try:
            backup(index).replace(backup(index + 1))
        except OSError:
            continue
    try:
        path.replace(backup(1))
    except OSError:
        return
    secure_path_mode(backup(1), FILE_MODE)


def append_jsonl_line(
    path: Path,
    record: dict[str, Any],
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> None:
    """Append one JSON record as a single physical line, privately and bounded."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    secure_path_mode(path.parent, DIR_MODE)
    rotate_log(path, max_bytes=max_bytes, backup_count=backup_count)

    payload = (
        json.dumps(record, separators=(",", ":"), ensure_ascii=False, default=str) + "\n"
    )
    # O_APPEND puts each record at EOF in one write, so concurrent hook
    # processes from parallel agent sessions cannot interleave a line. The mode
    # argument applies only when this call creates the file, hence the chmod.
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
    try:
        os.write(handle, payload.encode("utf-8"))
    finally:
        os.close(handle)
    secure_path_mode(path, FILE_MODE)
