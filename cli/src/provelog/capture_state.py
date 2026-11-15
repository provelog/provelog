"""The collector's read watermark, kept OUTSIDE the evidence store.

The store's unit of deletion is a conversation folder. If the watermark that says
"this host session was already imported up to seq N" lived inside that folder,
deleting the folder would look exactly like "never imported" - and the next poll
would re-import the whole conversation, silently undoing the deletion.

So the watermark lives here, at user level (`~/.provelog/capture.json`). Deleting
a folder (in Finder or in the viewer) now sticks: the collector still knows how
far it read, so the old records are not imported again. If the conversation
continues, the events after the watermark are recorded into a new folder marked
`head_omitted` - exactly what a fresh install would do on an open conversation.

Nothing here is evidence: it is a read position, small, and rebuildable by
re-importing. Losing it costs duplicate imports, never records.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CACHE: tuple[float, dict[str, Any]] | None = None


def path() -> Path:
    return Path.home() / ".provelog" / "capture.json"


def load() -> dict[str, Any]:
    global _CACHE
    target = path()
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return {"v": 1, "sessions": {}}
    if _CACHE is not None and _CACHE[0] == mtime:
        return _CACHE[1]
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"v": 1, "sessions": {}}
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        data = {"v": 1, "sessions": {}}
    _CACHE = (mtime, data)
    return data


def _save(data: dict[str, Any]) -> None:
    target = path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    except OSError:
        pass


def get(session_id: str) -> dict[str, Any]:
    entry = load()["sessions"].get(str(session_id))
    return dict(entry) if isinstance(entry, dict) else {}


def clear(session_id: str) -> None:
    """Forget one session's import position (used by an explicit rebuild)."""
    data = load()
    data["sessions"].pop(str(session_id), None)
    _save(data)


def update(session_id: str, **fields: Any) -> None:
    data = load()
    entry = data["sessions"].setdefault(str(session_id), {})
    entry.update({k: v for k, v in fields.items() if v is not None})
    _save(data)

def mark_deleted(session_id: str, at_ms: int | None = None) -> None:
    """Remember when the user deleted this conversation's records.

    The collector treats it as a hard boundary: events stamped before this are
    not recorded into the folder that starts after the deletion. Only the viewer
    can know the instant (Finder has no event to listen to), so a Finder deletion
    falls back to the last imported position instead.
    """
    import time as _time

    data = load()
    entry = data["sessions"].setdefault(str(session_id), {})
    entry["deleted_at_ms"] = int(at_ms if at_ms is not None else _time.time() * 1000)
    _save(data)
