"""User pause/resume state for the log collector.

A user pause is an intentional gap: while it lasts, nothing is read and
nothing is recorded.  On resume the collector writes a signed
``lifecycle.resume`` record that names the gap, and skips the paused interval
by default.  Backfilling is a separate, explicit act; it must never be implied
by "stop", or stopping would be a lie.

The file is deliberately small and human-readable.  It lives outside the
evidence store: it is control state, not evidence.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

MODE_STRICT = "strict"
MODE_CATCH_UP = "catch_up"


def path() -> Path:
    return Path.home() / ".provelog" / "pause.json"


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def read() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, target)


def clear() -> None:
    path().unlink(missing_ok=True)


def request_pause(mode: str = MODE_STRICT) -> dict:
    """Record the user's intent to stop recording. Idempotent."""
    data = read()
    if data.get("paused"):
        return data
    data = {"paused": True, "at": now_iso(), "mode": mode}
    save(data)
    return data


def request_resume(mode: str | None = None) -> dict:
    """Turn a pause into a resume request for the collector to process."""
    data = read()
    if not data.get("paused"):
        return {}
    data["paused"] = False
    data["resumed_at"] = now_iso()
    data["done"] = False
    if mode:
        data["mode"] = mode
    save(data)
    return data


def is_paused() -> bool:
    return bool(read().get("paused"))
