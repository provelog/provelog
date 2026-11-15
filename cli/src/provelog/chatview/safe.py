"""ASCII-safe path components for export filenames (untrusted ids/hosts)."""

from __future__ import annotations

import hashlib

_SAFE_COMPONENT = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def safe_component(raw: str, fallback_prefix: str = "session") -> str:
    raw = raw or ""
    if len(raw) <= 80 and raw not in (".", "..") and set(raw) <= _SAFE_COMPONENT:
        return raw
    kept = ("".join(c if c in _SAFE_COMPONENT else "_" for c in raw[:40])
            .strip("._") or fallback_prefix)
    return f"{kept}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"
