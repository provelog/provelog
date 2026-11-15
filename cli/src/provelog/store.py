"""The one production store factory: folder-canonical SessionStore only."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .session_store import SessionStore


def default_store_root() -> str:
    return str(Path.home() / "ProveLog Records")


def resolve_store_path(cfg: dict | None = None) -> str:
    """The records directory every entry point must agree on.

    Precedence: ``PROVELOG_STORE``, then the config file's ``store`` key, then
    the default ``~/ProveLog Records``. ``status``, ``export``, ``doctor`` and
    the chat viewer all resolve through here so one flag cannot point them at
    different trees.
    """
    env = os.environ.get("PROVELOG_STORE")
    if env:
        return str(Path(env).expanduser())
    if cfg and cfg.get("store"):
        return str(Path(str(cfg["store"])).expanduser())
    return default_store_root()


def resolve_store_root_for_home(home: str | Path) -> Path:
    """The store root for one home directory (the chat viewer's resolution).

    Same precedence as ``resolve_store_path`` but reads that home's own
    ``.provelog.json`` instead of walking the current working directory.
    """
    env = os.environ.get("PROVELOG_STORE")
    if env:
        return Path(env).expanduser()
    cfg_file = Path(home) / ".provelog.json"
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    if isinstance(cfg, dict) and cfg.get("store"):
        return Path(str(cfg["store"])).expanduser()
    return Path(home) / "ProveLog Records"


def open_store(path: str | None = None, *, read_only: bool = False) -> SessionStore:
    root = Path(path).expanduser() if path else Path(resolve_store_path())
    if root.suffix.lower() == ".db" or root.is_file():
        raise ValueError("the SQLite sink is no longer a supported store; "
                         "use the folder store at ~/ProveLog Records")
    return SessionStore(str(root), read_only=read_only)
