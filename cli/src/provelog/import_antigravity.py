"""Import Google Antigravity transcripts.

Antigravity writes one JSONL per session under
``~/.gemini/antigravity*/brain/<sessionId>/.system_generated/logs/transcript.jsonl``.
Records have ``source``, ``created_at`` and ``content``; ``USER_EXPLICIT`` is
the person and ``MODEL`` is the assistant.  User content wraps the request in
``<USER_REQUEST>`` and machine metadata in ``<ADDITIONAL_METADATA>``.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state
from .textlines import read_jsonl_lines

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900
_REQUEST = re.compile(r"<USER_REQUEST>\s*(.*?)\s*(?:<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>\s*)?</USER_REQUEST>", re.S)
_METADATA = re.compile(r"<ADDITIONAL_METADATA>\s*(\{.*?\})\s*</ADDITIONAL_METADATA>", re.S)


def roots() -> list[Path]:
    env = os.environ.get("ANTIGRAVITY_HOME")
    if env:
        return [Path(env).expanduser()]
    base = Path.home() / ".gemini"
    return sorted(p for p in base.glob("antigravity*") if p.is_dir())


def session_files(root: Path | None = None) -> list[Path]:
    out = []
    for base in roots() if root is None else [root]:
        out.extend(base.glob("brain/*/.system_generated/logs/transcript.jsonl"))
    return sorted((p for p in out if p.is_file()),
                  key=lambda p: p.stat().st_mtime, reverse=True)


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


def _user_text(raw: str) -> tuple[str, str]:
    workspace = ""
    metadata = _METADATA.search(raw)
    if metadata:
        try:
            data = json.loads(metadata.group(1))
            if isinstance(data, dict):
                workspace = str(data.get("cwd") or data.get("workspace") or "")
        except ValueError:
            pass
    match = _REQUEST.search(raw)
    return (match.group(1).strip() if match else raw.strip(), workspace)


def _session_id(path: Path) -> str:
                                                                 
    return path.parents[2].name if len(path.parents) >= 3 else path.parent.name


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(path: Path, store: Any, *, key: Any | None = None,
           stream: str | None = None, now: float | None = None,
           since_ms: int | None = None) -> dict[str, Any]:
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    rows, consumed = _read(path)
    if not rows:
        return {"session_id": _session_id(path), "skipped": "empty"}
    session_id = _session_id(path)
    stream = stream or f"local-antigravity-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)
    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=consumed)
        return {"session_id": session_id, "skipped": "legacy_watermark"}

    watermark = int(state.get("host_seq", -1))
    selected = [r for i, r in enumerate(rows) if i >= watermark]
    if since_ms is not None and folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}

    title = ""
    cwd = ""
    for r in rows:
        if r.get("source") != "USER_EXPLICIT":
            continue
        text, ws = _user_text(str(r.get("content") or ""))
        title = text.splitlines()[0][:120] if text else ""
        cwd = ws
        break
    title = title or session_id
    created = min((_ms(r.get("created_at")) for r in rows if r.get("created_at")), default=0)

    key = key or _device_key()
    if folder is None:
        meta = {"host": "antigravity", "host_session_id": session_id,
                "source": "host_import", "imported_from": str(path),
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
    for index, row in enumerate(selected):
        source = str(row.get("source") or "")
        text = str(row.get("content") or "").strip()
        ms = _ms(row.get("created_at")) or created
        if source == "USER_EXPLICIT":
            turn += 1
            user, ws = _user_text(text)
            if user:
                emit("user_message", {
                    "content": [{"type": "text", "text": user}],
                    "id": f"{session_id}-u-{watermark + index + 1}",
                    "origin": "user", "turn": turn,
                    **({"attachments": []} if ws else {}),
                }, ms)
        elif source == "MODEL" and text:
            emit("assistant_message", {
                "id": f"{session_id}-a-{watermark + index + 1}",
                "content": [{"type": "text", "text": text}],
                "turn": turn,
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
            "tool_calls": 0, "tool_results": 0, "committed": committed}


def watch_once(store: Any, now: float | None = None, key: Any | None = None,
               since_ms: int | None = None) -> list[dict[str, Any]]:
    import time as _time
    stamp = now if now is not None else _time.time()
    out: list[dict[str, Any]] = []
    for path in session_files():
        session_id = _session_id(path)
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
            out.append(ingest(path, store, key=key, now=stamp, since_ms=since_ms))
        except Exception as exc:
            out.append({"session_id": session_id,
                        "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out
