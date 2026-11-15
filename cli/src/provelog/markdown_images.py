"""Copy images referenced by Markdown into the session's evidence folder.

A host transcript often contains ``![alt](relative/path.png)`` in a message.
The signed text names the original path; this module stores the bytes under
``attachments/`` and records a mapping in ``manifest.markdown_images`` so the
viewer can serve the captured copy without rewriting any signed record.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
from pathlib import Path
from typing import Any

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".bmp", ".svg"}


def _texts(record: dict[str, Any]) -> list[str]:
    if str(record.get("action_type") or "") not in ("user_message", "assistant_message"):
        return []
    payload = record.get("payload") or {}
    blocks = payload.get("content")
    out: list[str] = []
    if isinstance(blocks, str):
        out.append(blocks)
    elif isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict) and block.get("type") in (None, "text"):
                text = block.get("text")
                if isinstance(text, str):
                    out.append(text)
    return out


def _resolve(path: str, project: Path) -> Path | None:
    raw = str(path or "").strip()
    if not raw or raw.startswith(("#", "//")) or re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I):
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = project / candidate
    try:
        candidate = candidate.resolve()
    except OSError:
        return None
    if not candidate.is_file() or candidate.suffix.lower() not in _IMAGE_EXT:
        return None
    return candidate


def capture(store: Any, session_id: str) -> int:
    """Copy every resolvable Markdown image for one session; return new count."""
    folder = store.locate(session_id)
    if folder is None:
        return 0
    manifest = store._manifest(folder)
    project_raw = str(manifest.get("project") or "")
    if not project_raw:
        return 0
    project = Path(project_raw).expanduser()
    mapping = dict(manifest.get("markdown_images") or {})
    known = {str(a.get("file")) for a in (manifest.get("attachments") or [])}
    added = 0
    for event in store.session_events(session_id):
        record = event if isinstance(event, dict) and "action_type" in event else (
            event.get("record") if isinstance(event, dict) else None)
        if not isinstance(record, dict):
            continue
        for text in _texts(record):
            for match in _IMAGE_RE.finditer(text):
                original = match.group(1).replace("&amp;", "&").strip("<>")
                if original in mapping:
                    continue
                source = _resolve(original, project)
                if source is None:
                    continue
                try:
                    data = source.read_bytes()
                except OSError:
                    continue
                digest = hashlib.sha256(data).hexdigest()
                ext = source.suffix.lower()
                name = f"md-{digest[:16]}{ext}"
                target = folder / "attachments" / name
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.is_file():
                        shutil.copyfile(source, target)
                        os.chmod(target, 0o600)
                except OSError:
                    continue
                mapping[original] = {
                    "file": name,
                    "sha256": f"sha256:{digest}",
                    "bytes": len(data),
                    "source": str(source),
                }
                known.add(name)
                added += 1
    if added:
        entries = []
        for name in sorted(known):
            file = folder / "attachments" / name
            entries.append({
                "file": name,
                "path": f"attachments/{name}",
                "sha256": "sha256:" + hashlib.sha256(file.read_bytes()).hexdigest()
                if file.is_file() else "",
                "bytes": file.stat().st_size if file.is_file() else 0,
                "media_type": mimetypes.guess_type(name)[0] or "",
                "name": name,
            })
        store.set_session_fields(session_id, markdown_images=mapping,
                                 attachments=entries)
    return added


def lookup(manifest: dict[str, Any], original: str, folder: Path) -> Path | None:
    """The captured file for a Markdown path, if it is in this session."""
    entry = (manifest.get("markdown_images") or {}).get(str(original))
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("file") or "")
    candidate = folder / "attachments" / name
    try:
        candidate = candidate.resolve()
        base = (folder / "attachments").resolve()
    except OSError:
        return None
    if base not in candidate.parents or not candidate.is_file():
        return None
    return candidate
