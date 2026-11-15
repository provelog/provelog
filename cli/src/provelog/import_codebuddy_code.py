"""Import CodeBuddy Code CLI sessions.

CodeBuddy Code (the CLI) writes the same WorkBuddy-shaped JSONL under
``$CODEBUDDY_HOME/projects/...`` (default ``~/.codebuddy``).  The actual JSONL
reader lives in :mod:`provelog.import_workbuddy`; this module only supplies
the CodeBuddy home and the distinct ``codebuddy-code`` host label.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from . import import_workbuddy
from .import_dsh import _anchor_after_pass


def codebuddy_code_root() -> Path:
    env = os.environ.get("CODEBUDDY_HOME")
    return Path(env).expanduser() if env else Path.home() / ".codebuddy"


def session_files(root: Path | None = None) -> list[Path]:
    base = (root or codebuddy_code_root()) / "projects"
    if not base.is_dir():
        return []
    out = []
    for path in base.rglob("*.jsonl"):
        if not path.is_file():
            continue
        parts = {p.lower() for p in path.parts}
        if "tool-results" in parts or "attachments" in parts:
            continue
        out.append(path)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def ingest(path: Path, store: Any, **kwargs: Any) -> dict[str, Any]:
    return import_workbuddy.ingest(path, store, host="codebuddy-code", **kwargs)


def watch_once(store: Any, now: float | None = None, key: Any | None = None,
               since_ms: int | None = None) -> list[dict[str, Any]]:
    stamp = now if now is not None else time.time()
    out: list[dict[str, Any]] = []
    for path in session_files():
        try:
            stat = path.stat()
            state = store.session_capture(path.stem)
            if (since_ms is not None and not state
                    and stat.st_mtime * 1000 < since_ms):
                continue
            if (state.get("source_mtime") == stat.st_mtime
                    and state.get("source_size") == stat.st_size
                    and state.get("commit_count") is not None):
                continue
            out.append(ingest(path, store, key=key, now=stamp, since_ms=since_ms))
        except Exception as exc:
            out.append({"session_id": path.stem,
                        "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out
