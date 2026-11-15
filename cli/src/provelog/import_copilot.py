"""Import GitHub Copilot CLI session-state events.

Copilot CLI writes ``~/.copilot/session-state/<id>/events.jsonl`` (plus a
``workspace.yaml``).  The event log starts with ``session.start`` and then
contains at least ``user.message`` / ``assistant.message`` records; tool event
types have varied across releases, so this reader accepts common shapes and
ignores unknown records.
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


def root() -> Path:
    return Path(os.environ.get("COPILOT_HOME") or (Path.home() / ".copilot"))


def session_dirs(root_path: Path | None = None) -> list[Path]:
    base = (root_path or root()) / "session-state"
    out = [p for p in base.iterdir() if p.is_dir() and (p / "events.jsonl").is_file()] \
        if base.is_dir() else []
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


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


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(str(x.get("text") or "") for x in value
                         if isinstance(x, dict) and x.get("text")).strip()
    return ""


def _yaml_value(path: Path, key: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + ":"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(folder: Path, store: Any, *, key: Any | None = None,
           stream: str | None = None, now: float | None = None,
           since_ms: int | None = None) -> dict[str, Any]:
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    events, consumed = _read(folder / "events.jsonl")
    if not events:
        return {"session_id": folder.name, "skipped": "empty"}
    session_id = folder.name
    stream = stream or f"local-copilot-{_bare(session_id)}"
    state_folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)
    if not state and state_folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=consumed)
        return {"session_id": session_id, "skipped": "legacy_watermark"}

    watermark = int(state.get("host_seq", -1))
    selected = [e for i, e in enumerate(events) if i >= watermark]
    if since_ms is not None and state_folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}

    start = next((e for e in events if e.get("type") == "session.start"), {})
    data = start.get("data") if isinstance(start.get("data"), dict) else {}
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    cwd = str(context.get("cwd") or _yaml_value(folder / "workspace.yaml", "cwd") or "")
    created = _ms(data.get("startTime") or data.get("startedAt") or start.get("timestamp"))
    title = ""
    for event in events:
        if event.get("type") == "user.message":
            payload = event.get("data") if isinstance(event.get("data"), dict) else {}
            title = _text(payload.get("content")).splitlines()[0][:120]
            if title:
                break
    title = title or session_id

    key = key or _device_key()
    if state_folder is None:
        meta = {"host": "copilot", "host_session_id": session_id,
                "source": "host_import", "imported_from": str(folder),
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
    for event in selected:
        kind = str(event.get("type") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        ms = _ms(event.get("timestamp"))
        if kind == "user.message":
            turn += 1
            text = _text(data.get("content"))
            if text:
                emit("user_message", {
                    "content": [{"type": "text", "text": text}],
                    "id": str(event.get("id") or f"{session_id}-u-{turn}"),
                    "origin": "user", "turn": turn,
                }, ms)
        elif kind == "assistant.message":
            text = _text(data.get("content"))
            if text:
                emit("assistant_message", {
                    "id": str(event.get("id") or data.get("messageId") or ""),
                    "content": [{"type": "text", "text": text}], "turn": turn,
                }, ms)
        elif kind in ("tool.execution_start", "tool.call"):
            call_id = str(data.get("callId") or data.get("toolCallId") or event.get("id") or "")
            tool = str(data.get("toolName") or data.get("name") or "")
            args = data.get("arguments") if isinstance(data.get("arguments"), dict) else data.get("input")
            body = {"tool": tool, "call_id": call_id}
            body.update(fr._content("args", args if isinstance(args, dict) else {}))
            if turn:
                body["turn"] = turn
            emit("tool_call", body, ms)
            tool_calls += 1
        elif kind in ("tool.execution_complete", "tool.result"):
            call_id = str(data.get("callId") or data.get("toolCallId") or "")
            tool = str(data.get("toolName") or data.get("name") or "")
            result = data.get("result") if "result" in data else data.get("output")
            payload = {"call_id": call_id, "tool": tool,
                       "content": [{"type": "text", "text": _text(result)}],
                       "is_error": bool(data.get("error") or data.get("isError"))}
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
        "host_seq": consumed,
        "source_path": str(folder),
        "source_mtime": (folder / "events.jsonl").stat().st_mtime,
        "source_size": (folder / "events.jsonl").stat().st_size,
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
            stat = (folder / "events.jsonl").stat()
            state = store.session_capture(session_id)
            if (since_ms is not None and not state
                    and stat.st_mtime * 1000 < since_ms):
                continue
            if (state.get("source_mtime") == stat.st_mtime
                    and state.get("source_size") == stat.st_size
                    and state.get("commit_count") is not None):
                continue
            out.append(ingest(folder, store, key=key, now=stamp, since_ms=since_ms))
        except Exception as exc:
            out.append({"session_id": session_id,
                        "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out
