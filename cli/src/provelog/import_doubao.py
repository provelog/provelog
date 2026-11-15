"""Import Doubao Work (DoubaoWork) local agent sessions.

Doubao Work keeps agent sessions as plain files, independent of its encrypted
message DB::

    ~/Library/Application Support/DoubaoWork/Default/.doubaowork/
      agent_mode/workspace/.sessions/<sessionId>/
        agents/<agentId>/system/assignment.md
        agents/<agentId>/system/trajectory.jsonl

``assignment.md`` carries the user's requirements turn by turn;
``trajectory.jsonl`` carries assistant messages and tool calls/results in the
OpenAI-style shape.  This is the readable, local transcript we import.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state
from .platform_paths import app_config_dir
from .textlines import read_jsonl_lines

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900
_REQ = re.compile(r"^## \[([^\]]+)\] \u9700\u6c42\s*\n(.*?)(?=^## \[|\Z)", re.M | re.S)
_EXIT = re.compile(r"exited with code\s+(-?\d+)")


def doubao_sessions_root() -> Path:
    env = os.environ.get("DOUBAO_WORK_HOME")
    if env:
        base = Path(env).expanduser()
    else:
        base = app_config_dir("DoubaoWork") / "Default"
    return (base / ".doubaowork" / "agent_mode" / "workspace" / ".sessions")


def session_files(root: Path | None = None) -> list[Path]:
    base = root or doubao_sessions_root()
    out = []
    for path in base.glob("*/agents/*/system/trajectory.jsonl"):
        if path.is_file():
            out.append(path)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def _session_dir(path: Path) -> Path:
                                                               
    return path.parents[3]


def _assignment(path: Path) -> Path:
    return path.parent / "assignment.md"


def _requirements(path: Path) -> list[dict[str, Any]]:
    try:
        text = _assignment(path).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for stamp, body in _REQ.findall(text):
        body = body.strip()
        if body:
            out.append({"time": stamp, "text": body})
    return out


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


_ABS_PATH = re.compile(r"(/(?:Users|home|Volumes|private|opt|srv)/[^\s\"'`;,)]+)")


def _infer_project(events: list[dict[str, Any]]) -> str:
    """Best-effort workspace from the Bash commands in the trajectory.

    Doubao Work records no cwd, but its Bash calls carry absolute paths such as
    ``ls -la /path/to/project``. Use the most frequent existing
    directory; only a heuristic, never a replacement for a host-provided cwd.
    """
    counts: dict[str, int] = {}
    for event in events:
        calls = event.get("tool_calls") if isinstance(event, dict) else None
        if not isinstance(calls, list):
            continue
        for call in calls:
            args = _tool_args(call)
            command = str(args.get("command") or "")
            for match in _ABS_PATH.findall(command):
                path = match.rstrip(".,:")
                try:
                    if Path(path).is_dir():
                        counts[path] = counts.get(path, 0) + 1
                except OSError:
                    continue
    if not counts:
        return ""
    return sorted(counts, key=lambda p: (-counts[p], len(p), p))[0]


def _tool_args(call: Any) -> dict[str, Any]:
    if not isinstance(call, dict):
        return {}
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    args = fn.get("arguments") if isinstance(fn, dict) else None
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return {"_raw": args}
    return args if isinstance(args, dict) else {}


def _tool_name(call: Any) -> str:
    if not isinstance(call, dict):
        return ""
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    return str(fn.get("name") or "") if isinstance(fn, dict) else ""


def _event_time(event: dict[str, Any]) -> int:
    for key in ("time", "timestamp", "created_at", "createdAt", "ts"):
        value = event.get(key)
        if value:
            stamp = _ms(value)
            if stamp:
                return stamp
    return 0


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(path: Path, store: Any, *, key: Any | None = None,
           stream: str | None = None, now: float | None = None,
           since_ms: int | None = None) -> dict[str, Any]:
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    events, consumed = _read(path)
    reqs = _requirements(path)
    if not events and not reqs:
        return {"session_id": _session_dir(path).name, "skipped": "empty"}
    session_id = _session_dir(path).name
    stream = stream or f"local-doubao-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)

    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=consumed,
                                  assignment_seq=len(reqs))
        return {"session_id": session_id, "skipped": "legacy_watermark"}

    watermark = int(state.get("host_seq", -1))
    req_watermark = int(state.get("assignment_seq", -1))
    selected_reqs = reqs[max(0, req_watermark):]
    selected_events = [event for index, event in enumerate(events) if index >= watermark]
    if since_ms is not None and folder is None and not selected_reqs and not selected_events:
        return {"session_id": session_id, "skipped": "before_baseline"}

    created = _ms(reqs[0]["time"]) if reqs else int(stamp * 1000)
    title = ""
    if reqs:
        title = reqs[0]["text"].strip().splitlines()[0][:120]

    key = key or _device_key()
    inferred = _infer_project(events)
    if folder is not None and inferred and manifest.get("project") != inferred:
                                                                              
                                                             
        store.set_session_fields(session_id, project=inferred)
    if folder is None:
        meta = {"host": "doubao-work", "host_session_id": session_id,
                "source": "host_import", "imported_from": str(path),
                "capture_point": "host_import", "title": title}
        if inferred:
            meta["project"] = inferred
        if since_ms is not None:
            meta["capture_from_ms"] = since_ms
            meta["head_omitted"] = bool(created and created < since_ms)
        fr = Provelog(stream, key=key, store=store, flush=False, float_text=True)
        fr.chain._clock = _clock_for(created)
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

    current_turn = 0
    for req in selected_reqs:
        current_turn += 1
        text = req["text"].strip()
        emit("user_message", {
            "content": [{"type": "text", "text": text}],
            "id": f"doubao-{session_id}-u-{current_turn}",
            "origin": "user", "turn": current_turn,
        }, _ms(req["time"]) or created)

    tool_turn = current_turn or 1
    for index, event in enumerate(selected_events):
        role = str(event.get("role") or "")
        event_ms = _event_time(event)
        ms = event_ms or created
        time_source = "event" if event_ms else "session"
        if role == "assistant":
            text = str(event.get("content") or "").strip()
            calls = event.get("tool_calls") if isinstance(event.get("tool_calls"), list) else []
            if text:
                emit("assistant_message", {
                    "id": f"doubao-{session_id}-a-{max(0, watermark) + index + 1}",
                    "content": [{"type": "text", "text": text}],
                    "turn": tool_turn, "ts_source": time_source,
                    **({"usage": event["usage"]} if isinstance(event.get("usage"), dict) else {}),
                }, ms)
            for call in calls:
                call_id = str(call.get("id") or "") if isinstance(call, dict) else ""
                body = {"tool": _tool_name(call), "call_id": call_id,
                        "turn": tool_turn, "ts_source": time_source}
                body.update(fr._content("args", _tool_args(call)))
                emit("tool_call", body, ms)
        elif role == "tool":
            text = str(event.get("content") or "")
            match = _EXIT.search(text)
            code = int(match.group(1)) if match else None
            result: dict[str, Any] = {
                "call_id": str(event.get("tool_call_id") or ""),
                "content": [{"type": "text", "text": text}],
                "is_error": code not in (None, 0),
                "turn": tool_turn, "ts_source": time_source,
            }
            if code is not None:
                result["execution"] = {"status": "succeeded" if code == 0 else "failed",
                                       "exit_code": code}
            emit("tool_result", result, ms)
        elif role == "user":
            text = str(event.get("content") or "").strip()
            if text:
                emit("user_message", {
                    "content": [{"type": "text", "text": text}],
                    "id": f"doubao-{session_id}-u-inline-{watermark + index}",
                    "origin": "user", "turn": tool_turn,
                    "ts_source": time_source,
                }, ms)

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
        "assignment_seq": len(reqs),
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
            "tool_calls": sum(1 for r in records if r.get("action_type") == "tool_call"),
            "tool_results": sum(1 for r in records if r.get("action_type") == "tool_result"),
            "committed": committed}


def watch_once(store: Any, now: float | None = None, key: Any | None = None,
               since_ms: int | None = None) -> list[dict[str, Any]]:
    import time as _time
    stamp = now if now is not None else _time.time()
    out: list[dict[str, Any]] = []
    for path in session_files():
        session_id = _session_dir(path).name
        try:
            stat = path.stat()
            state = store.session_capture(session_id)
            if (since_ms is not None and not state
                    and stat.st_mtime * 1000 < since_ms):
                continue
            unchanged = (state.get("source_mtime") == stat.st_mtime
                         and state.get("source_size") == stat.st_size
                         and state.get("commit_count") is not None)
            if unchanged:
                continue
            out.append(ingest(path, store, key=key, now=stamp, since_ms=since_ms))
        except Exception as exc:
            out.append({"session_id": session_id,
                        "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out
