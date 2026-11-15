"""opencode reader: ~/.local/share/opencode/opencode.db (XDG data dir).

opencode, kilo and zcode share one storage schema; the parsing lives in
opencode_db.py and this module only names the database. Payload shapes
verified against the real opencode database.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..model import SessionSummary, SessionView
from . import opencode_db


def _data_root(home: Path) -> Path:
    """~/.local/share, or $XDG_DATA_HOME when set (the XDG Base Directory
    standard opencode follows; the database moves with it)."""
    env = os.environ.get("XDG_DATA_HOME")
    return Path(env) if env else home / ".local" / "share"


def install_dir(home: Path) -> Path:
    """The directory whose existence marks this CLI as installed."""
    return _data_root(home) / "opencode"


def data_dir(home: Path) -> Path:
    return _data_root(home) / "opencode" / "opencode.db"


def discover(home: Path) -> list[SessionSummary]:
    return opencode_db.discover_db(data_dir(home), "opencode")


def load(home: Path, session_id: str) -> SessionView | None:
    return opencode_db.load_db(data_dir(home), "opencode", session_id)
