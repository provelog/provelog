"""Deterministic, safe file names for exported evidence packs.

`my-evidence.zip` is a placeholder that collides on the second export. Names
are built from stable, non-sensitive fields only: the agent key, the session's
last activity in local time. The session title is deliberately NOT used - it
is the user's prompt and may contain secrets or customer names.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_component(value: Any, fallback: str = "session") -> str:
    text = _SAFE.sub("-", str(value or "")).strip("-._")
    return (text or fallback)[:40]


def _stamp(row: dict[str, Any]) -> str:
    from datetime import datetime
    try:
        text = str(row.get("last_activity") or "").replace("Z", "+00:00")
        return datetime.fromisoformat(text).astimezone().strftime("%Y%m%d-%H%M%S")
    except ValueError:
        return time.strftime("%Y%m%d-%H%M%S")


def short_id(row: dict[str, Any]) -> str:
    """First 8 hex-ish characters of the session id, else of the stream id."""
    for key in ("session_id", "stream_id"):
        cleaned = re.sub(r"[^0-9a-fA-F]", "", str(row.get(key) or ""))
        if len(cleaned) >= 8:
            return cleaned[:8].lower()
    return "00000000"


def pack_basename(row: dict[str, Any], ext: str = "zip") -> str:
    """`<agent>-<YYYYMMDD-HHMMSS>.<ext>` - stable and title-free."""
    agent = safe_component(row.get("agent"), "session")
    return f"{agent}-{_stamp(row)}.{ext}"


def next_free(path: Path) -> Path:
    """First free name at `path`: `x.zip`, `x-2.zip`, `x-3.zip`, ..."""
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def batch_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def batch_dir_name() -> str:
    return batch_stamp()


def collection_name(count: int) -> str:
    return f"provelog-export-{batch_stamp()}.zip"
