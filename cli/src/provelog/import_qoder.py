"""Import Qoder / Qoder CN conversations from their local SQLite store.

Qoder keeps a local chat database at
``~/Library/Application Support/com.qoder.app.stable/main.sqlite`` (international)
and ``com.qodercn.app.stable`` (CN).  The schema is shared:

``chat_sessions``          id, title, cwd, model, created/updated
``chat_session_messages``  sequence, role, source, payload_json

A user payload carries ``parts`` (text + attachments); an assistant payload
carries ordered ``parts`` of ``thinking`` / ``tool`` / ``text`` / ``hook``.
Hook rows are host bookkeeping and stay out of the evidence stream.
"""

from __future__ import annotations

# ruff: noqa: B023 - flush() is called synchronously inside the loop.
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state
from .platform_paths import is_macos, is_windows, roaming_appdata, xdg_config_home

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900


def qoder_databases() -> list[tuple[str, Path]]:
    """(host key, database) for both Qoder channels, existing only.

    Electron uses the bundle id on macOS and the application name on Windows
    and Linux.  Scan the known aliases instead of guessing one spelling; the
    directory name still selects the CN/international channel.
    """
    explicit = os.environ.get("QODER_APP_DATA")
    if explicit:
        bases = [Path(explicit).expanduser()]
    elif is_macos():
        bases = [Path.home() / "Library" / "Application Support"]
    elif is_windows():
        bases = [roaming_appdata()]
    else:
        bases = [xdg_config_home()]
    folders = (
        "com.qodercn.app.stable", "com.qoder.app.stable",
        "Qoder CN", "Qoder", "QoderCN", "qoder-cn", "qoder",
    )
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for base in bases:
        for folder in folders:
            path = base / folder / "main.sqlite"
            if not path.is_file():
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            host = "qoder-cn" if "cn" in folder.lower() else "qoder"
            out.append((host, path))
    return out


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _text_from_parts(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    out = []
    for part in parts:
        if isinstance(part, dict) and part.get("type") in ("text", "thinking"):
            text = str(part.get("text") or "")
            if text:
                out.append(text)
    return "\n".join(out).strip()


def _session_rows(db: Path) -> list[dict[str, Any]]:
    con = _connect(db)
    try:
        try:
            rows = con.execute(
                "select session_id,title,cwd,model,created_at,updated_at,"
                "additional_direcotries,origin_session_id,extra_json "
                "from chat_sessions where deleted_at is null order by updated_at desc"
            ).fetchall()
        except sqlite3.Error:
                                                                        
            try:
                rows = [tuple(r) + ("[]",) for r in con.execute(
                    "select session_id,title,cwd,model,created_at,updated_at,"
                    "origin_session_id,extra_json "
                    "from chat_sessions where deleted_at is null order by updated_at desc"
                ).fetchall()]
            except sqlite3.Error:
                try:
                    rows = [tuple(r) + ("[]", None, None) for r in con.execute(
                        "select session_id,title,cwd,model,created_at,updated_at,"
                        "origin_session_id "
                        "from chat_sessions where deleted_at is null order by updated_at desc"
                    ).fetchall()]
                except sqlite3.Error:
                    rows = [tuple(r) + ("[]", None, None) for r in con.execute(
                        "select session_id,title,cwd,model,created_at,updated_at "
                        "from chat_sessions where deleted_at is null order by updated_at desc"
                    ).fetchall()]
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out = []
    for r in rows:
        try:
            extras = _json(r[6])
        except (TypeError, ValueError):
            extras = []
        extra = {}
        try:
            extra = _json(r[8]) if len(r) > 8 else {}
        except (TypeError, ValueError):
            extra = {}
        out.append({
            "session_id": r[0], "title": r[1] or "", "cwd": r[2] or "",
            "model": r[3] or "", "created_at": int(r[4] or 0),
            "updated_at": int(r[5] or 0),
            "additional": [str(x) for x in extras] if isinstance(extras, list) else [],
            "origin_session_id": (str(r[7]) if len(r) > 7 and r[7] else ""),
            "imported_from": (str(extra.get("importedFrom") or "")
                              if isinstance(extra, dict) else ""),
        })
    return out


def _project(session: dict[str, Any]) -> str:
    """The user-facing workspace, not Qoder's managed copy when it made one.

    Qoder opens a session in ``~/Documents/Qoder/<date>/<id>`` and keeps the
    originally selected folder in ``additional_direcotries``. That selected
    folder is what the user calls the workspace, so group by it when it is the
    only additional directory; otherwise keep the host cwd as the truth.
    """
    cwd = str(session.get("cwd") or "").strip()
    extras = [p for p in (session.get("additional") or []) if Path(p).is_dir()]
    managed = (Path.home() / "Documents" / "Qoder")
    try:
        under_managed = bool(cwd) and Path(cwd).resolve().is_relative_to(managed.resolve())
    except (OSError, ValueError):
        under_managed = False
    if under_managed and len(extras) == 1:
        return extras[0]
    return cwd


def _messages(db: Path, session_id: str) -> list[dict[str, Any]]:
    con = _connect(db)
    try:
        rows = con.execute(
            "select sequence,message_id,turn_id,role,status,source,created_at,payload_json "
            "from chat_session_messages where session_id=? order by sequence",
            (session_id,),
        ).fetchall()
    finally:
        con.close()
    out = []
    for sequence, message_id, turn_id, role, status, source, created_at, payload in rows:
        out.append({"sequence": int(sequence), "message_id": str(message_id),
                    "turn_id": str(turn_id or ""), "role": str(role or ""),
                    "status": str(status or ""), "source": str(source or ""),
                    "created_at": int(created_at or 0),
                    "payload": _json(payload)})
    return out


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def _ingest_session(db: Path, host: str, session: dict[str, Any], store: Any,
                    key: Any, now: float, since_ms: int | None) -> dict[str, Any]:
    from . import attachments as attachments_mod
    from .easy import _device_key
    from .sdk import Provelog

    session_id = str(session["session_id"])
    stream = f"local-{host}-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)
    messages = _messages(db, session_id)
    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=max(
            (m["sequence"] for m in messages), default=0))
        return {"session_id": session_id, "skipped": "legacy_watermark"}

                                                                            
                                                                             
                                                       
    watermark = int(state.get("host_seq", -1))
    deleted_at = int(state.get("deleted_at_ms") or 0)
    selected = [m for m in messages
                if m["sequence"] > watermark
                and m["role"] in ("user", "assistant")
                and (since_ms is None or m["created_at"] > since_ms)
                and (not deleted_at or m["created_at"] > deleted_at)]
    if since_ms is not None and folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}
    if folder is None and state and not selected:
        return {"session_id": session_id, "skipped": "deleted_no_new_events"}

    key = key or _device_key()
    project = _project(session)
    if (folder is not None and session.get("imported_from")
            and manifest.get("imported_from") != session["imported_from"]):
        try:
            store.set_session_fields(session_id,
                                     imported_from=session["imported_from"])
        except (AttributeError, OSError):
            pass
    if folder is not None and project and manifest.get("project") != project:
                                                                            
                                                                           
                                                                   
        store.set_session_fields(session_id, project=project,
                                 host_cwd=session["cwd"] or None)
    if folder is None:
        meta = {"host": host, "host_session_id": session_id,
                "source": "host_import", "imported_from": str(db),
                "capture_point": "host_import", "qoder_session": session_id}
        if since_ms is not None:
            meta["capture_from_ms"] = since_ms
            meta["head_omitted"] = bool(session["created_at"] and
                                        session["created_at"] < since_ms)
        if session["title"]:
            meta["title"] = session["title"]
        if project:
            meta["project"] = project
        if session["cwd"]:
            meta["host_cwd"] = session["cwd"]
        if session.get("origin_session_id"):
            meta["origin_session_id"] = session["origin_session_id"]
        if session.get("imported_from"):
            meta["imported_from"] = session["imported_from"]
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
    pending_attachments: list[tuple[bytes, dict[str, Any]]] = []

    def attachment_block(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        source_value = (item.get("path") or item.get("filePath") or
                        item.get("file_path") or item.get("url") or "")
        source = Path(str(source_value)).expanduser()
        if not source.is_absolute():
            source = Path(str(session.get("cwd") or "")).expanduser() / source
        try:
            if not source.is_file():
                return None
            data = source.read_bytes()
        except OSError:
            return None
        name = str(item.get("name") or source.name)
        media = str(item.get("mediaType") or item.get("mimeType") or
                    item.get("mime_type") or "")
        if not media:
            media = {".png": "image/png", ".jpg": "image/jpeg",
                     ".jpeg": "image/jpeg", ".webp": "image/webp",
                     ".gif": "image/gif", ".pdf": "application/pdf"}.get(
                         source.suffix.lower(), "application/octet-stream")
        digest = hashlib.sha256(data).hexdigest()
        meta = {"attachmentId": "sha256:" + digest, "mediaType": media,
                "bytes": len(data), "name": name}
        entry = attachments_mod.entry_for(meta)
        pending_attachments.append((data, entry))
        if media.startswith("image/"):
            return {"type": "image", "attachment": {
                "attachmentId": meta["attachmentId"], "mediaType": media,
                "bytes": len(data), "name": name}}
        return None

    for message in selected:
        payload = message["payload"] if isinstance(message["payload"], dict) else {}
        parts = payload.get("parts") if isinstance(payload.get("parts"), list) else []
        ms = message["created_at"]
        if message["role"] == "user":
            turn += 1
            text = _text_from_parts(parts)
            attachments = payload.get("attachments")
            image_blocks = []
            if isinstance(attachments, list):
                for item in attachments:
                    block = attachment_block(item)
                    if block is not None:
                        image_blocks.append(block)
            if not text and not image_blocks and not attachments:
                continue
            body: dict[str, Any] = {
                "content": ([{"type": "text", "text": text}] if text else []) + image_blocks,
                "id": message["message_id"], "origin": "user", "turn": turn,
            }
            if isinstance(attachments, list) and attachments:
                body["attachments"] = attachments
            emit("user_message", body, ms)
            continue

        if message["role"] != "assistant":
            continue
        blocks: list[dict[str, Any]] = []

        def flush(segment: int) -> None:
            nonlocal blocks
            if not blocks:
                return
            body: dict[str, Any] = {
                "id": f"{message['message_id']}:{segment}",
                "content": blocks,
                "turn": turn,
            }
            if session["model"] and session["model"] != "auto":
                body["model"] = session["model"]
                body["provider"] = host
            emit("assistant_message", body, ms)
            blocks = []

        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            kind = str(part.get("type") or "")
            if kind in ("text", "thinking"):
                text = str(part.get("text") or "")
                if text:
                    blocks.append({"type": "thinking" if kind == "thinking" else "text",
                                   "text": text})
            elif kind == "tool":
                flush(index)
                tool = part.get("tool") if isinstance(part.get("tool"), dict) else {}
                call_id = str(tool.get("id") or "")
                name = str(tool.get("name") or "")
                args = tool.get("input") if isinstance(tool.get("input"), dict) else {}
                body = {"tool": name, "call_id": call_id}
                body.update(fr._content("args", args))
                if turn:
                    body["turn"] = turn
                emit("tool_call", body, ms)
                tool_calls += 1
                response = tool.get("response")
                if response is None and isinstance(tool.get("shellOutput"), dict):
                    response = tool["shellOutput"].get("text")
                text = response if isinstance(response, str) else (
                    json.dumps(response, ensure_ascii=False) if response is not None else "")
                status = str(tool.get("status") or "")
                result_content = [{"type": "text", "text": text}] if text else []
                result_images = attachments_mod.inline_images(response, prefix="tool-image")
                for image_block, data, _name in result_images:
                    pending_attachments.append((data, attachments_mod.entry_for(image_block["attachment"])))
                    result_content.append(image_block)
                result: dict[str, Any] = {
                    "call_id": call_id,
                    "content": result_content,
                    "is_error": status not in ("completed", "succeeded", "ok"),
                    "tool": name,
                    "status": status,
                }
                execution: dict[str, Any] = {"status": status}
                if isinstance(tool.get("durationMs"), (int, float)):
                    execution["duration_ms"] = int(tool["durationMs"])
                result["execution"] = execution
                if turn:
                    result["turn"] = turn
                emit("tool_result", result, ms)
                tool_results += 1
                                                                            
        flush(len(parts))

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
        attachments_mod.save_pending(store, session_id, store.locate(session_id),
                                     pending_attachments)
        store.rebuild_md(session_id)

    capture = {
        "host_seq": max((m["sequence"] for m in messages), default=watermark),
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
    for host, db in qoder_databases():
        try:
            sessions = _session_rows(db)
        except sqlite3.Error as exc:
            out.append({"session_id": "", "error": f"qoder: {exc}"})
            continue
        for session in sessions:
            session_id = session["session_id"]
            try:
                state = store.session_capture(session_id)
                if (state.get("source_updated_at") == session["updated_at"]
                        and state.get("commit_count") is not None):
                    try:
                        store.update_title(session_id,
                                                   session.get("title") or "")
                    except (AttributeError, OSError):
                        pass
                    continue
                out.append(_ingest_session(db, host, session, store, key or
                                           _key(), stamp, since_ms))
            except Exception as exc:
                out.append({"session_id": session_id,
                            "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out


def _key():
    from .easy import _device_key
    return _device_key()
