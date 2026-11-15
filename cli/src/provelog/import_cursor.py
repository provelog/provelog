"""Import Cursor desktop Agent/Composer conversations.

Cursor stores all desktop sessions in one SQLite database at
``~/Library/Application Support/Cursor/User/globalStorage/state.vscdb``
(``CURSOR_DESKTOP_USER_DIR`` overrides the ``User`` directory):

``composerHeaders``  one row per session (composerId, title, workspace, times)
``cursorDiskKV``     ``composerData:<id>`` state + ``bubbleId:<id>:<bubble>``
                     messages; bubble type 1 is the person, 2 the assistant.

Bubble order comes from ``composerData.fullConversationHeadersOnly``; the
assistant's ``thinking`` and ``toolFormerData`` fields become thinking, tool
call and tool result records.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state
from .platform_paths import vscode_user_dir

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900


def user_dir() -> Path:
    env = os.environ.get("CURSOR_DESKTOP_USER_DIR")
    if env:
        return Path(env).expanduser()
    return vscode_user_dir("Cursor")


def database() -> Path:
    return user_dir() / "globalStorage" / "state.vscdb"


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
        return value.strip()
    if isinstance(value, list):
        return "\n".join(str(x.get("text") or "") for x in value
                         if isinstance(x, dict) and x.get("text")).strip()
    return ""


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


def _sessions(db: Path) -> list[dict[str, Any]]:
    con = _connect(db)
    try:
        rows = con.execute(
            "select composerId,workspaceId,createdAt,lastUpdatedAt,value "
            "from composerHeaders h where coalesce(isSubagent,0)=0 "
            "and exists (select 1 from cursorDiskKV k where "
            "k.key like 'bubbleId:' || h.composerId || ':%') "
            "order by coalesce(recency,lastUpdatedAt) desc").fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out = []
    for cid, workspace, created, updated, raw in rows:
        head = _json(raw) if isinstance(raw, str) else {}
        if not isinstance(head, dict):
            head = {}
        cwd = ""
        ident = head.get("workspaceIdentifier")
        if isinstance(ident, dict):
            uri = ident.get("uri") if isinstance(ident.get("uri"), dict) else {}
            cwd = str(uri.get("fsPath") or uri.get("path") or "")
        if not cwd:
            loc = head.get("agentLocation")
            if isinstance(loc, dict):
                env = loc.get("environment") if isinstance(loc.get("environment"), dict) else {}
                uri = env.get("uri") if isinstance(env.get("uri"), dict) else {}
                cwd = str(uri.get("fsPath") or "")
        if not cwd:
            repos = head.get("trackedGitRepos")
            if isinstance(repos, list) and repos and isinstance(repos[0], dict):
                cwd = str(repos[0].get("repoPath") or "")
        out.append({"id": str(cid), "workspace": str(workspace or ""),
                    "created": int(created or 0), "updated": int(updated or 0),
                    "head": head, "cwd": cwd})
    return out


def _bubbles(db: Path, session: dict[str, Any]) -> list[dict[str, Any]]:
    con = _connect(db)
    try:
        row = con.execute("select value from cursorDiskKV where key=?",
                          (f"composerData:{session['id']}",)).fetchone()
        data = _json(row[0]) if row and isinstance(row[0], str) else {}
        if not isinstance(data, dict):
            data = {}
        order = []
        headers = data.get("fullConversationHeadersOnly")
        if isinstance(headers, list):
            for h in headers:
                if isinstance(h, dict) and h.get("bubbleId"):
                    order.append(str(h["bubbleId"]))
        rows = con.execute(
            "select key,value from cursorDiskKV where key like ? order by rowid",
            (f"bubbleId:{session['id']}:%",)).fetchall()
        by_id = {}
        for key, raw in rows:
            bubble = _json(raw) if isinstance(raw, str) else {}
            if isinstance(bubble, dict):
                by_id[str(key).rsplit(":", 1)[-1]] = bubble
        if order:
            selected = [by_id[b] for b in order if b in by_id]
            ordered = set(order)
            selected.extend(b for bid, b in by_id.items() if bid not in ordered)
        else:
            selected = list(by_id.values())
        return selected
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(db: Path, session: dict[str, Any], store: Any, *, key: Any | None = None,
           stream: str | None = None, now: float | None = None,
           since_ms: int | None = None) -> dict[str, Any]:
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    session_id = session["id"]
    bubbles = _bubbles(db, session)
    if not bubbles:
        return {"session_id": session_id, "skipped": "empty"}
    stream = stream or f"local-cursor-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)
    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=len(bubbles))
        return {"session_id": session_id, "skipped": "legacy_watermark"}

    watermark = int(state.get("host_seq", -1))
    selected = bubbles[max(0, watermark):]
    if since_ms is not None and folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}

    title = str(session["head"].get("name") or session["head"].get("subtitle") or "").strip()
    if not title:
        for bubble in bubbles:
            if int(bubble.get("type") or 0) == 1:
                title = _text(bubble.get("text")).splitlines()[0][:120]
                if title:
                    break
    title = title or session_id
    created = session["created"]

    key = key or _device_key()
    if folder is None:
        meta = {"host": "cursor", "host_session_id": session_id,
                "source": "host_import", "imported_from": str(db),
                "capture_point": "host_import", "title": title}
        if session["cwd"]:
            meta["project"] = session["cwd"]
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
    for bubble in selected:
        kind = int(bubble.get("type") or 0)
        ms = _ms(bubble.get("createdAt"))
        if kind == 1:
            turn += 1
            text = _text(bubble.get("text"))
            if text:
                emit("user_message", {
                    "content": [{"type": "text", "text": text}],
                    "id": f"cursor-{session_id}-u-{turn}",
                    "origin": "user", "turn": turn,
                }, ms)
            continue
        if kind != 2:
            continue
        blocks = []
        thinking = bubble.get("thinking")
        if isinstance(thinking, dict):
            text = str(thinking.get("text") or "").strip()
            if text:
                blocks.append({"type": "thinking", "text": text})
        text = _text(bubble.get("text"))
        if text:
            blocks.append({"type": "text", "text": text})
        if blocks:
            emit("assistant_message", {
                "id": f"cursor-{session_id}-a-{turn}",
                "content": blocks, "turn": turn,
            }, ms)
        tool = bubble.get("toolFormerData")
        if isinstance(tool, dict):
            call_id = str(tool.get("toolCallId") or "")
            name = str(tool.get("name") or "")
            params = _json(tool.get("params"))
            args = params if isinstance(params, dict) else {}
            body = {"tool": name, "call_id": call_id}
            body.update(fr._content("args", args))
            if turn:
                body["turn"] = turn
            emit("tool_call", body, ms)
            tool_calls += 1
            status = str(tool.get("status") or "")
            if status in ("completed", "error"):
                result = tool.get("result")
                payload = {"call_id": call_id, "tool": name,
                           "content": [{"type": "text", "text": _text(result)}],
                           "is_error": status == "error"}
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
        store.rebuild_md(session_id)

    if title:
        try:
            store.update_title(session_id, title)
        except (AttributeError, OSError):
            pass
    capture = {
        "host_seq": len(bubbles),
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
    db = database()
    if not db.is_file():
        return []
    out: list[dict[str, Any]] = []
    for session in _sessions(db):
        try:
            state = store.session_capture(session["id"])
            if (state.get("source_updated_at") == session["updated"]
                    and state.get("commit_count") is not None):
                continue
            out.append(ingest(db, session, store, key=key or _key(),
                              now=stamp, since_ms=since_ms))
        except Exception as exc:
            out.append({"session_id": session["id"],
                        "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out


def _key():
    from .easy import _device_key
    return _device_key()
