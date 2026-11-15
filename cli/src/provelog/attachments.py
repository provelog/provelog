"""Image (and other binary) attachments referenced by a record.

A DSH message does not carry image bytes inline: it carries
``{"type": "image", "attachment": {"attachmentId": "sha256:…", "mediaType":
"image/png", "bytes": 63108, "name": "shot.png"}}`` and the bytes live in
the harness's own store (``$DSH_HOME/attachments/v1/objects/<aa>/<hash>``).

An evidence record must not depend on another program's cache, so the bytes
are copied into the session folder's ``attachments/`` when the session is
imported, named by their content hash, and listed in ``manifest.attachments``.
The viewer then serves them from the folder it is already reading.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any

_MEDIA_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
    "application/pdf": ".pdf",
}

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def valid_digest(digest: str) -> bool:
    """True when the value is a real content hash: exactly 64 hex chars.

    Host transcripts are untrusted input and the digest is used in file paths,
    so anything that is not a real content hash is dropped before it reaches
    ``entry_for``'s paths.
    """
    return bool(_HEX64.fullmatch(str(digest or "")))


def content_hash(attachment_id: str) -> str:
    """``sha256:abc…`` -> ``abc…`` (the harness prefixes the algorithm)."""
    value = str(attachment_id or "")
    return value.split(":", 1)[1] if value.startswith("sha256:") else value


def extension(media_type: str) -> str:
    return _MEDIA_EXT.get(str(media_type or "").lower(), "")


def dsh_object(dsh_home: Path, attachment_id: str) -> Path | None:
    """The harness's own copy of an attachment, when it is still there."""
    digest = content_hash(attachment_id)
    if not valid_digest(digest):
        return None
    candidate = Path(dsh_home) / "attachments" / "v1" / "objects" / digest[:2] / digest
    return candidate if candidate.is_file() else None


def entry_for(meta: dict[str, Any]) -> dict[str, Any] | None:
    """The manifest entry for one attachment block's ``attachment`` object.

    None when the attachment id is not a real content hash: a hostile
    transcript must not be able to pick the file name (or path) this writes.
    """
    digest = content_hash(meta.get("attachmentId") or "")
    if not valid_digest(digest):
        return None
    media_type = str(meta.get("mediaType") or "")
    name = str(meta.get("name") or "")
    ext = extension(media_type) or Path(name).suffix or ".bin"
    return {
        "file": f"{digest}{ext}",
        "path": f"attachments/{digest}{ext}",
        "sha256": f"sha256:{digest}",
        "bytes": int(meta.get("bytes") or 0),
        "media_type": media_type,
        "name": name,
        "width": meta.get("width"),
        "height": meta.get("height"),
    }


def _safe_target(folder: Path, rel_path: str) -> Path | None:
    """Resolve ``folder/<rel_path>`` and require the result to stay inside
    ``folder/attachments``. None when it does not (defense in depth: entry
    paths are already hex-validated, but a write must never escape on any
    future code path either).
    """
    value = str(rel_path or "")
    if not value:
        return None
    path = Path(folder) / value
    try:
        resolved = path.resolve()
        base = (Path(folder) / "attachments").resolve()
    except OSError:
        return None
    if base not in resolved.parents:
        return None
    return resolved


def copy_into(folder: Path, object_path: Path, entry: dict[str, Any]) -> bool:
    """Copy one blob into the session folder. False when it is already there."""
    if not isinstance(entry, dict):
        return False
    target = _safe_target(folder, entry.get("path"))
    if target is None or target.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(object_path, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return True


def resolve(folder: Path, file_name: str) -> Path | None:
    """A file inside THIS session's attachments dir, or None.

    ``file_name`` is a bare name: no separators, no traversal.
    """
    name = str(file_name or "")
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    path = Path(folder) / "attachments" / name
    try:
        resolved = path.resolve()
        base = (Path(folder) / "attachments").resolve()
    except OSError:
        return None
    if base not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None


                                                                             
                     
 
                                                                      
                                                                               
                                                                              
                                                                         
                                                                             
                                                                           
                                                                             

_DATA_URL = re.compile(r"^data:([^;,]+)?(;base64)?,(.*)$", re.S | re.I)


def _decode_data_url(value: str) -> tuple[bytes, str] | None:
    match = _DATA_URL.match(str(value or "").strip())
    if not match:
        return None
    media = str(match.group(1) or "application/octet-stream").lower()
    payload = match.group(3) or ""
    try:
        data = (base64.b64decode(payload, validate=False) if match.group(2)
                else payload.encode("utf-8"))
    except (ValueError, TypeError):
        return None
    return (data, media) if data else None


def _block_image(block: Any) -> tuple[bytes, str, str] | None:
    """Extract bytes/media/name from one supported image block."""
    if not isinstance(block, dict):
        return None
    kind = str(block.get("type") or "").lower()

    def result(data: Any, media: Any = "", name: Any = "") -> tuple[bytes, str, str] | None:
        try:
            raw = base64.b64decode(str(data), validate=False)
        except (ValueError, TypeError):
            return None
        if not raw:
            return None
        return raw, str(media or "application/octet-stream").lower(), str(name or "")

                               
    source = block.get("source")
    if kind == "image" and isinstance(source, dict):
        if str(source.get("type") or "").lower() == "base64":
            return result(source.get("data"), source.get("media_type"), block.get("name"))

                                
    inline = block.get("inline_data") or block.get("inlineData")
    if isinstance(inline, dict):
        return result(inline.get("data"), inline.get("mime_type") or inline.get("mimeType"),
                      block.get("name"))

                                                                      
    if kind in ("image_url", "input_image", "output_image"):
        image_url = block.get("image_url") or block.get("url")
        if isinstance(image_url, dict):
            image_url = image_url.get("url")
        if isinstance(image_url, str):
            decoded = _decode_data_url(image_url)
            if decoded:
                return decoded[0], decoded[1], str(block.get("name") or "")
        return None

                                                                  
    if kind in ("file", "image"):
        image_url = block.get("url") or block.get("data")
        if isinstance(image_url, str):
            decoded = _decode_data_url(image_url)
            if decoded:
                media = str(block.get("mime") or block.get("mimeType") or decoded[1])
                return decoded[0], media, str(block.get("name") or "")
        return None

                                                
    if kind == "image" and block.get("data"):
        return result(block.get("data"), block.get("media_type") or block.get("mime_type"),
                      block.get("name"))
    return None


def inline_images(value: Any, *, prefix: str = "image") -> list[tuple[dict[str, Any], bytes, str]]:
    """Find image blocks in a content tree and return record blocks + bytes.

    The returned block is safe to put in a signed payload; the caller only
    needs to save ``(data, entry)`` according to its pending-attachment flow.
    """
    found: list[tuple[dict[str, Any], bytes, str]] = []
    seen: set[str] = set()
    counter = 0

    def walk(item: Any) -> None:
        nonlocal counter
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, dict):
            return
        extracted = _block_image(item)
        if extracted is not None:
            data, media, supplied_name = extracted
            digest = hashlib.sha256(data).hexdigest()
            if digest not in seen:
                seen.add(digest)
                counter += 1
                ext = extension(media) or Path(supplied_name).suffix or ".bin"
                name = supplied_name or f"{prefix}-{counter}{ext}"
                meta = {"attachmentId": "sha256:" + digest, "mediaType": media,
                        "bytes": len(data), "name": name}
                found.append(({"type": "image", "attachment": {
                    "attachmentId": meta["attachmentId"], "mediaType": media,
                    "bytes": len(data), "name": name}}, data, name))
            return
                                                                          
                                                                               
        for key in ("content", "parts", "message", "item", "data"):
            child = item.get(key)
            if isinstance(child, (list, dict)):
                walk(child)

    walk(value)
    return found


def save_pending(store: Any, session_id: str, folder: Path | None,
                 pending: list[tuple[bytes, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Write pending inline-image bytes and register them in the manifest.

    Entries rejected by ``entry_for`` (None) and entries whose path would
    leave ``folder/attachments`` are skipped, never written.
    """
    if not pending or folder is None:
        return []
    added: list[dict[str, Any]] = []
    for data, entry in pending:
        if not isinstance(entry, dict):
            continue
        target = _safe_target(folder, entry.get("path"))
        if target is None or target.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        added.append(entry)
    if added and hasattr(store, "add_attachments"):
        store.add_attachments(session_id, added)
    return added


def image_refs(value: Any, *, prefix: str = "image") -> list[dict[str, Any]]:
    """Remote image references, recorded without fetching the bytes.

    A remote URL is evidence the host returned that URL; it is not a copy of
    the image.  Keep it in the signed payload so the trajectory does not lose
    the reference, but never make network I/O during import.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(url: str, media: str, name: str) -> None:
        if not url.startswith(("http://", "https://")) or url in seen:
            return
        seen.add(url)
        block: dict[str, Any] = {"type": "image", "url": url,
                                 "name": name or f"{prefix}-{len(out) + 1}"}
        if media:
            block["media_type"] = media
        out.append(block)

    def walk(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, dict):
            return
        kind = str(item.get("type") or "").lower()
        if kind in ("image_url", "input_image", "output_image", "file", "image"):
            value = item.get("image_url") or item.get("url") or item.get("source")
            if isinstance(value, dict):
                value = value.get("url")
            if isinstance(value, str):
                add(value, str(item.get("mime") or item.get("mimeType") or
                               item.get("media_type") or ""),
                    str(item.get("name") or ""))
        for key in ("content", "parts", "message", "item", "data"):
            child = item.get(key)
            if isinstance(child, (list, dict)):
                walk(child)

    walk(value)
    return out
