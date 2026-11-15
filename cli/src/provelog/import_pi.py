"""Import pi coding-agent sessions (JSONL).

pi writes one append-only JSONL per session under
``~/.pi/agent/sessions/<encoded-cwd>/<timestamp>_<sessionId>.jsonl``.
The first line is a ``session`` header; following lines are entries:
``message`` (user / assistant / toolResult), ``compaction`` /
``branch_summary`` (host context rewrites, ignored in v1), ``model_change``,
``thinking_level_change`` and ``label``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state
from .textlines import read_jsonl_lines

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900


def pi_session_dir(host: str = "pi") -> Path:
    if host == "omp":
        root = os.environ.get("OMP_AGENT_DIR")
        if root:
            return Path(root).expanduser() / "sessions"
        return Path.home() / ".omp" / "agent" / "sessions"
    env = os.environ.get("PI_CODING_AGENT_SESSION_DIR")
    if env:
        return Path(env).expanduser()
    root = os.environ.get("PI_CODING_AGENT_DIR")
    if root:
        return Path(root).expanduser() / "sessions"
    return Path.home() / ".pi" / "agent" / "sessions"


def session_files(root: Path | None = None, host: str = "pi") -> list[Path]:
    base = root or pi_session_dir(host)
    return sorted((p for p in base.glob("*/*.jsonl") if p.is_file()),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _safe(value: Any) -> Any:
    """Record floats are forbidden by canonical JSON; keep them as strings."""
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
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


def _read(path: Path) -> tuple[list[dict[str, Any]], int]:
    try:
        lines = read_jsonl_lines(path)
    except OSError:
        return [], 0
    rows = []
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
            rows.append(row)
                                                                           
                                                                             
                                                                            
    return rows, len(rows)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts = []
    for block in value:
        if isinstance(block, dict) and block.get("type") == "text":
            text = str(block.get("text") or "")
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(path: Path, store: Any, *, key: Any | None = None,
           stream: str | None = None, now: float | None = None,
           since_ms: int | None = None, host: str = "pi") -> dict[str, Any]:
    from . import attachments as attachments_mod
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    entries, consumed = _read(path)
    if not entries:
        return {"session_id": path.stem, "skipped": "empty"}
    header = next((e for e in entries if e.get("type") == "session"), {})
    session_id = str(header.get("id") or path.stem)
    stream = stream or f"local-{host}-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)

    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=consumed)
        return {"session_id": session_id, "skipped": "legacy_watermark"}

    watermark = int(state.get("host_seq", -1))
    selected = [(i, e) for i, e in enumerate(entries) if i >= watermark]
    selected_messages = [e for _, e in selected if e.get("type") == "message"]
    if since_ms is not None and folder is None and not selected_messages:
        return {"session_id": session_id, "skipped": "before_baseline"}

    created = _ms(header.get("timestamp"))
    first_user = ""
    for entry in selected_messages:
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        if message.get("role") == "user":
            first_user = _text(message.get("content"))
            if first_user:
                break
    title = first_user.splitlines()[0][:120] if first_user else session_id
    cwd = str(header.get("cwd") or "")
    parent = str(header.get("parentSession") or "")

    key = key or _device_key()
    if folder is None:
        meta = {"host": host, "host_session_id": session_id,
                "source": "host_import", "imported_from": str(path),
                "capture_point": "host_import", "title": title}
        if cwd:
            meta["project"] = cwd
        if parent:
            meta["fork_of"] = parent
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

    turn = 0
    tool_calls = tool_results = 0
    pending_images: list[tuple[bytes, dict[str, Any]]] = []
    model = ""
    provider = ""

    for _, entry in selected:
        kind = entry.get("type")
        ms = _ms(entry.get("timestamp")) or created
        if kind == "model_change":
            provider = str(entry.get("provider") or provider)
            model = str(entry.get("modelId") or entry.get("model") or model)
            continue
        if kind in ("compaction", "branch_summary"):
                                                                           
                                               
            continue
        if kind != "message":
            continue
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        role = str(message.get("role") or "")
        msg_ms = _ms(message.get("timestamp")) or ms
        if role == "user":
            turn += 1
            text = _text(message.get("content"))
            images = attachments_mod.inline_images(message.get("content"), prefix="user-image")
            refs = attachments_mod.image_refs(message.get("content"), prefix="user-image")
            if text or images or refs:
                content = [{"type": "text", "text": text}] if text else []
                for block, data, _name in images:
                    pending_images.append((data, attachments_mod.entry_for(block["attachment"])))
                    content.append(block)
                content.extend(refs)
                emit("user_message", {
                    "content": content,
                    "id": str(entry.get("id") or f"{session_id}-u-{turn}"),
                    "origin": "user", "turn": turn,
                }, msg_ms)
        elif role == "assistant":
            blocks = message.get("content")
            if not isinstance(blocks, list):
                blocks = []
            body: dict[str, Any] = {"id": str(entry.get("id") or ""),
                                    "turn": turn}
            content = []
            calls = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = str(block.get("text") or "")
                    if text:
                        content.append({"type": "text", "text": text})
                elif block.get("type") == "thinking":
                    text = str(block.get("thinking") or "")
                    if text:
                        content.append({"type": "thinking", "text": text})
                elif block.get("type") == "toolCall":
                    calls.append(block)
            if content:
                body["content"] = content
                model_name = str(message.get("model") or model)
                provider_name = str(message.get("provider") or provider)
                if model_name:
                    body["model"] = model_name
                    body["provider"] = provider_name or host
                usage = _safe(message.get("usage"))
                if isinstance(usage, dict):
                    body["usage"] = usage
                emit("assistant_message", body, msg_ms)
            for call in calls:
                call_id = str(call.get("id") or "")
                args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
                body = {"tool": str(call.get("name") or ""), "call_id": call_id}
                body.update(fr._content("args", args))
                if turn:
                    body["turn"] = turn
                emit("tool_call", body, msg_ms)
                tool_calls += 1
        elif role == "toolResult":
            text = _text(message.get("content"))
            result_images = attachments_mod.inline_images(message.get("content"),
                                                           prefix="tool-image")
            result_content = [{"type": "text", "text": text}] if text else []
            for image_block, data, _name in result_images:
                pending_images.append((data, attachments_mod.entry_for(image_block["attachment"])))
                result_content.append(image_block)
            result_content.extend(attachments_mod.image_refs(message.get("content"), prefix="tool-image"))
            body = {"call_id": str(message.get("toolCallId") or ""),
                    "tool": str(message.get("toolName") or ""),
                    "content": result_content,
                    "is_error": bool(message.get("isError"))}
            if turn:
                body["turn"] = turn
            emit("tool_result", body, msg_ms)
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
               since_ms: int | None = None, host: str = "pi") -> list[dict[str, Any]]:
    import time as _time
    stamp = now if now is not None else _time.time()
    out: list[dict[str, Any]] = []
    for path in session_files(host=host):
        session_id = path.stem
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
            out.append(ingest(path, store, key=key, now=stamp, since_ms=since_ms,
                              host=host))
        except Exception as exc:
            out.append({"session_id": session_id,
                        "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out
