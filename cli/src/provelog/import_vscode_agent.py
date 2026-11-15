"""Import Cline / Kilo / Roo Code VS Code-family task histories.

The three extensions are Cline-family and store one directory per task under
a VS Code-compatible global storage root::

    <globalStorage>/<extension-id>/tasks/<taskId>/
      api_conversation_history.json   # Anthropic-style messages
      task_metadata.json              # optional title/cwd

Roots follow the platform: macOS ``~/Library/Application Support``, Windows
``%APPDATA%``, Linux ``$XDG_CONFIG_HOME`` (or ``~/.config``).
the Cline extension id is ``saoudrizwan.claude-dev``, Kilo is
``kilocode.kilo-code`` and Roo is ``rooveterinaryinc.roo-cline``.  The CLI
formats are separate importers; this one is only the VS Code extension data.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state
from .platform_paths import vscode_global_storage_dir

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900

                                     
SPECS = (
    ("cline", ("saoudrizwan.claude-dev",), "Cline (VS Code)"),
    ("kilo", ("kilocode.kilo-code", "kilocode.kilo-code-nightly"), "Kilo (VS Code)"),
    ("roo", ("rooveterinaryinc.roo-cline",), "Roo Code"),
)


def global_storage_roots() -> list[Path]:
    env = os.environ.get("PROVELOG_VSCODE_GLOBALSTORAGE")
    if env:
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    out = []
    for app in ("Code", "Cursor", "VSCodium"):
        p = vscode_global_storage_dir(app)
        if p.is_dir():
            out.append(p)
    return out


def task_dirs(root: Path | None = None) -> list[tuple[str, Path, Path]]:
    """(host, task directory, api_conversation_history.json), newest first."""
    roots = [root] if root else global_storage_roots()
    out: list[tuple[str, Path, Path]] = []
    for base in roots:
        for host, ext_ids, _label in SPECS:
            for ext in ext_ids:
                tasks = base / ext / "tasks"
                if not tasks.is_dir():
                    continue
                for task in tasks.iterdir():
                    history = task / "api_conversation_history.json"
                    if task.is_dir() and history.is_file():
                        out.append((host, task, history))
    return sorted(out, key=lambda x: x[2].stat().st_mtime, reverse=True)


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


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(str(b.get("text") or "") for b in value
                         if isinstance(b, dict) and b.get("type") == "text").strip()
    return ""


def _message_list(raw: Any) -> list[dict[str, Any]]:
    value = _json(raw) if isinstance(raw, str) else raw
    if isinstance(value, dict):
        value = value.get("messages") or value.get("history") or []
    if not isinstance(value, list):
        return []
    return [m for m in value if isinstance(m, dict)]


def _metadata(task: Path) -> dict[str, Any]:
    for name in ("task_metadata.json", "metadata.json"):
        path = task / name
        if path.is_file():
            value = _json(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(value, dict):
                return value
    return {}


def _title(meta: dict[str, Any], messages: list[dict[str, Any]], session_id: str) -> str:
    for key in ("title", "task", "name", "description"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0][:120]
    for message in messages:
        if message.get("role") == "user":
            text = _text(message.get("content"))
            if text and not text.lstrip().startswith("<"):
                return text.splitlines()[0][:120]
    return session_id


def _event_time(message: dict[str, Any]) -> int:
    for key in ("timestamp", "ts", "createdAt", "created_at", "time"):
        value = message.get(key)
        if value:
            stamp = _ms(value)
            if stamp:
                return stamp
    return 0


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(task: Path, store: Any, *, host: str, history: Path | None = None,
           key: Any | None = None, stream: str | None = None,
           now: float | None = None, since_ms: int | None = None) -> dict[str, Any]:
    from . import attachments as attachments_mod
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    history = history or (task / "api_conversation_history.json")
    messages = _message_list(history.read_text(encoding="utf-8", errors="replace"))
    if not messages:
        return {"session_id": task.name, "skipped": "empty"}
    session_id = task.name
    meta = _metadata(task)
    title = _title(meta, messages, session_id)
    stream = stream or f"local-{host}-vscode-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)
    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=len(messages))
        return {"session_id": session_id, "skipped": "legacy_watermark"}

    watermark = int(state.get("host_seq", -1))
    if len(messages) < watermark:
        return {"session_id": session_id, "skipped": "source_shrank"}
    selected = messages[max(0, watermark):]
    if since_ms is not None and folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}
    created = _ms(meta.get("createdAt") or meta.get("created_at"))
    cwd = str(meta.get("cwd") or meta.get("workspace") or "")
    model = str(meta.get("model") or "")

    key = key or _device_key()
    if folder is None:
        meta_payload = {"host": host, "host_session_id": session_id,
                        "source": "host_import", "imported_from": str(task),
                        "capture_point": "host_import", "title": title}
        if cwd:
            meta_payload["project"] = cwd
        if since_ms is not None:
            meta_payload["capture_from_ms"] = since_ms
            meta_payload["head_omitted"] = bool(created and created < since_ms)
        fr = Provelog(stream, key=key, store=store, flush=False, float_text=True)
        fr.chain._clock = _clock_for(created or int(stamp * 1000))
        records = [fr.start(meta_payload)]
    else:
        fr = _resume_recorder(store, session_id, stream, key, manifest, state)
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
    for index, message in enumerate(selected):
        role = str(message.get("role") or "")
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        event_ms = _event_time(message)
        ms = event_ms or created
        time_source = "event" if event_ms else "session"
        if role == "user":
            results = [b for b in blocks
                       if isinstance(b, dict) and b.get("type") == "tool_result"]
            if results:
                for block in results:
                    result_content = [{"type": "text", "text": _text(block.get("content"))}]
                    result_images = attachments_mod.inline_images(
                        block.get("content"), prefix="tool-image")
                    for image_block, data, _name in result_images:
                        pending_images.append((data, attachments_mod.entry_for(image_block["attachment"])))
                        result_content.append(image_block)
                    result_content.extend(attachments_mod.image_refs(
                        block.get("content"), prefix="tool-image"))
                    payload = {"call_id": str(block.get("tool_use_id") or ""),
                               "content": result_content,
                               "is_error": bool(block.get("is_error")),
                               "ts_source": time_source}
                    if turn:
                        payload["turn"] = turn
                    emit("tool_result", payload, ms)
                    tool_results += 1
                continue
            text = _text(content)
            images = attachments_mod.inline_images(content, prefix="user-image")
            refs = attachments_mod.image_refs(content, prefix="user-image")
            if text or images or refs:
                turn += 1
                user_content = [{"type": "text", "text": text}] if text else []
                for image_block, data, _name in images:
                    pending_images.append((data, attachments_mod.entry_for(image_block["attachment"])))
                    user_content.append(image_block)
                user_content.extend(refs)
                emit("user_message", {
                    "content": user_content,
                    "id": f"{session_id}-u-{watermark + index + 1}",
                    "origin": "user", "turn": turn,
                    "ts_source": time_source,
                }, ms)
        elif role == "assistant":
            parts = []
            calls = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "text":
                    text = str(block.get("text") or "")
                    if text:
                        parts.append({"type": "text", "text": text})
                elif kind == "thinking":
                    text = str(block.get("thinking") or "")
                    if text:
                        parts.append({"type": "thinking", "text": text})
                elif kind == "tool_use":
                    calls.append(block)
            if parts:
                body = {"id": f"{session_id}-a-{watermark + index + 1}",
                        "content": parts, "turn": turn,
                        "ts_source": time_source}
                if model:
                    body["model"] = model
                    body["provider"] = host
                emit("assistant_message", body, ms)
            for call in calls:
                call_id = str(call.get("id") or "")
                args = call.get("input") if isinstance(call.get("input"), dict) else {}
                payload = {"tool": str(call.get("name") or ""), "call_id": call_id,
                           "ts_source": time_source}
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
        "source_path": str(task),
        "source_mtime": history.stat().st_mtime,
        "source_size": history.stat().st_size,
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
    stamp = now if now is not None else time.time()
    out: list[dict[str, Any]] = []
    for host, task, history in task_dirs():
        try:
            stat = history.stat()
            state = store.session_capture(task.name)
            if (since_ms is not None and not state
                    and stat.st_mtime * 1000 < since_ms):
                continue
            if (state.get("source_mtime") == stat.st_mtime
                    and state.get("source_size") == stat.st_size
                    and state.get("commit_count") is not None):
                continue
            out.append(ingest(task, store, host=host, history=history,
                              key=key, now=stamp, since_ms=since_ms))
        except Exception as exc:
            out.append({"session_id": task.name,
                        "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out


def counts_by_host() -> dict[str, int]:
    out = {host: 0 for host, _ids, _label in SPECS}
    for host, _task, _history in task_dirs():
        out[host] = out.get(host, 0) + 1
    return out
