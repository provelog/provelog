"""Import OpenCode and Kilo CLI conversations from their SQLite store.

OpenCode 1.19 and Kilo 7.x share the same OpenCode-derived schema::

    ~/.local/share/opencode/opencode.db
    ~/.local/share/kilo/kilo.db
      session(id, title, directory, model, time_created, time_updated, ...)
      message(id, session_id, time_created, data JSON)
      part(id, message_id, session_id, data JSON)

``data`` is the message/part object minus ids; the reader merges the row ids
back in, exactly as OpenCode's own ``info()`` / ``part()`` do.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state
from .platform_paths import local_appdata, roaming_appdata, xdg_data_home

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900


def _data_candidates(app: str, filename: str) -> list[Path]:
    """Where an XDG-style CLI may keep its database on each platform.

    macOS/Linux use the XDG data directory; Windows Electron/Node builds have
    used both LocalAppData and RoamingAppData across releases, so try both.
    """
    return [
        xdg_data_home() / app / filename,
        local_appdata() / app / filename,
        roaming_appdata() / app / filename,
        Path.home() / ".local" / "share" / app / filename,
    ]


def databases() -> list[tuple[str, Path]]:
    out = []
    for host, env, app, filename in (
        ("opencode", "OPENCODE_DB", "opencode", "opencode.db"),
        ("kilo", "KILO_DB", "kilo", "kilo.db"),
    ):
        explicit = os.environ.get(env)
        candidates = ([Path(explicit).expanduser()] if explicit
                      else _data_candidates(app, filename))
        for path in candidates:
            if path.is_file():
                out.append((host, path))
                break
    zcode_explicit = os.environ.get("ZCODE_DB")
    zcode_candidates = ([Path(zcode_explicit).expanduser()] if zcode_explicit else [
        Path.home() / ".zcode" / "cli" / "db" / "db.sqlite",
        roaming_appdata() / "zcode" / "cli" / "db" / "db.sqlite",
        local_appdata() / "zcode" / "cli" / "db" / "db.sqlite",
    ])
    for path in zcode_candidates:
        if path.is_file():
            out.append(("zcode", path))
            break
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


def _safe(value: Any) -> Any:
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    return value


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(v.get("text") or "") for v in value
                         if isinstance(v, dict) and v.get("type") == "text")
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return ""


def _sessions(db: Path) -> list[dict[str, Any]]:
    con = _connect(db)
    try:
        rows = con.execute(
            "select id,parent_id,title,directory,model,time_created,time_updated "
            "from session order by time_updated desc"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return [{"id": str(r[0]), "parent": str(r[1] or ""), "title": str(r[2] or ""),
             "cwd": str(r[3] or ""), "model": str(r[4] or ""),
             "created": int(r[5] or 0), "updated": int(r[6] or 0)} for r in rows]


def _messages(db: Path, session_id: str) -> list[dict[str, Any]]:
    con = _connect(db)
    try:
        rows = con.execute(
            "select id,time_created,data from message where session_id=? "
            "order by time_created,id", (session_id,)).fetchall()
        out = []
        for mid, created, raw in rows:
            parts = con.execute(
                "select id,time_created,data from part where message_id=? "
                "order by time_created,id", (mid,)).fetchall()
            out.append({"id": str(mid), "created": int(created or 0),
                        "data": _json(raw) if isinstance(raw, str) else {},
                        "parts": [{"id": str(p[0]), "created": int(p[1] or 0),
                                   "data": _json(p[2]) if isinstance(p[2], str) else {}}
                                  for p in parts]})
    finally:
        con.close()
    return out


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(db: Path, session: dict[str, Any], store: Any, *, host: str,
           key: Any | None = None, now: float | None = None,
           since_ms: int | None = None) -> dict[str, Any]:
    from . import attachments as attachments_mod
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    session_id = session["id"]
    stream = f"local-{host}-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)
    messages = _messages(db, session_id)

    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=max(
            (m["created"] for m in messages), default=0))
        return {"session_id": session_id, "skipped": "legacy_watermark"}

    watermark = int(state.get("host_seq", -1))
    selected = [m for m in messages
                if m["created"] > watermark
                and (since_ms is None or m["created"] > since_ms)]
    if since_ms is not None and folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}

    title = session["title"].strip()
    if not title:
        for m in messages:
            data = m["data"] if isinstance(m["data"], dict) else {}
            if data.get("role") == "user":
                title = next((_text(p["data"].get("text")) for p in m["parts"]
                              if isinstance(p["data"], dict) and p["data"].get("type") == "text"), "")
                if title:
                    break
    title = (title or session_id)[:120]

    key = key or _device_key()
    if folder is None:
        meta = {"host": host, "host_session_id": session_id,
                "source": "host_import", "imported_from": str(db),
                "capture_point": "host_import", "title": title}
        if session["cwd"]:
            meta["project"] = session["cwd"]
        if session["parent"]:
            meta["fork_of"] = session["parent"]
        if since_ms is not None:
            meta["capture_from_ms"] = since_ms
            meta["head_omitted"] = bool(session["created"] and session["created"] < since_ms)
        fr = Provelog(stream, key=key, store=store, flush=False, float_text=True)
        fr.chain._clock = _clock_for(session["created"] or int(stamp * 1000))
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
        data = message["data"] if isinstance(message["data"], dict) else {}
        role = str(data.get("role") or "")
        ms = int(data.get("time", {}).get("created") if isinstance(data.get("time"), dict)
                 else message["created"]) or message["created"]
        parts = message["parts"]
        if role == "user":
            turn += 1
            text = "\n".join(
                str(p["data"].get("text") or "") for p in parts
                if isinstance(p["data"], dict) and p["data"].get("type") == "text"
                and p["data"].get("text")
            ).strip()
            image_values = [p["data"] for p in parts
                            if isinstance(p, dict) and isinstance(p.get("data"), dict)]
            images = attachments_mod.inline_images(image_values, prefix="user-image")
            refs = attachments_mod.image_refs(image_values, prefix="user-image")
            if text or images or refs:
                content = [{"type": "text", "text": text}] if text else []
                for block, data, _name in images:
                    pending_images.append((data, attachments_mod.entry_for(block["attachment"])))
                    content.append(block)
                content.extend(refs)
                emit("user_message", {
                    "content": content,
                    "id": message["id"], "origin": "user", "turn": turn,
                }, ms)
            continue

        if role != "assistant":
            continue
        blocks = []
        calls = []
        for p in parts:
            part = p["data"] if isinstance(p["data"], dict) else {}
            kind = part.get("type")
            if kind == "text":
                text = str(part.get("text") or "")
                if text:
                    blocks.append({"type": "text", "text": text})
            elif kind == "reasoning":
                text = str(part.get("text") or "")
                if text:
                    blocks.append({"type": "thinking", "text": text})
            elif kind == "tool":
                calls.append(part)
        if blocks:
            body = {"id": message["id"], "content": blocks, "turn": turn}
            provider = str(data.get("providerID") or "")
            model = str(data.get("modelID") or session["model"] or "")
            if model:
                body["model"] = model
                body["provider"] = provider or host
            usage = data.get("tokens")
            if isinstance(usage, dict):
                body["usage"] = _safe(usage)
            emit("assistant_message", body, ms)
        for part in calls:
            call_id = str(part.get("callID") or "")
            tool = str(part.get("tool") or "")
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            args = state.get("input") if isinstance(state.get("input"), dict) else {}
            body = {"tool": tool, "call_id": call_id}
            body.update(fr._content("args", args))
            if turn:
                body["turn"] = turn
            emit("tool_call", body, ms)
            tool_calls += 1
            status = str(state.get("status") or "")
            output = state.get("output")
            if output is None:
                output = state.get("error")
            text = _text(output)
            result_images = attachments_mod.inline_images(output, prefix="tool-image")
            result_content = [{"type": "text", "text": text}] if text else []
            for image_block, data, _name in result_images:
                pending_images.append((data, attachments_mod.entry_for(image_block["attachment"])))
                result_content.append(image_block)
            result_content.extend(attachments_mod.image_refs(output, prefix="tool-image"))
            result = {"call_id": call_id, "tool": tool,
                      "content": result_content,
                      "is_error": status == "error"}
            if turn:
                result["turn"] = turn
            emit("tool_result", result, ms)
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
        "host_seq": max((m["created"] for m in messages), default=watermark),
        "source_path": str(db),
        "source_mtime": db.stat().st_mtime,
        "source_size": db.stat().st_size,
        "source_updated_at": session["updated"],
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
    for host, db in databases():
        try:
            sessions = _sessions(db)
        except sqlite3.Error as exc:
            out.append({"session_id": "", "error": f"{host}: {exc}"})
            continue
        for session in sessions:
            try:
                state = store.session_capture(session["id"])
                if (state.get("source_updated_at") == session["updated"]
                        and state.get("commit_count") is not None):
                                                                    
                                                                              
                    try:
                        store.update_title(session["id"],
                                                   session.get("title") or "")
                    except (AttributeError, OSError):
                        pass
                    continue
                out.append(ingest(db, session, store, host=host,
                                  key=key or _key(), now=stamp, since_ms=since_ms))
            except Exception as exc:
                out.append({"session_id": session["id"],
                            "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out


def _key():
    from .easy import _device_key
    return _device_key()
