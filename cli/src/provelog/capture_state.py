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
    entry["deleted_by"] = "viewer"
    _save(data)


                                                                              
                                                             
ROOT_MISSING_GRACE_S = 600.0


def _iso_ms(value: Any) -> int:
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                   .timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def _has_new_records(store: Any, session_id: str, boundary: int) -> bool:
    try:
        events = store.session_events(session_id)
    except Exception:
        return True
    return any(_iso_ms(event.get("ts_utc")) > boundary for event in events)


                                                                              
                                                                              
                                                                            
                                           
_RESTORE_FIELDS = (
    "host_seq", "assignment_seq", "source_path", "source_mtime", "source_size",
    "chain_id", "capture_from_ms", "head_omitted", "capture_point",
    "verification_level", "commit_at", "commit_count", "source_offsets",
    "last_turn", "last_ms", "legacy_multi",
)


def _bare_id(value: Any) -> str:
    text = str(value or "")
    return text[5:] if text.startswith("id-") else text


def _restored_capture(store: Any, session_id: str, folder: Any) -> dict[str, Any]:
    """Read the watermark stored in a returned folder's manifest.

    A restored folder is evidence; its manifest records the last host position
    that folder actually captured. Do not derive this from the chain's record
    seq: importers use host sequence numbers, not evidence sequence numbers.
    """
    try:
        manifest = json.loads((Path(folder) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(manifest, dict):
        return {}
    source: Any = manifest
    if str(manifest.get("session_id") or "") not in {session_id, _bare_id(session_id)}:
        child = (manifest.get("child_sessions") or {}).get(session_id)
        if child is None:
            for key, value in (manifest.get("child_sessions") or {}).items():
                if _bare_id(key) == _bare_id(session_id):
                    child = value
                    break
        source = child if isinstance(child, dict) else {}
    return {key: source[key] for key in _RESTORE_FIELDS if key in source}


def reconcile_missing(store: Any, at_ms: int | None = None,
                      now: float | None = None) -> bool:
    """Reconcile state rows with the evidence folders they describe.

    Returns True when the whole store root is missing inside the grace window:
    callers must skip this pass rather than rebuild folders from old events.

    A missing root is ambiguous (Finder deletion vs an unmounted disk). After
    the grace period it is treated as a deletion of every conversation; before
    that, nothing is marked and no importer runs. A single missing session
    folder while the root exists is unambiguous and is marked immediately.
    """
    import time as _time

    data = load()
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        return False
    if not sessions:
                                                                              
                                           
        if data.pop("store_missing_since", None) is not None:
            _save(data)
        return False
    now_s = float(now if now is not None else _time.time())
    now_ms = int(at_ms if at_ms is not None else now_s * 1000)
    root = getattr(store, "root", None)
    root_missing = bool(root is not None and not Path(root).exists())

    def last_captured_ms(entry: dict[str, Any]) -> int:
        for key in ("last_ms", "source_updated_at"):
            value = entry.get(key)
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                return number
        for key in ("source_mtime", "commit_at"):
            value = entry.get(key)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                return int(number * 1000) if number < 10_000_000_000 else int(number)
        return now_ms

    changed = 0
    if root_missing:
        since = data.get("store_missing_since")
        try:
            since_s = float(since)
        except (TypeError, ValueError):
            since_s = 0.0
        if since_s <= 0:
            data["store_missing_since"] = now_s
            _save(data)
            return True
        if now_s - since_s < ROOT_MISSING_GRACE_S:
            return True
        for entry in sessions.values():
            if not isinstance(entry, dict) or entry.get("deleted_at_ms"):
                continue
            entry["deleted_at_ms"] = last_captured_ms(entry)
            entry["deleted_by"] = "missing_root"
            changed += 1
        data.pop("store_missing_since", None)
        if changed:
            _save(data)
        return False

    if data.pop("store_missing_since", None) is not None:
        changed += 1
    for session_id, entry in sessions.items():
        if not isinstance(entry, dict):
            continue
        try:
            folder = store.locate(session_id)
        except Exception:
            continue
        if folder is None:
            if not entry.get("deleted_at_ms"):
                entry["deleted_at_ms"] = last_captured_ms(entry)
                entry["deleted_by"] = "missing_folder"
                changed += 1
            continue
                                                                            
                                                                             
                                                                              
        if (entry.get("deleted_by") in ("missing_root", "missing_folder")
                and entry.get("deleted_at_ms")):
            boundary = int(entry.get("deleted_at_ms") or 0)
            if not _has_new_records(store, session_id, boundary):
                restored = _restored_capture(store, session_id, folder)
                if restored:
                    entry.update(restored)
                entry.pop("deleted_at_ms", None)
                entry.pop("deleted_by", None)
                changed += 1
    if changed:
        _save(data)
    return False


def stalled_sources(now: float | None = None, min_age_s: float = 900.0,
                    active_within_s: float = 86400.0) -> list[Path]:
    """Source files that grew after the last recorded watermark.

    A collector killed by its parent Job Object cannot keep the watermark
    current; comparing source size with the last commit is a cheap detector.
    Only sources whose last commit is older than `min_age_s` are returned, so
    an ordinary ten-second polling interval does not look stalled.
    """
    import time

    now = time.time() if now is None else now
    out: list[Path] = []
    for entry in load().get("sessions", {}).values():
        if not isinstance(entry, dict):
            continue
        try:
            source = Path(str(entry.get("source_path") or ""))
            recorded = int(entry.get("source_size") or 0)
            commit_at = float(entry.get("commit_at") or 0)
            actual_stat = source.stat()
        except (OSError, TypeError, ValueError):
            continue
        if actual_stat.st_mtime < now - active_within_s:
            continue
        if (
            source not in out
            and actual_stat.st_size > recorded
            and commit_at
            and now - commit_at >= min_age_s
        ):
            out.append(source)
    return out
