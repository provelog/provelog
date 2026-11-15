"""Import omp coding-agent sessions.

omp is a pi fork and keeps the same append-only JSONL session format under
``$OMP_AGENT_DIR/sessions/<encoded-cwd>/*.jsonl`` (default
``~/.omp/agent/sessions``).  It adds a leading ``title`` row and a few
``custom``/``session_init`` bookkeeping rows, which the pi reader already
skips.  Keeping one reader means a format fix cannot drift between the two
hosts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import import_pi


def session_dir() -> Path:
    return import_pi.pi_session_dir("omp")


def session_files(root: Path | None = None) -> list[Path]:
    return import_pi.session_files(root, host="omp")


def ingest(path: Path, store: Any, *, key: Any | None = None,
           stream: str | None = None, now: float | None = None,
           since_ms: int | None = None) -> dict[str, Any]:
    return import_pi.ingest(path, store, key=key, stream=stream, now=now,
                            since_ms=since_ms, host="omp")


def watch_once(store: Any, now: float | None = None, key: Any | None = None,
               since_ms: int | None = None) -> list[dict[str, Any]]:
    return import_pi.watch_once(store, now=now, key=key, since_ms=since_ms,
                                host="omp")
