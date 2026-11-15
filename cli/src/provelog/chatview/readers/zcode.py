"""zcode reader: ~/.zcode/cli/db/db.sqlite.

Correction of an earlier assumption: ZCode's HOOK transcripts are
temporary (deleted right after each hook returns - that is what the
recorder's receipts work around), but the tool itself keeps a persistent
session database with the opencode-family session/message/part schema
(source: github.com/zai-org/ZCode, apps/zcode-cli/packages/adapters/
src/storage/session-store/rows.ts). The parsing lives in opencode_db.py;
this module only names the database. When the file is absent the viewer
reports the host as not run on this device - never as "no transcripts".
"""

from __future__ import annotations

from pathlib import Path

from ..model import SessionSummary, SessionView
from . import opencode_db


def install_dir(home: Path) -> Path:
    """The directory whose existence marks this CLI as installed (this
    host documents no config-dir environment variable - the standard
    home-relative path is the only official location)."""
    return home / ".zcode"


def data_dir(home: Path) -> Path:
    return home / ".zcode" / "cli" / "db" / "db.sqlite"


def discover(home: Path) -> list[SessionSummary]:
    return opencode_db.discover_db(data_dir(home), "zcode")


def load(home: Path, session_id: str) -> SessionView | None:
    return opencode_db.load_db(data_dir(home), "zcode", session_id)
