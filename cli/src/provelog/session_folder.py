"""The user-facing session folder: ``~/ProveLog Records/<agent>/<title>-id-<id>/``.

This module is the format/path layer only. It does not decide which store is
canonical yet; it gives the rest of the product one place to create a
human-readable session folder and its small manifest.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT_NAME = "ProveLog Records"
TRANSCRIPT_NAME = "transcript-preview.md"
TITLE_MAX = 80

                                                                            
                                                                 
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')
_SPACE = re.compile(r"\s+")


def sessions_root(home: Path | None = None) -> Path:
    """The visible, user-owned evidence root: ``~/ProveLog Records/<agent>/<session>/``."""
    base = Path.home() if home is None else Path(home)
    return base / ROOT_NAME


def sanitize_component(text: str, *, max_len: int = TITLE_MAX, fallback: str = "untitled") -> str:
    """Make one path component safe on macOS, Windows and Linux.

    Keeps Chinese/emoji, normalizes to NFC, replaces path separators and
    control characters, collapses whitespace, strips trailing dots/spaces,
    avoids Windows device names, and truncates by Unicode characters.
    """
    value = unicodedata.normalize("NFC", str(text or ""))
    value = _UNSAFE.sub("-", value)
    value = _SPACE.sub(" ", value).strip(" .")
    if not value:
        value = fallback
    if len(value) > max_len:
        value = value[:max_len].rstrip(" .-")
    if not value:
        value = fallback
    if value.upper() in _WINDOWS_RESERVED:
        value = f"{value}_"
    return value


def sanitize_session_id(session_id: str) -> str:
    """Session ids are opaque, but must still be one safe path component.

    A harness prefix that repeats the word the folder already carries is
    dropped: ``session-51…`` would otherwise read ``sessionid-session-51…``.
    """
    raw = str(session_id or "")
    for prefix in ("session-", "session_", "sess-", "sess_"):
        if raw.lower().startswith(prefix) and len(raw) > len(prefix) + 4:
            raw = raw[len(prefix):]
            break
    value = sanitize_component(raw, max_len=96, fallback="unknown")
                                                                              
    return value.replace("-id-", "-id_")


def folder_name(title: str, session_id: str) -> str:
    """A title is optional. With one: ``hello-id-123``; without: ``sessionid-123``."""
    clean = sanitize_component(title, fallback="")
    sid = sanitize_session_id(session_id)
    if not clean:
        return f"sessionid-{sid}"
    return f"{clean}-id-{sid}"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


@dataclass
class SessionFolder:
    """One session's visible folder and its two canonical files."""

    path: Path
    session_id: str
    agent: str
    title: str
    created_at: str
    manifest: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, *, root: Path | None = None, agent: str, title: str,
               session_id: str, created_at: str | None = None) -> SessionFolder:
        agent_part = sanitize_component(agent, max_len=40, fallback="agent")
        base = (root or sessions_root()) / agent_part
        base.mkdir(parents=True, exist_ok=True)
        _chmod(base.parent, 0o700)
        _chmod(base, 0o700)
        path = base / folder_name(title, session_id)
        path.mkdir(parents=True, exist_ok=True)
        _chmod(path, 0o700)
        attachments = path / "attachments"
        attachments.mkdir(exist_ok=True)
        _chmod(attachments, 0o700)
        if not (attachments / "index.md").exists():
            idx = attachments / "index.md"
            idx.write_text("# Attachments\n\n", encoding="utf-8")
            _chmod(idx, 0o600)
        created = created_at or _now()
        folder = cls(path=path, session_id=session_id, agent=agent, title=title,
                     created_at=created)
        folder.manifest = {
            "v": 2,
            "session_id": session_id,
            "agent": agent,
            "title": title,
            "display_title": title,
            "created_at": created,
            "last_activity": created,
            "canonical": ["trajectory.jsonl.zst", TRANSCRIPT_NAME, "manifest.json"],
            "files": {},
            "records": 0,
            "frames": [],
            "chain": {"records": 0, "head": ""},
            "sources": {},
            "coverage": {"model_calls": 0, "tool_calls": 0, "gaps": []},
            "attachments": [],
            "anchors": [],
        }
        folder.write_manifest()
        if not (path / TRANSCRIPT_NAME).exists():
            heading = title or f"sessionid-{session_id}"
            folder.write_session(
                "This is a preview of the conversation only. "
                "The complete session record is `trajectory.jsonl.zst`\n\n"
                f"# {heading}\n")
        return folder

    @property
    def session_md(self) -> Path:
        return self.path / TRANSCRIPT_NAME

    @property
    def manifest_json(self) -> Path:
        return self.path / "manifest.json"

    def write_session(self, text: str) -> None:
        self.session_md.write_text(text, encoding="utf-8")
        _chmod(self.session_md, 0o600)

    def append_session(self, text: str) -> None:
        with self.session_md.open("a", encoding="utf-8", newline="\n") as f:
            f.write(text)
        _chmod(self.session_md, 0o600)

    def write_manifest(self) -> None:
        self.manifest["last_activity"] = _now()
        self.manifest_json.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        _chmod(self.manifest_json, 0o600)

    def set_display_title(self, title: str) -> None:
        """Title changes update the index, never rename the folder (Codex-style)."""
        self.manifest["display_title"] = title
        self.write_manifest()
