"""Read DeepSeek Harness's log-backed session title.

DSH stores one ``session.v4.jsonl.zstd`` per session. The title is written as
a log event by DSH's own session-title service; we only read it. If zstandard
is unavailable (or the file has no title), return "" - callers then use the
sessionid-only folder name.
"""

from __future__ import annotations

import json
from pathlib import Path


def _decompress(raw: bytes) -> bytes:
    try:
        import zstandard
        return zstandard.ZstdDecompressor().decompress(raw)
    except Exception:
        pass
    try:
        from compression import zstd                
        return zstd.decompress(raw)
    except Exception:
        return b""


def _title_from_lines(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if not isinstance(o, dict):
            continue
                                                                         
                                                                              
        kind = str(o.get("type") or "")
        if kind in ("session_title", "session-title", "session_info", "session_meta"):
            for key in ("title", "sessionTitle", "displayName", "label"):
                v = o.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        if kind == "session" and isinstance(o.get("title"), str) and o["title"].strip():
            return o["title"].strip()
        for key in ("sessionTitle", "displayName"):
            v = o.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for container in ("data", "payload", "session"):
            inner = o.get(container)
            if not isinstance(inner, dict):
                continue
            for key in ("title", "sessionTitle", "displayName", "label"):
                v = inner.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return ""


def dsh_title(home: Path, session_id: str) -> str:
    root = home / ".dsh" / "sessions"
    if not root.is_dir():
        return ""
    names = [session_id]
    if not session_id.startswith("session-"):
        names.append("session-" + session_id)
    for name in names:
        for path in root.glob(f"*/{name}/session.v4.jsonl.zstd"):
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            text = _decompress(raw).decode("utf-8", "replace")
            title = _title_from_lines(text)
            if title:
                return title
    return ""
