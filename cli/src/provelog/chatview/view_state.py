"""Local, non-evidence view state for the viewer.

Archive and pin are chatview preferences, not signed events: rewriting a
manifest or appending to a trajectory would make them look like part of the
proof. Keep them in a small JSON file next to the store instead.

This is deliberately separate from host archive state. Codex's
``archived_sessions/`` directory and DSH's archived ids are source facts; this
module only records what the ProveLog viewer was told to hide or front.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FILE_NAME = "view-state.json"


def _path(home: Path) -> Path:
                                                                              
                                                                 
    from .records import store_root
    return store_root(home) / FILE_NAME


def read(home: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sessions = raw.get("sessions") if isinstance(raw, dict) else None
    if not isinstance(sessions, dict):
        return {}
    return {str(k): v for k, v in sessions.items() if isinstance(v, dict)}


def archived(home: Path) -> dict[str, dict[str, Any]]:
    """Only entries that are actually archived (a pin-only entry is not)."""
    return {sid: entry for sid, entry in read(home).items()
            if entry.get("archived_at")}


def pinned(home: Path) -> dict[str, dict[str, Any]]:
    return {sid: entry for sid, entry in read(home).items()
            if entry.get("pinned_at")}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write(home: Path, state: dict[str, dict[str, Any]]) -> None:
    path = _path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "sessions": state}
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def set_archived(home: Path, session_id: str, archived: bool) -> dict[str, Any]:
    """Set one session's view archive. Never mutates evidence.

    Archive and pin are mutually exclusive, mirroring DSH: a pinned session
    that is archived loses its pin, and an archived row offers unarchive, not
    pin. An empty entry is removed so a pin-only entry never reads as archived.
    """
    state = read(home)
    entry = dict(state.get(session_id) or {})
    if archived:
        entry.pop("pinned_at", None)
        entry.setdefault("archived_at", _now())
    else:
        entry.pop("archived_at", None)
    if entry:
        state[session_id] = entry
    else:
        state.pop(session_id, None)
    _write(home, state)
    return state.get(session_id) or {}


def set_pinned(home: Path, session_id: str, pinned: bool) -> dict[str, Any]:
    """Set one session's viewer pin. Never mutates evidence."""
    state = read(home)
    entry = dict(state.get(session_id) or {})
    if pinned:
                                                                       
                                                                           
                              
        entry.pop("archived_at", None)
        entry.setdefault("pinned_at", _now())
    else:
        entry.pop("pinned_at", None)
    if entry:
        state[session_id] = entry
    else:
        state.pop(session_id, None)
    _write(home, state)
    return state.get(session_id) or {}


def clear(home: Path, session_id: str) -> None:
    """Drop every viewer preference for a session (used when it is deleted)."""
    state = read(home)
    if session_id in state:
        state.pop(session_id, None)
        _write(home, state)
