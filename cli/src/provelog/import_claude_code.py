"""Import Claude Code local transcripts.

Claude Code writes one JSONL per session under
``<CLAUDE_CONFIG_DIR or ~/.claude>/projects/<encoded-cwd>/<session>.jsonl``.
Only ``user`` and ``assistant`` records are conversation:
assistant content blocks are text / thinking / tool_use; tool results arrive
as ``user`` records with ``tool_result`` blocks.  ``isMeta`` and
``isSidechain`` records are CLI bookkeeping and sub-agent flows, not the main
conversation, and are skipped just as the viewer skips them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .chatview.model import usage_dict
from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state
from .textlines import read_jsonl_lines

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900


def claude_root() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude"


def session_files(root: Path | None = None) -> list[Path]:
    base = (root or claude_root()) / "projects"
    if not base.is_dir():
        return []
    from .chatview.readers import is_receipt_file
    out = [p for p in base.glob("*/*.jsonl")
           if p.is_file() and not is_receipt_file(p)]
    out.extend(p for p in base.glob("*/*/subagents/*.jsonl")
               if p.is_file() and not is_receipt_file(p))
    return sorted(out, key=lambda p: (p.parent.name == "subagents", -p.stat().st_mtime))


def _ms(value: Any, fallback: int = 0) -> int:
    from datetime import datetime
    if isinstance(value, (int, float)):
        number = float(value)
        return int(number * 1000) if number < 10_000_000_000 else int(number)
    if isinstance(value, str) and value.strip():
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return fallback
    return fallback


def _read(path: Path) -> tuple[list[dict[str, Any]], int]:
    try:
        lines = read_jsonl_lines(path)
    except OSError:
        return [], 0
    out = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            if index == len(lines) - 1:
                break
            continue
        if isinstance(row, dict):
            out.append(row)
                                                                           
                                                                             
                                                                            
    return out, len(out)


def _is_system_prompt(text: str) -> bool:
    """Claude's own continuation/compaction prompt is not a user turn."""
    return text.lstrip().startswith((
        "This session is being continued from a previous conversation",
        "<system-reminder>",
        "[Request interrupted by user]",
        "(not recorded - this part of the conversation happened before",
    ))


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(str(b.get("text") or "") for b in value
                         if isinstance(b, dict) and b.get("type") == "text").strip()
    return ""


_DOC_EXT = {"application/pdf": ".pdf", "text/plain": ".txt", "text/markdown": ".md",
            "text/csv": ".csv", "application/json": ".json"}


def _documents(content: Any) -> list[dict[str, Any]]:
    """Listing blocks for documents attached to a user message.

    The document itself is not copied into the record. Each attachment is
    recorded as what it was: name, path when Claude Code gave one, media type,
    size, and the SHA-256 of its bytes when they were inline. Before this, a
    ``document`` block was dropped without a trace, and a message that carried
    only a document was not recorded as a turn at all.
    """
    out: list[dict[str, Any]] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") not in ("document", "file"):
            continue
        source = block.get("source") if isinstance(block.get("source"), dict) else {}
        media = str(source.get("media_type") or block.get("media_type") or "")
        if media.lower().startswith("image/"):
            continue                                                  
        path = ""
        for key in ("path", "file_path", "url"):
            value = block.get(key) or source.get(key)
            if isinstance(value, str) and value:
                path = value
                break
        name = ""
        for value in (block.get("title"), block.get("name"), block.get("filename"),
                      source.get("filename"), source.get("name")):
            if isinstance(value, str) and value.strip():
                name = value.strip()
                break
        meta: dict[str, Any] = {}
        data = source.get("data")
        raw: bytes | None = None
        if isinstance(data, str) and data:
            if source.get("type") == "base64":
                try:
                    raw = base64.b64decode(data)
                except (ValueError, TypeError):
                    raw = None
            elif source.get("type") == "text":
                raw = data.encode("utf-8")
        if raw is not None:
            meta["bytes"] = len(raw)
            meta["sha256"] = hashlib.sha256(raw).hexdigest()
        if not name:
            name = (path.rstrip("/").rsplit("/", 1)[-1] if path
                    else f"document-{len(out) + 1}{_DOC_EXT.get(media.lower(), '')}")
        meta["name"] = name[:240]
        if path:
            meta["path"] = path[:1000]
        if media:
            meta["mediaType"] = media
        out.append({"type": "document", "document": meta})
    return out


def _usage(message: dict[str, Any]) -> dict[str, Any] | None:
    raw = message.get("usage")
    if not isinstance(raw, dict):
        return None
    usage = usage_dict({
        "input": raw.get("input_tokens"),
        "output": raw.get("output_tokens"),
        "cache_read": raw.get("cache_read_input_tokens"),
        "cache_write": raw.get("cache_creation_input_tokens"),
    })
    return _safe(usage) if usage else None


def _safe(value: Any) -> Any:
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    return value


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def _merge_assistant_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claude writes one row per content block of the same assistant message.

    Treat those rows as one assistant message (the same way the product shows
    a turn), not as a dozen separate replies. Streaming rows repeat the same
    message id; their blocks are deduplicated by block identity.
    """
    out: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    seen: set[tuple[str, str]] = set()

    def flush() -> None:
        nonlocal pending, seen
        if pending is not None:
            out.append(pending)
        pending = None
        seen = set()

    for row in rows:
        if row.get("type") != "assistant" or row.get("isMeta") or row.get("isSidechain"):
            flush()
            out.append(row)
            continue
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        mid = str(message.get("id") or row.get("uuid") or "")
        if not mid:
            flush()
            out.append(row)
            continue
        blocks = message.get("content")
        blocks = blocks if isinstance(blocks, list) else []
        if pending is None or str(pending.get("_message_id")) != mid:
            flush()
            pending = {**row, "message": {**message, "content": []},
                       "_message_id": mid}
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type") or "")
            key = (kind, str(block.get("id") or block.get("text")
                             or block.get("thinking") or ""))
            if key in seen:
                continue
            seen.add(key)
            pending["message"]["content"].append(block)
        if message.get("model"):
            pending["message"]["model"] = message["model"]
        if message.get("usage"):
            pending["message"]["usage"] = message["usage"]
    flush()
    return out


def ingest(path: Path, store: Any, *, key: Any | None = None,
           stream: str | None = None, now: float | None = None,
           since_ms: int | None = None) -> dict[str, Any]:
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    rows, consumed = _read(path)
    if not rows:
        return {"session_id": path.stem, "skipped": "empty"}
    from .session_kind import claude_identity
    session_id, parent = claude_identity(path)
    if parent and store.locate(parent) is None:
        return {"session_id": session_id, "skipped": "parent_unavailable"}
                                                                                
    if parent:
        rows = [{**r, "isSidechain": False} for r in rows]
    stream = stream or f"local-claude-code-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)
    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=consumed)
        return {"session_id": session_id, "skipped": "legacy_watermark"}

    watermark = int(state.get("host_seq", -1))
    deleted_at = int(state.get("deleted_at_ms") or 0)
    baseline = since_ms if folder is None else None
    selected = [
        (i, row) for i, row in enumerate(rows)
        if i >= watermark
        and (baseline is None or _ms(row.get("timestamp")) > baseline)
        and (not deleted_at or _ms(row.get("timestamp")) > deleted_at)
    ]
    if folder is None and not selected:
        if state and deleted_at:
            return {"session_id": session_id, "skipped": "deleted_no_new_events"}
        if since_ms is not None:
            return {"session_id": session_id, "skipped": "before_baseline"}
    selected = [(i, r) for i, r in selected]
    selected = _merge_assistant_rows([r for _, r in selected])

    title = ""
    cwd = ""
    created = 0
    version = ""
    surface = ""
    for row in rows:
        entrypoint = row.get("entrypoint")
        if not surface and isinstance(entrypoint, str):
            low = entrypoint.lower()
            if "desktop" in low:
                surface = "desktop"
            elif "vscode" in low or "vs-code" in low:
                surface = "vscode"
            elif "cli" in low:
                surface = "cli"
        if not cwd and isinstance(row.get("cwd"), str):
            cwd = row["cwd"]
        if not version and isinstance(row.get("version"), str):
            version = row["version"]
        if not created and isinstance(row.get("timestamp"), str):
            created = _ms(row["timestamp"])
        if row.get("type") == "ai-title" and row.get("aiTitle"):
            title = str(row["aiTitle"]).strip()
        elif row.get("type") == "summary" and row.get("summary"):
            title = str(row["summary"]).strip()
    if not title:
        for row in rows:
            if row.get("type") == "user" and not row.get("isMeta"):
                text = _text((row.get("message") or {}).get("content"))
                if text and not text.startswith("<"):
                    title = text.splitlines()[0][:120]
                    break
    title = title or session_id

    key = key or _device_key()
    if folder is None:
        meta = {"host": "claude-code", "host_session_id": session_id,
                "source": "host_import", "imported_from": str(path),
                "capture_point": "host_import", "title": title}
        if parent:
            meta.update(parent_session=parent, session_role="subagent")
        if cwd:
            meta["project"] = cwd
        if version:
            meta["host_version"] = version
        if surface:
            meta["surface"] = surface
        if since_ms is not None:
            meta["capture_from_ms"] = since_ms
            meta["head_omitted"] = bool(created and created < since_ms)
        fr = Provelog(stream, key=key, store=store, flush=False, float_text=True)
        fr.chain._clock = _clock_for(created or int(stamp * 1000))
        records = [fr.start(meta)]
    else:
        fr = _resume_recorder(store, session_id, stream, key, manifest, state)
        if fr is None:
            return {"session_id": session_id, "skipped": "no_chain"}
        records = []

    def emit(action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
        rec = _emit(fr, action, payload, ms)
        records.append(rec)
        return rec

    from . import attachments as attachments_mod
    pending_images: list[tuple[bytes, dict[str, Any]]] = []
    image_no = 0

    def result_blocks(value: Any) -> list[dict[str, Any]]:
        """Claude tool results can contain text and base64 screenshots."""
        nonlocal image_no
        if isinstance(value, str):
            return [{"type": "text", "text": value}] if value else []
        out: list[dict[str, Any]] = []
        for block in value if isinstance(value, list) else []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                text = str(block.get("text") or "")
                if text:
                    out.append({"type": "text", "text": text})
            elif kind == "image":
                source = block.get("source") or {}
                if source.get("type") != "base64" or not source.get("data"):
                    continue
                try:
                    data = base64.b64decode(str(source["data"]))
                except (ValueError, TypeError):
                    continue
                media = str(source.get("media_type") or "image/png")
                image_no += 1
                ext = {"image/png": ".png", "image/jpeg": ".jpg",
                       "image/jpg": ".jpg", "image/webp": ".webp"}.get(media, ".bin")
                name = f"screenshot-{image_no}{ext}"
                digest = hashlib.sha256(data).hexdigest()
                meta = {"attachmentId": "sha256:" + digest, "mediaType": media,
                        "bytes": len(data), "name": name}
                entry = attachments_mod.entry_for(meta)
                pending_images.append((data, entry))
                out.append({"type": "image", "attachment": {
                    "attachmentId": meta["attachmentId"], "mediaType": media,
                    "bytes": len(data), "name": name}})
        return out

    turn = 0
    tool_calls = tool_results = 0
    for row in selected:
        if row.get("isMeta") or row.get("isSidechain"):
            continue
        kind = row.get("type")
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        ms = _ms(row.get("timestamp")) or created
        if kind == "user":
            content = message.get("content")
            blocks = content if isinstance(content, list) else []
            results = [b for b in blocks
                       if isinstance(b, dict) and b.get("type") == "tool_result"]
            if results:
                for block in results:
                    payload = {"call_id": str(block.get("tool_use_id") or ""),
                               "content": result_blocks(block.get("content")),
                               "is_error": bool(block.get("is_error"))}
                    if turn:
                        payload["turn"] = turn
                    emit("tool_result", payload, ms)
                    tool_results += 1
                continue
            text = _text(content)
            user_images = attachments_mod.inline_images(content, prefix="user-image")
            user_refs = attachments_mod.image_refs(content, prefix="user-image")
            user_docs = _documents(content)
            if (text or user_images or user_refs or user_docs) and not _is_system_prompt(text):
                turn += 1
                user_content = [{"type": "text", "text": text}] if text else []
                for block, data, _name in user_images:
                    entry = attachments_mod.entry_for(block["attachment"])
                    pending_images.append((data, entry))
                    user_content.append(block)
                user_content.extend(user_refs)
                user_content.extend(user_docs)
                emit("user_message", {
                    "content": user_content,
                    "id": str(row.get("uuid") or f"{session_id}-u-{turn}"),
                    "origin": "user", "turn": turn,
                }, ms)
        elif kind == "assistant":
            content = message.get("content")
            blocks = content if isinstance(content, list) else []
            body: dict[str, Any] = {"id": str(row.get("uuid") or ""),
                                    "turn": turn}
            parts = []
            calls = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = str(block.get("text") or "")
                    if text:
                        parts.append({"type": "text", "text": text})
                elif block.get("type") == "thinking":
                    text = str(block.get("thinking") or "")
                    if text:
                        parts.append({"type": "thinking", "text": text})
                elif block.get("type") == "tool_use":
                    calls.append(block)
            if parts:
                body["content"] = parts
                model = message.get("model")
                if isinstance(model, str) and model:
                    body["model"] = model
                    body["provider"] = "anthropic"
                usage = _usage(message)
                if usage:
                    body["usage"] = usage
                emit("assistant_message", body, ms)
            for call in calls:
                raw_args = call.get("input")
                args = _safe(raw_args) if isinstance(raw_args, (dict, list)) else {}
                call_id = str(call.get("id") or "")
                payload = {"tool": str(call.get("name") or ""), "call_id": call_id}
                payload.update(fr._content("args", args))
                if turn:
                    payload["turn"] = turn
                emit("tool_call", payload, ms)
                tool_calls += 1

    committed = False
    if fr.chain._open:
        since_commit = stamp - float(state.get("commit_at") or 0)
        pending = int(fr.chain._seq) - int(state.get("commit_count") or 0)
        if records and (state.get("commit_count") is None
                        or pending >= COMMIT_EVERY_RECORDS
                        or since_commit >= COMMIT_EVERY_S):
            fr.chain._clock = _clock_for(int(stamp * 1000))
            records.append(fr.chain.commit(reason="auto"))
            committed = True
        elif not records and state and state.get("commit_count") is None:
            fr.chain._clock = _clock_for(int(stamp * 1000))
            records.append(fr.chain.commit(reason="backfill"))
            committed = True

    if records:
        store.append_records(stream, records, defer_index=False)
        if pending_images:
            folder = store.locate(session_id)
            if folder is not None:
                added = []
                for data, entry in pending_images:
                    target = Path(folder) / entry["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    try:
                        os.chmod(target, 0o600)
                    except OSError:
                        pass
                    added.append(entry)
                store.add_attachments(session_id, added)
        store.rebuild_md(session_id)

    if title:
        try:
            store.update_title(session_id, title)
        except (AttributeError, OSError):
            pass
    capture = {
        "host_seq": consumed,
        "source_path": str(path),
        "source_mtime": path.stat().st_mtime,
        "source_size": path.stat().st_size,
        "chain_id": fr.chain.session_id,
        "capture_point": "host_import",
        "verification_level": "signed_local",
    }
    if committed:
        capture["commit_at"] = stamp
        capture["commit_count"] = int(fr.chain._seq)
    store.set_session_capture(session_id, **capture)
    return {"session_id": session_id, "stream": stream, "added": len(records),
            "tool_calls": tool_calls, "tool_results": tool_results,
            "committed": committed}


def watch_once(store: Any, now: float | None = None, key: Any | None = None,
               since_ms: int | None = None) -> list[dict[str, Any]]:
    import time as _time
    stamp = now if now is not None else _time.time()
    out: list[dict[str, Any]] = []
    for path in session_files():
        from .session_kind import claude_identity
        session_id, _parent = claude_identity(path)
        try:
            stat = path.stat()
            state = store.session_capture(session_id)
            if (since_ms is not None and not state
                    and stat.st_mtime * 1000 < since_ms):
                continue
            if (state.get("source_mtime") == stat.st_mtime
                    and state.get("source_size") == stat.st_size
                    and state.get("commit_count") is not None):
                continue
            out.append(ingest(path, store, key=key, now=stamp, since_ms=since_ms))
        except Exception as exc:
            out.append({"session_id": session_id,
                        "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out
