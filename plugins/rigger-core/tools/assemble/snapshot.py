"""The per-session resolved snapshot: <config dir>/rigger/state/<session id>.resolved.json.

The session id comes from hook input, so it is checked before it becomes part of a path: an id
that is not 1 to 128 of `A-Z a-z 0-9 _ -` gives no path, and nothing is written or read.
"""

import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Optional

SNAPSHOT_FORMAT = 2  # 2: `slots` holds the resolved slot table (slots.as_snapshot), not the level-1 merge
SNAPSHOT_KEYS = ("format", "parts", "modules", "config", "sections", "rows", "slots", "notices")
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
CLEANUP_PATTERNS = ("*.resolved.json", "*.options.*.json", "*.tmp")


def state_dir(config_dir: Path) -> Path:
    return config_dir / "rigger" / "state"


def snapshot_path(config_dir: Path, session_id: str) -> Optional[Path]:
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        return None
    return state_dir(config_dir) / (session_id + ".resolved.json")


def write_snapshot(config_dir: Path, session_id: str, data: dict) -> Optional[Path]:
    """Write atomically (temp name, then os.replace); None when the id is unsafe or the write fails."""
    path = snapshot_path(config_dir, session_id)
    if path is None:
        return None
    try:
        # ASCII escapes: a lone surrogate (a raw non-UTF-8 byte kept by surrogateescape) round-trips as \udcXX.
        body = (json.dumps(data, ensure_ascii=True, sort_keys=True, indent=1) + "\n").encode("ascii")
    except (TypeError, ValueError):
        return None
    temp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(body)
        os.replace(str(temp), str(path))
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass
        return None
    return path


def read_snapshot(config_dir: Path, session_id: str) -> Optional[dict]:
    """The snapshot, or None when it is missing, unreadable, or of another format (D21)."""
    path = snapshot_path(config_dir, session_id)
    if path is None:
        return None
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or any(key not in data for key in SNAPSHOT_KEYS):
        return None
    fmt = data["format"]
    if not isinstance(fmt, int) or isinstance(fmt, bool) or fmt != SNAPSHOT_FORMAT:
        return None
    return data


def cleanup(config_dir: Path, now: float, max_age_days: int = 14) -> int:
    """Delete snapshot, options and temp files older than max_age_days; return how many."""
    limit = now - max_age_days * 86400
    deleted = 0
    try:
        entries = list(os.scandir(str(state_dir(config_dir))))
    except OSError:
        return 0
    for entry in entries:
        if not any(fnmatch.fnmatchcase(entry.name, pattern) for pattern in CLEANUP_PATTERNS):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
            # os.unlink refuses a directory, so only files and links go.
            if info.st_mtime < limit:
                os.unlink(entry.path)
                deleted += 1
        except OSError:
            continue
    return deleted
