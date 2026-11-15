"""Stamp badges, read from the canonical session manifests.

STAMPED  a non-pending anchor receipt is in the session's manifest
QUEUED   a queued anchor exists but its receipt has not been delivered
UNSTAMPED no anchor, or no recorded session
"""

from __future__ import annotations

from pathlib import Path

STAMPED = "stamped"
QUEUED = "queued"
UNSTAMPED = "unstamped"


def _manifest(home: Path, host: str, session_id: str) -> dict:
    """The session's manifest, through the same store resolution the viewer
    uses - PROVELOG_STORE and the config file included, not a hardcoded path."""
    import json

    from .records import store_root

    root = store_root(home)
    if not root.is_dir():
        return {}
    for mf in root.glob("*/*/manifest.json"):
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(m.get("session_id")) != str(session_id):
            continue
        if host and m.get("agent") and str(m.get("agent")) != str(host):
            continue
        return m
    return {}


def anchors_for(home: Path, host: str, session_id: str) -> list[dict]:
    """This conversation's stamps, not its subagents'.

    A folder can hold a parent and the child streams beneath it; each stream has
    its own coverage. An entry that names a different stream is that stream's
    evidence, and one that names no stream (an older manifest) only belongs here
    when the folder holds a single stream.
    """
    from ..session_store import own_stream_id

    manifest = _manifest(home, host, session_id)
    own = own_stream_id(manifest)
    rows = [a for a in (manifest.get("anchors") or []) if isinstance(a, dict)]
    if len(manifest.get("streams") or {}) <= 1:
        return rows
    return [a for a in rows if str(a.get("stream_id") or "") == own]


def _delivered(a: dict) -> bool:
    """A stamp with an actual receipt, not queued and not a lost claim."""
    return bool(a.get("receipt")) and not a.get("pending") and not a.get("failed")


def badge_for(host: str, session_id: str, source_path: str | None,
              project: str | None, home: Path) -> str:
    anchors = anchors_for(home, host, session_id)
    if any(_delivered(a) for a in anchors):
        return STAMPED
    if any(a.get("pending") for a in anchors):
        return QUEUED
    return UNSTAMPED


def anchor_receipt(home: Path, host: str, session_id: str) -> dict | None:
    anchors = [a for a in anchors_for(home, host, session_id) if _delivered(a)]
    if not anchors:
        return None
    latest = max(anchors, key=lambda a: int(a.get("covers_up_to", 0)))
    return latest.get("receipt")
