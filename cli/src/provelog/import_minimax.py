"""Import MiniMax Code conversations from its local runtime database.

MiniMax Code keeps the desktop/CLI conversation store at
``~/.minimax/v2/sqlite/runtime-state.sqlite``:

``local_runtime_sessions``     title, workspace, model, timestamps
``local_runtime_message_rows`` role, turn, timestamp, ``data_json``

The message payload is compact: a user row carries ``msg_content``; an
assistant row carries ``msg_content``, ``thinking_content`` and
``tool_calls`` (args / result / status), plus provider usage.  This importer
maps those to the same records as the other hosts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import attachments as attachments_mod
from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state
from .platform_paths import app_config_dir

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900
_MEDIA = re.compile(r"<media\b[^>]*\bsrc=\"([^\"]+)\"[^>]*>", re.I)


def _media_blocks(text: str, pending: list[tuple[dict[str, Any], Path]]) -> list[dict[str, Any]]:
    """MiniMax deliver-assets: local files are copied; remote URLs are recorded."""
    out: list[dict[str, Any]] = []
    for source in _MEDIA.findall(text or ""):
        source = source.strip()
        if not source:
            continue
        if source.startswith(("http://", "https://")):
            out.append({"type": "image", "url": source, "name": source.rsplit("/", 1)[-1]})
            continue
        path = Path(source).expanduser()
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        digest = hashlib.sha256(data).hexdigest()
        media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower(),
                                                                 "application/octet-stream")
        entry = {"file": digest + (attachments_mod.extension(media) or path.suffix or ".bin"),
                 "path": f"attachments/{digest}{attachments_mod.extension(media) or path.suffix or '.bin'}",
                 "sha256": f"sha256:{digest}", "bytes": len(data),
                 "media_type": media, "name": path.name}
        pending.append((entry, path))
        out.append({"type": "image", "attachment": {
            "attachmentId": entry["sha256"], "mediaType": media,
            "bytes": len(data), "name": path.name}})
    return out


def minimax_databases() -> list[Path]:
    env = os.environ.get("MINIMAX_HOME")
    roots = [Path(env).expanduser()] if env else [
        Path.home() / ".minimax",
        app_config_dir("MiniMax Code"),
        app_config_dir("MiniMax"),
    ]
    paths: list[Path] = []
    for root in roots:
        paths.extend([root / "v2" / "sqlite" / "runtime-state.sqlite",
                      root / "sqlite" / "runtime-state.sqlite"])
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


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(p.get("text") or "") for p in value
                         if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _refresh_title(store: Any, session: dict[str, Any]) -> None:
    """Take the host's title once it exists, and follow a later rename.

    MiniMax names a conversation after its first reply, so the title is
    usually still empty when the first pass creates the record. Nothing is
    written when the manifest already carries it.
    """
    title = str(session.get("title") or "").strip()
    if not title:
        return
    try:
        folder, manifest = _session_state(store, session["session_id"])
        if folder is not None and str(manifest.get("display_title") or "") != title:
            store.set_session_fields(session["session_id"], display_title=title)
    except (AttributeError, OSError):
        pass


def _sessions(db: Path) -> list[dict[str, Any]]:
    con = _connect(db)
    try:
        rows = con.execute(
            "select session_id,title,workspace_dir,project_workspace_dir,"
            "created_at_ms,updated_at_ms,status,extra_data_json "
            "from local_runtime_sessions where archived=0 order by updated_at_ms desc"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out = []
    for row in rows:
        extra = _json(row[7]) if isinstance(row[7], str) else {}
        if not isinstance(extra, dict):
            extra = {}
        out.append({"session_id": str(row[0]), "title": str(row[1] or ""),
                    "cwd": str(row[2] or row[3] or ""), "created_at": int(row[4] or 0),
                    "updated_at": int(row[5] or 0), "status": str(row[6] or ""),
                    "model": str(extra.get("effectiveModel") or "")})
    return out


def _messages(db: Path, session_id: str) -> list[dict[str, Any]]:
    con = _connect(db)
    try:
        rows = con.execute(
            "select id,msg_id,role,turn_id,created_at_ms,data_json "
            "from local_runtime_message_rows where session_id=? order by id",
            (session_id,),
        ).fetchall()
    finally:
        con.close()
    out = []
    for row in rows:
        data = _json(row[5]) if isinstance(row[5], str) else {}
        out.append({"row_id": int(row[0]), "msg_id": str(row[1] or ""),
                    "role": str(row[2] or ""), "turn_id": str(row[3] or ""),
                    "created_at": int(row[4] or 0),
                    "data": data if isinstance(data, dict) else {}})
    return out


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def _result_text(data: Any) -> tuple[str, dict[str, Any], bool, str | None, int | None]:
    """Return text, execution, is_error, persisted path, persisted bytes."""
    if not isinstance(data, dict):
        raw = str(data) if data is not None else ""
        return raw, {}, False, None, None
    text = _text(data.get("content"))
    if not text:
        text = str(data.get("text") or "")
    details = data.get("details") if isinstance(data.get("details"), dict) else {}
    execution = details.get("execution") if isinstance(details.get("execution"), dict) else {}
    process = details.get("processOutput") if isinstance(details.get("processOutput"), dict) else {}
    status = str(execution.get("status") or "")
    is_error = bool(status and status not in ("succeeded", "success", "ok", "exited")
                    and execution.get("exitCode") not in (0, None))
    output = details.get("output") if isinstance(details.get("output"), dict) else {}
    omitted = output.get("omittedBytes")
    persisted_path = process.get("rawOutputPath")
    if not persisted_path:
        persisted_path = data.get("fullOutputPath") or details.get("fullOutputPath")
    persisted_bytes = int(omitted) if isinstance(omitted, int) and omitted > 0 else None
    return text, execution, is_error, (str(persisted_path) if persisted_path else None), persisted_bytes


def _ingest_session(db: Path, session: dict[str, Any], store: Any, key: Any,
                    now: float, since_ms: int | None) -> dict[str, Any]:
    from .easy import _device_key
    from .sdk import Provelog

    session_id = session["session_id"]
    stream = f"local-minimax-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    if folder is not None:
                                                                         
                                                           
        _refresh_title(store, session)
    state = store.session_capture(session_id)
    messages = _messages(db, session_id)
    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=max(
            (m["row_id"] for m in messages), default=0))
        return {"session_id": session_id, "skipped": "legacy_watermark"}

    watermark = int(state.get("host_seq", -1))
    selected = [m for m in messages
                if m["row_id"] > watermark and m["role"] in ("user", "assistant")
                and (since_ms is None or m["created_at"] > since_ms)]
    if since_ms is not None and folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}

    key = key or _device_key()
    if folder is None:
        meta = {"host": "minimax", "host_session_id": session_id,
                "source": "host_import", "imported_from": str(db),
                "capture_point": "host_import", "minimax_session": session_id}
        if since_ms is not None:
            meta["capture_from_ms"] = since_ms
            meta["head_omitted"] = bool(session["created_at"] and
                                        session["created_at"] < since_ms)
        if session["title"]:
            meta["title"] = session["title"]
        if session["cwd"]:
            meta["project"] = session["cwd"]
        fr = Provelog(stream, key=key, store=store, flush=False, float_text=True)
        fr.chain._clock = _clock_for(session["created_at"] or int(now * 1000))
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
    pending_attachments: list[tuple[dict[str, Any], Path]] = []

    for message in selected:
        data = message["data"]
        ms = message["created_at"]
        if message["role"] == "user":
            turn += 1
            text = str(data.get("msg_content") or "").strip()
            media = _media_blocks(text, pending_attachments)
            if text or media:
                emit("user_message", {
                    "content": ([{"type": "text", "text": text}] if text else []) + media,
                    "id": message["msg_id"], "origin": "user", "turn": turn,
                }, ms)
            continue
        if message["role"] != "assistant":
            continue

        model = str(data.get("model") or session["model"] or "")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        blocks: list[dict[str, Any]] = []
        think = str(data.get("thinking_content") or "").strip()
        if think:
            blocks.append({"type": "thinking", "text": think})
        text = str(data.get("msg_content") or "").strip()
        if text:
            blocks.append({"type": "text", "text": text})
        blocks.extend(_media_blocks(text, pending_attachments))
        if blocks:
            body: dict[str, Any] = {"id": message["msg_id"], "content": blocks,
                                    "turn": turn}
            if model:
                body["model"] = model
                body["provider"] = "minimax"
            if usage:
                body["usage"] = usage
            emit("assistant_message", body, ms)
            if usage:
                emit("model_call", {
                    "model": model, "provider": "minimax", "usage": usage,
                    "derived_from": "assistant_message",
                    "message_id": message["msg_id"],
                    **({"turn": turn} if turn else {}),
                }, ms)

        for call in data.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("tool_name") or "")
            call_id = str(call.get("tool_call_id") or "")
            args = _json(call.get("tool_call_args"))
            if not isinstance(args, dict):
                args = {"_raw": call.get("tool_call_args")} if call.get("tool_call_args") else {}
            body = {"tool": name, "call_id": call_id}
            body.update(fr._content("args", args))
            if turn:
                body["turn"] = turn
            emit("tool_call", body, ms)
            tool_calls += 1

            result_data = _json(call.get("tool_call_result_data"))
            result_text, execution, is_error, persisted_path, persisted_bytes = _result_text(result_data)
            result: dict[str, Any] = {
                "call_id": call_id, "tool": name,
                "content": [{"type": "text", "text": result_text}],
                "is_error": is_error,
            }
            status = call.get("tool_call_status")
            if status is not None:
                result["status"] = status
            if execution:
                result["execution"] = execution
            if turn:
                result["turn"] = turn
            if persisted_path and persisted_bytes:
                source = Path(persisted_path)
                entry = None
                try:
                    raw = source.read_bytes()
                    digest = hashlib.sha256(raw).hexdigest()
                    entry = {"kind": "tool_output", "file": f"{digest}.txt",
                             "path": f"attachments/{digest}.txt", "name": source.name,
                             "source": "minimax", "bytes": len(raw),
                             "sha256": f"sha256:{digest}"}
                    result["truncated"] = {"by": "minimax", "kind": "persisted_output",
                                           "external_ref": str(source), "bytes": len(raw),
                                           "sha256": digest}
                    pending_attachments.append((entry, source))
                except OSError:
                    result["truncated"] = {"by": "minimax", "kind": "persisted_output",
                                           "external_ref": str(source), "bytes": persisted_bytes}
            emit("tool_result", result, ms)
            tool_results += 1

    committed = False
    if fr.chain._open:
        since_commit = now - float(state.get("commit_at") or 0)
        pending = int(fr.chain._seq) - int(state.get("commit_count") or 0)
        if records and (state.get("commit_count") is None
                        or pending >= COMMIT_EVERY_RECORDS
                        or since_commit >= COMMIT_EVERY_S):
            fr.chain._clock = _clock_for(int(now * 1000))
            records.append(fr.chain.commit(reason="auto"))
            committed = True
        elif not records and state and state.get("commit_count") is None:
            fr.chain._clock = _clock_for(int(now * 1000))
            records.append(fr.chain.commit(reason="backfill"))
            committed = True

    if records:
        store.append_records(stream, records, defer_index=False)
        folder = store.locate(session_id)
        added_attachments = []
        if folder is not None:
            for entry, source in pending_attachments:
                if source.is_file() and attachments_mod.copy_into(
                        Path(folder), source, entry):
                    added_attachments.append(entry)
            if added_attachments and hasattr(store, "add_attachments"):
                store.add_attachments(session_id, added_attachments)
        store.rebuild_md(session_id)

    _refresh_title(store, session)

    capture = {
        "host_seq": max((m["row_id"] for m in messages), default=watermark),
        "source_path": str(db),
        "source_mtime": db.stat().st_mtime,
        "source_size": db.stat().st_size,
        "source_updated_at": session["updated_at"],
        "chain_id": fr.chain.session_id,
        "capture_point": "host_import",
        "verification_level": "signed_local",
    }
    if committed:
        capture["commit_at"] = now
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
    for db in minimax_databases():
        try:
            sessions = _sessions(db)
        except sqlite3.Error as exc:
            out.append({"session_id": "", "error": f"minimax: {exc}"})
            continue
        for session in sessions:
            session_id = session["session_id"]
            try:
                state = store.session_capture(session_id)
                if (state.get("source_updated_at") == session["updated_at"]
                        and state.get("commit_count") is not None):
                                                                            
                    _refresh_title(store, session)
                    continue
                out.append(_ingest_session(db, session, store, key or
                                           _device_key(), stamp, since_ms))
            except Exception as exc:
                out.append({"session_id": session_id,
                            "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out


def _device_key():
    from .easy import _device_key
    return _device_key()
