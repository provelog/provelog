"""Import OpenClaw agent conversations from its per-agent SQLite store.

OpenClaw keeps one database per agent::

    ~/.openclaw/agents/<agentId>/agent/openclaw-agent.sqlite

The ``transcript_events`` table is an append-only event log: ``event_json``
(or a zstd-compressed ``event_zstd``) with ``session``, ``message``,
``custom_message`` and custom metadata entries.  Messages use the same
text / thinking / toolCall / toolResult vocabulary as pi.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900


def state_roots() -> list[Path]:
    roots = []
    env = os.environ.get("OPENCLAW_STATE_DIR")
    if env:
        roots.append(Path(env).expanduser())
    else:
        roots.append(Path.home() / ".openclaw")
                                                                          
                                                                             
        roots.extend(sorted(Path.home().glob(".openclaw-*")))
    out, seen = [], set()
    for root in roots:
        key = str(root)
        if key not in seen and root.is_dir():
            out.append(root)
            seen.add(key)
    return out


def databases() -> list[Path]:
    out = []
    for root in state_roots():
        out.extend(root.glob("agents/*/agent/openclaw-agent.sqlite"))
    return sorted(p for p in out if p.is_file())


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _safe(value: Any) -> Any:
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


def _event_rows(path: Path, session_id: str) -> list[dict[str, Any]]:
    con = _connect(path)
    try:
        rows = con.execute(
            "select seq,event_json,event_zstd from transcript_events "
            "where session_id=? order by seq", (session_id,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out = []
    for seq, raw, packed in rows:
        if isinstance(raw, str):
            event = _json(raw)
        elif packed:
            try:
                import zstandard
                event = _json(zstandard.ZstdDecompressor().decompress(bytes(packed)))
            except Exception:
                event = None
        else:
            event = None
        if isinstance(event, dict):
            out.append({"seq": int(seq), "event": event})
    return out


def _sessions(path: Path) -> list[str]:
    con = _connect(path)
    try:
        rows = con.execute("select distinct session_id from transcript_events").fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return [str(r[0]) for r in rows if r[0]]


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(db: Path, session_id: str, store: Any, *, key: Any | None = None,
           now: float | None = None, since_ms: int | None = None) -> dict[str, Any]:
    from . import attachments as attachments_mod
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    rows = _event_rows(db, session_id)
    if not rows:
        return {"session_id": session_id, "skipped": "empty"}
    header = next((r["event"] for r in rows if r["event"].get("type") == "session"), {})
    session_id = str(header.get("id") or session_id)
    stream = f"local-openclaw-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)
    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=max(r["seq"] for r in rows))
        return {"session_id": session_id, "skipped": "legacy_watermark"}

    watermark = int(state.get("host_seq", -1))
    selected = [r for r in rows if r["seq"] > watermark]
    if since_ms is not None and folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}

    created = _ms(header.get("timestamp"))
    title = ""
    for r in rows:
        event = r["event"]
        if event.get("type") != "message":
            continue
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        if message.get("role") == "user":
            title = _text(message.get("content")).splitlines()[0][:120]
            if title:
                break
    title = title or session_id
    cwd = str(header.get("cwd") or "")

    key = key or _device_key()
    if folder is None:
        meta = {"host": "openclaw", "host_session_id": session_id,
                "source": "host_import", "imported_from": str(db),
                "capture_point": "host_import", "title": title}
        if cwd:
            meta["project"] = cwd
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
    for row in selected:
        event = row["event"]
        kind = event.get("type")
        ms = _ms(event.get("timestamp"))
        if kind == "model_change":
            model = str(event.get("modelId") or model)
            provider = str(event.get("provider") or provider)
            continue
        if kind != "message":
            continue
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
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
                    "id": str(event.get("id") or ""), "origin": "user", "turn": turn,
                }, msg_ms)
        elif role == "assistant":
            blocks = message.get("content")
            if not isinstance(blocks, list):
                blocks = []
            content, calls = [], []
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
                body = {"id": str(event.get("id") or ""), "content": content,
                        "turn": turn}
                model_name = str(message.get("model") or model)
                if model_name:
                    body["model"] = model_name
                    body["provider"] = str(message.get("provider") or provider or "openclaw")
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
        elif role in ("toolResult", "tool"):
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
        "host_seq": max((r["seq"] for r in rows), default=watermark),
        "source_path": str(db),
        "source_mtime": db.stat().st_mtime,
        "source_size": db.stat().st_size,
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
    for db in databases():
        for session_id in _sessions(db):
            try:
                out.append(ingest(db, session_id, store, key=key or _key(),
                                  now=stamp, since_ms=since_ms))
            except Exception as exc:
                out.append({"session_id": session_id,
                            "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out


def _key():
    from .easy import _device_key
    return _device_key()
