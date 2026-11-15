"""One ZIP holding several evidence packs, for convenience.

A collection is a container, not a merged bundle: every conversation keeps its
own bundle, signatures, anchors and attachments, and is verified on its own.
The container adds an index (``collection.json``) and one manifest covering all
member files; it deliberately does not claim the set is complete.
"""

from __future__ import annotations

import json
import os
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .canonical import sha256_hex
from .pack import _DISCLAIMER, _offline_assets

COLLECTION_FORMAT = "provelog.collection/1"
_FIXED_TIME = (2026, 1, 1, 0, 0, 0)

_GUIDE = """ProveLog evidence collection
================================

This ZIP holds several conversations. Each one is an independent evidence
pack: its own bundle, its own signatures, its own timestamps and attachments.

How to verify it
----------------
1. Double-click verify-offline.html, then drop this whole ZIP onto the page.
   It verifies every conversation locally, with no install and no network.
2. On the command line:
       provelog verify <this file>.zip

What this proves
----------------
Every member is verified on its own: its records are signed, hash-chained and
(when a trusted timestamp is present) bound to an RFC 3161 time.

What this does not prove
------------------------
This container is a convenience wrapper. It does not prove that the set is
complete: someone could remove one conversation from this ZIP and every
remaining conversation would still verify. Treat the per-conversation result
as the evidence, and the container as the envelope.
"""


def _zinfo(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def write_collection(out: Path, members: Iterable[dict[str, Any]], *,
                     overwrite: bool = False) -> Path:
    """Write the collection to `out` atomically; return the final path.

    `members` yields dicts: ``name`` (safe member directory name), ``bundle``
    (the bundle dict), ``files`` (relative member files such as
    ``attachments/<name>``) and ``meta`` (session_id/stream_id/agent/title/
    records/stamped). Member bundles are written as-is; nothing is re-signed.
    """
    out = Path(out)
    if out.exists() and not overwrite:
        raise FileExistsError(str(out))
    tmp = out.with_name(out.name + ".tmp")
    if tmp.exists():
        tmp.unlink()

    hashes: dict[str, dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []

    def add(z: zipfile.ZipFile, name: str, data: bytes) -> None:
        z.writestr(_zinfo(name), data)
        hashes[name] = {"sha256": sha256_hex(data), "bytes": len(data)}

    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in sorted(_offline_assets().items()):
                add(z, name, data)

            for member in members:
                base = "sessions/" + member["name"] + "/"
                bundle_bytes = json.dumps(member["bundle"], indent=2,
                                          ensure_ascii=False).encode("utf-8")
                add(z, base + "bundle.json", bundle_bytes)
                for rel, data in sorted((member.get("files") or {}).items()):
                    add(z, base + rel.lstrip("/"), data)
                meta = dict(member.get("meta") or {})
                meta["path"] = base + "bundle.json"
                meta["bundle_sha256"] = sha256_hex(bundle_bytes)
                entries.append(meta)

            collection = {
                "format": COLLECTION_FORMAT,
                "created_at": _now(),
                "tool": _tool_version(),
                "members": entries,
                "note": ("the container is a convenience wrapper; every member "
                         "verifies on its own, and the set itself is not proven "
                         "complete"),
            }
            add(z, "collection.json",
                json.dumps(collection, indent=2, ensure_ascii=False).encode("utf-8"))
            add(z, "VERIFY.txt", _GUIDE.encode("utf-8"))
            manifest = {
                "format": COLLECTION_FORMAT,
                "members": len(entries),
                "files": hashes,
                "disclaimer": _DISCLAIMER,
            }
            add(z, "MANIFEST.json",
                json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, out)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return out


def _now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tool_version() -> str:
    from . import __version__
    return f"provelog {__version__}"


def read_collection(path: str | Path
                    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """(collection metadata, members) for a collection ZIP, else None.

    Each member carries ``meta``, ``bundle`` and ``files`` (relative member
    paths, e.g. ``attachments/x``), ready for ``verify_bundle``.
    """
    p = Path(path)
    if not zipfile.is_zipfile(p):
        return None
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        if "collection.json" not in names:
            return None
        try:
            meta = json.loads(z.read("collection.json").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(meta, dict) or meta.get("format") != COLLECTION_FORMAT:
            return None
        members: list[dict[str, Any]] = []
        for entry in meta.get("members") or []:
            if not isinstance(entry, dict):
                continue
            rel = str(entry.get("path") or "")
            if not rel or rel not in names:
                raise ValueError(f"collection member is missing: {rel or '(no path)'}")
            base = rel.rsplit("/", 1)[0] + "/"
            bundle = json.loads(z.read(rel).decode("utf-8"))
            files = {n[len(base):]: z.read(n) for n in names
                     if n.startswith(base) and n != rel}
            members.append({"meta": entry, "bundle": bundle, "files": files})
        return meta, members
