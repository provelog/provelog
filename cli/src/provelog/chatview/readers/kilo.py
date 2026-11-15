"""kilo reader: ~/.local/share/kilo/kilo.db.

Kilo Code descends from opencode and keeps the same session/message/part
schema; the parsing lives in opencode_db.py and this module only names
the database.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..model import SessionSummary, SessionView
from . import opencode_db


def _data_root(home: Path) -> Path:
    """~/.local/share, or $XDG_DATA_HOME when set (the XDG Base Directory
    standard kilo follows; the database moves with it)."""
    env = os.environ.get("XDG_DATA_HOME")
    return Path(env) if env else home / ".local" / "share"


def install_dir(home: Path) -> Path:
    """The directory whose existence marks this CLI as installed."""
    return _data_root(home) / "kilo"


def data_dir(home: Path) -> Path:
    return _data_root(home) / "kilo" / "kilo.db"


def discover(home: Path) -> list[SessionSummary]:
    return opencode_db.discover_db(data_dir(home), "kilo")


def load(home: Path, session_id: str) -> SessionView | None:
    return opencode_db.load_db(data_dir(home), "kilo", session_id)
