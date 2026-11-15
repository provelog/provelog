"""Import Hermes Agent conversations from its SQLite state database.

Hermes keeps state under ``$HERMES_HOME/state.db`` (default
``~/.hermes/state.db``) with ``sessions`` and ``messages`` tables.  The
schema has evolved; the reader resolves columns by name and tolerates missing
optional columns instead of pinning one release.

Message rows carry ``content``, optional ``reasoning``/``reasoning_content``,
``tool_calls`` (a JSON string) and ``tool_call_id``/``tool_name`` for tool
results.
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


def hermes_databases() -> list[Path]:
    root = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    paths = [root / "state.db"]
    profiles = root / "profiles"
    if profiles.is_dir():
        paths.extend(sorted(profiles.glob("*/state.db")))
    return [p for p in paths if p.is_file()]


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


def _rows(con: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    try:
        cur = con.execute(sql)
    except sqlite3.Error:
        return []
    cols = [d[0] for d in cur.description or []]
    return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def _sessions(db: Path) -> list[dict[str, Any]]:
    con = _connect(db)
    try:
        rows = _rows(con, "select * from sessions")
    finally:
        con.close()
    return rows


def _messages(db: Path, session_id: str) -> list[dict[str, Any]]:
    con = _connect(db)
    try:
        cur = con.execute(
            "select * from messages where session_id=? order by "
            "coalesce(timestamp, id), id", (session_id,))
        cols = [d[0] for d in cur.description or []]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        out = []
        for block in value:
            if isinstance(block, dict):
                if block.get("type") in ("text", "input_text", "output_text"):
                    out.append(str(block.get("text") or ""))
                elif block.get("type") == "thinking":
                    out.append(str(block.get("thinking") or ""))
            elif isinstance(block, str):
                out.append(block)
        return "\n".join(x for x in out if x)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return ""


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(db: Path, session: dict[str, Any], store: Any, *, key: Any | None = None,
           now: float | None = None, since_ms: int | None = None) -> dict[str, Any]:
    from . import attachments as attachments_mod
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    session_id = str(session.get("id") or session.get("session_id") or "")
    if not session_id:
        return {"session_id": "", "skipped": "no_session_id"}
    stream = f"local-hermes-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)
    messages = _messages(db, session_id)
    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=max(
            (int(m.get("id") or 0) for m in messages), default=0))
        return {"session_id": session_id, "skipped": "legacy_watermark"}

    watermark = int(state.get("host_seq", -1))
    selected = [m for m in messages if int(m.get("id") or 0) > watermark]
    if since_ms is not None and folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}

    created = _ms(session.get("started_at"))
    title = str(session.get("title") or "").strip()
    if not title:
        for m in messages:
            if str(m.get("role") or "") == "user":
                title = _text(m.get("content")).splitlines()[0][:120]
                break
    title = title or session_id
    model = str(session.get("model") or "")

    key = key or _device_key()
    if folder is None:
        meta = {"host": "hermes", "host_session_id": session_id,
                "source": "host_import", "imported_from": str(db),
                "capture_point": "host_import", "title": title}
        if session.get("cwd"):
            meta["project"] = str(session["cwd"])
        if session.get("parent_session_id"):
            meta["fork_of"] = str(session["parent_session_id"])
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
    for message in selected:
        role = str(message.get("role") or "")
        ms = _ms(message.get("timestamp")) or created
        if role == "user":
            turn += 1
            text = _text(message.get("content")).strip()
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
                    "id": str(message.get("id") or f"{session_id}-u-{turn}"),
                    "origin": "user", "turn": turn,
                }, ms)
        elif role == "assistant":
            blocks = []
            reasoning = message.get("reasoning") or message.get("reasoning_content")
            if reasoning:
                blocks.append({"type": "thinking", "text": str(reasoning)})
            text = _text(message.get("content")).strip()
            if text:
                blocks.append({"type": "text", "text": text})
            if blocks:
                body = {"id": str(message.get("id") or ""), "content": blocks,
                        "turn": turn}
                if model:
                    body["model"] = model
                    body["provider"] = "hermes"
                emit("assistant_message", body, ms)
            calls = _json(message.get("tool_calls"))
            if isinstance(calls, list):
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    call_id = str(call.get("id") or "")
                    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                    args = _json(fn.get("arguments"))
                    body = {"tool": str(fn.get("name") or call.get("name") or ""),
                            "call_id": call_id}
                    body.update(fr._content("args", args if isinstance(args, dict) else {}))
                    if turn:
                        body["turn"] = turn
                    emit("tool_call", body, ms)
                    tool_calls += 1
        elif role == "tool":
            text = _text(message.get("content"))
            result_images = attachments_mod.inline_images(message.get("content"),
                                                           prefix="tool-image")
            result_content = [{"type": "text", "text": text}] if text else []
            for image_block, data, _name in result_images:
                pending_images.append((data, attachments_mod.entry_for(image_block["attachment"])))
                result_content.append(image_block)
            result_content.extend(attachments_mod.image_refs(message.get("content"), prefix="tool-image"))
            payload = {"call_id": str(message.get("tool_call_id") or ""),
                       "tool": str(message.get("tool_name") or ""),
                       "content": result_content,
                       "is_error": bool(message.get("is_error"))}
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
        "host_seq": max((int(m.get("id") or 0) for m in messages), default=watermark),
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
    for db in hermes_databases():
        try:
            sessions = _sessions(db)
        except sqlite3.Error as exc:
            out.append({"session_id": "", "error": f"hermes: {exc}"})
            continue
        for session in sessions:
            try:
                out.append(ingest(db, session, store, key=key or _key(),
                                  now=stamp, since_ms=since_ms))
            except Exception as exc:
                out.append({"session_id": str(session.get("id") or ""),
                            "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out


def _key():
    from .easy import _device_key
    return _device_key()
