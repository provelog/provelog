"""Import Cline CLI conversations (JSON snapshots + manifest).

Cline CLI keeps one directory per session under
``~/.cline/data/sessions/<id>/``:

``<id>.messages.json``  {updatedAt, context, messages, systemPrompt}
``<id>.json``           manifest (prompt, provider, model, cwd, ...)

Messages use Claude-style blocks: ``text``, ``thinking``, ``tool_use`` and
``tool_result``.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .chatview.model import usage_dict
from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900

_USER_INPUT = re.compile(r"^\s*<user_input\b[^>]*>(.*?)</user_input>\s*$", re.S)


def _user_text(value: Any) -> str:
    """Cline wraps the entering prompt in <user_input mode=...>. It is host
    framing, not user content; keep the inner prompt so the viewer does not
    show markup on every row. Context envelopes outside the tag are retained."""
    text = _text(value)
    match = _USER_INPUT.match(text)
    return (match.group(1) if match else text).strip()


def sessions_dir() -> Path:
    root = os.environ.get("CLINE_DATA_DIR")
    if root:
        return Path(root).expanduser() / "sessions"
    env = os.environ.get("CLINE_SESSION_DATA_DIR")
    if env:
        return Path(env).expanduser()
    home = os.environ.get("CLINE_DIR") or str(Path.home() / ".cline")
    return Path(home).expanduser() / "data" / "sessions"


def session_dirs(root: Path | None = None) -> list[Path]:
    base = root or sessions_dir()
    out = []
    for child in base.iterdir() if base.is_dir() else []:
        if child.is_dir() and (child / f"{child.name}.messages.json").is_file():
            out.append(child)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


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


def _tool_result_text(value: Any) -> str:
    """Text from Cline tool_result content (string, text blocks, or result rows)."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if not isinstance(block, dict):
                if isinstance(block, str):
                    parts.append(block)
                continue
            result = block.get("result")
            if isinstance(result, str):
                parts.append(result)
            elif isinstance(result, dict):
                parts.append(json.dumps(result, ensure_ascii=False))
            elif isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in ("result", "text", "output", "content"):
            if isinstance(value.get(key), str):
                return value[key]
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def _tool_result_error(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("is_error") or value.get("error")
                    or value.get("success") is False)
    if isinstance(value, list):
        return any(_tool_result_error(item) for item in value)
    return False


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        out = []
        for block in value:
            if isinstance(block, dict) and block.get("type") == "text":
                out.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                out.append(block)
        return "\n".join(x for x in out if x)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return ""


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(folder: Path, store: Any, *, key: Any | None = None,
           stream: str | None = None, now: float | None = None,
           since_ms: int | None = None) -> dict[str, Any]:
    from . import attachments as attachments_mod
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    session_id = folder.name
    manifest = _json((folder / f"{session_id}.json").read_text(encoding="utf-8")) \
        if (folder / f"{session_id}.json").is_file() else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    snap = _json((folder / f"{session_id}.messages.json").read_text(encoding="utf-8"))
    if not isinstance(snap, dict):
        return {"session_id": session_id, "skipped": "empty"}
    messages = snap.get("messages") if isinstance(snap.get("messages"), list) else []
    if not messages:
        return {"session_id": session_id, "skipped": "empty"}

    stream = stream or f"local-cline-{_bare(session_id)}"
    state = store.session_capture(session_id)
    folder_path, folder_manifest = _session_state(store, session_id)
    watermark = int(state.get("host_seq", -1))
    selected = [m for m in messages[max(0, watermark):] if isinstance(m, dict)]
    if since_ms is not None and folder_path is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}

    created = _ms(manifest.get("started_at") or snap.get("updatedAt")
                  or snap.get("updated_at"))
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    title = str(metadata.get("title") or manifest.get("prompt") or "").splitlines()[0][:120]
    if not title:
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "user":
                title = _text(message.get("content")).splitlines()[0][:120]
                break
    title = title or session_id
    cwd = str(manifest.get("cwd") or "")

    key = key or _device_key()
    if folder_path is None:
        meta = {"host": "cline", "host_session_id": session_id,
                "source": "host_import", "imported_from": str(folder),
                "capture_point": "host_import", "title": title}
        if cwd:
            meta["project"] = cwd
        if manifest.get("parent_session_id"):
            meta["fork_of"] = str(manifest["parent_session_id"])
        if since_ms is not None:
            meta["capture_from_ms"] = since_ms
            meta["head_omitted"] = bool(created and created < since_ms)
        fr = Provelog(stream, key=key, store=store, flush=False, float_text=True)
        fr.chain._clock = _clock_for(created or int(stamp * 1000))
        records = [fr.start(meta)]
    else:
        fr = _resume_recorder(store, session_id, stream, key, folder_manifest, state)
        if fr is None:
            return {"session_id": session_id, "skipped": "no_chain"}
        records = []

    def emit(action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
        rec = _emit(fr, action, payload, ms)
        records.append(rec)
        return rec

    turn = 0
    tool_calls = tool_results = 0
    pending_images: list[tuple[bytes, dict[str, Any]]] = []
    for message in selected:
        role = str(message.get("role") or "")
        blocks = message.get("content")
        if isinstance(blocks, str):
            blocks = [{"type": "text", "text": blocks}]
        if not isinstance(blocks, list):
            blocks = []
        ms = _ms(message.get("ts")) or created
        if role == "user":
            turn += 1
            text = _user_text(blocks)
            images = attachments_mod.inline_images(blocks, prefix="user-image")
            refs = attachments_mod.image_refs(blocks, prefix="user-image")
            if text or images or refs:
                content = [{"type": "text", "text": text}] if text else []
                for block, data, _name in images:
                    pending_images.append((data, attachments_mod.entry_for(block["attachment"])))
                    content.append(block)
                content.extend(refs)
                emit("user_message", {
                    "content": content,
                    "id": f"cline-{session_id}-u-{turn}", "origin": "user",
                    "turn": turn,
                }, ms)
        elif role == "assistant":
            content = []
            calls = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "text":
                    text = str(block.get("text") or "")
                    if text:
                        content.append({"type": "text", "text": text})
                elif kind == "thinking":
                    text = str(block.get("thinking") or "")
                    if text:
                        content.append({"type": "thinking", "text": text})
                elif kind == "tool_use":
                    calls.append(block)
            if content:
                body = {"id": f"cline-{session_id}-a-{turn}",
                        "content": content, "turn": turn}
                info = message.get("modelInfo") if isinstance(message.get("modelInfo"), dict) else {}
                if info.get("id"):
                    body["model"] = str(info["id"])
                if info.get("provider"):
                    body["provider"] = str(info["provider"])
                metrics = message.get("metrics") if isinstance(message.get("metrics"), dict) else {}
                usage = usage_dict({
                    "input": metrics.get("inputTokens"),
                    "output": metrics.get("outputTokens"),
                    "cache_read": metrics.get("cacheReadTokens"),
                    "cache_write": metrics.get("cacheWriteTokens"),
                    "total": metrics.get("totalTokens"),
                })
                if usage:
                    body["usage"] = usage
                emit("assistant_message", body, ms)
            for call in calls:
                call_id = str(call.get("id") or "")
                args = call.get("input") if isinstance(call.get("input"), dict) else {}
                body = {"tool": str(call.get("name") or ""), "call_id": call_id}
                body.update(fr._content("args", args))
                if turn:
                    body["turn"] = turn
                emit("tool_call", body, ms)
                tool_calls += 1
        elif role == "tool":
            pass
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            call_id = str(block.get("tool_use_id") or "")
            content = block.get("content")
            text = _tool_result_text(content)
            result_content = [{"type": "text", "text": text}] if text else []
            result_images = attachments_mod.inline_images(content, prefix="tool-image")
            for image_block, data, _name in result_images:
                pending_images.append((data, attachments_mod.entry_for(image_block["attachment"])))
                result_content.append(image_block)
            result_content.extend(attachments_mod.image_refs(content, prefix="tool-image"))
            payload = {"call_id": call_id,
                       "content": result_content,
                       "is_error": bool(block.get("is_error"))
                       or _tool_result_error(content)}
            if turn:
                payload["turn"] = turn
            emit("tool_result", payload, ms)
            tool_results += 1

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
        attachments_mod.save_pending(store, session_id, store.locate(session_id),
                                     pending_images)
        store.rebuild_md(session_id)

    if title:
        try:
            store.update_title(session_id, title)
        except (AttributeError, OSError):
            pass
    capture = {
        "host_seq": len(messages),
        "source_path": str(folder),
        "source_mtime": folder.stat().st_mtime,
        "source_size": sum(p.stat().st_size for p in folder.iterdir() if p.is_file()),
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
    for folder in session_dirs():
        session_id = folder.name
        try:
            state = store.session_capture(session_id)
            if (since_ms is not None and not state
                    and folder.stat().st_mtime * 1000 < since_ms):
                continue
            if state.get("host_seq") is not None:
                snap = _json((folder / f"{session_id}.messages.json").read_text(encoding="utf-8"))
                count = len(snap.get("messages") or []) if isinstance(snap, dict) else 0
                if count == state.get("host_seq") and state.get("commit_count") is not None:
                    continue
            out.append(ingest(folder, store, key=key, now=stamp, since_ms=since_ms))
        except Exception as exc:
            out.append({"session_id": session_id,
                        "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out
