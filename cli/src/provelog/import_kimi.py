"""Import Kimi Code sessions (desktop and CLI share the same local store).

Kimi Code writes one append-only ``wire.jsonl`` per agent under
``$KIMI_CODE_HOME/sessions/<workDirKey>/<sessionId>/agents/main/wire.jsonl``
(default home ``~/.kimi-code``; older builds used ``~/.kimi``).  The session
directory also carries ``state.json`` with the title and working directory.

The wire is an event log, not a list of finished messages:

* ``context.append_message`` carries a user/assistant message or a host
  injection;
* ``context.append_loop_event`` streams ``content.part`` text between
  ``step.begin`` / ``step.end`` and carries ``tool.call`` / ``tool.result``;
* ``context.apply_compaction`` is a host context rewrite; v1 ignores it
  (legacy evidence may still contain compaction records).

The importer reconstructs the same event vocabulary as the other hosts.  It
only reads the main agent's log; sub-agent wire files stay out of v1.
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

_EXIT = re.compile(r"Command failed with exit code:\s*(\d+)")


def kimi_homes(path: str | os.PathLike[str] | None = None) -> list[Path]:
    """Every Kimi Code home that may have been used, newest location first."""
    if path is not None:
        return [Path(path).expanduser()]
    homes = []
    env = os.environ.get("KIMI_CODE_HOME")
    if env:
        homes.append(Path(env).expanduser())
    homes.extend([Path.home() / ".kimi-code", Path.home() / ".kimi"])
    out, seen = [], set()
    for home in homes:
        key = str(home)
        if key not in seen and home.is_dir():
            out.append(home)
            seen.add(key)
    return out


def session_files(home: Path) -> list[Path]:
    """Main-agent wire logs, newest first (deduplicated by real path)."""
    patterns = ("sessions/*/*/agents/main/wire.jsonl",
                "sessions/*/*/wire.jsonl")
    files: list[Path] = []
    for pattern in patterns:
        files.extend(Path(home).glob(pattern))
    out, seen = [], set()
    for path in files:
        key = str(path.resolve())
        if key not in seen and path.is_file():
            out.append(path)
            seen.add(key)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def _session_dir(path: Path) -> Path:
                                                                  
    if path.parent.name == "main" and path.parent.parent.name == "agents":
        return path.parent.parent.parent
    return path.parent.parent


def _state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads((_session_dir(path) / "state.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


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


def read_wire(path: Path) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    try:
        lines = read_jsonl_lines(path)
    except OSError:
        return [], 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            if index == len(lines) - 1:
                break
            continue
        if isinstance(event, dict):
            events.append(event)
                                                                           
                                                                             
                                                                            
    return events, len(events)


def _text(value: Any) -> str:
    """Join text parts from an append_message content array."""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts = []
    for block in value:
        if isinstance(block, dict) and block.get("type") in ("text", "thinking"):
            text = str(block.get("text") or "")
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _person(origin: Any) -> bool:
    if not isinstance(origin, dict):
        return True
    kind = str(origin.get("kind") or "")
    trigger = str(origin.get("trigger") or "")
    return not kind or kind == "user" or kind.startswith("user") or trigger == "user-slash"


def _facts(path: Path) -> dict[str, Any]:
    state = _state(path)
    session_dir = _session_dir(path)
    title = str(state.get("title") or "").strip()
    cwd = str(state.get("workDir") or state.get("cwd") or "").strip()
    created = _ms(state.get("createdAt"))
    updated = _ms(state.get("updatedAt"))
    surface = str(state.get("client") or state.get("surface") or "").lower()
    if "desktop" in surface:
        surface = "desktop"
    elif "vscode" in surface or "vs-code" in surface:
        surface = "vscode"
    elif "cli" in surface:
        surface = "cli"
    return {"session_id": session_dir.name, "title": title, "cwd": cwd,
            "created_at": created, "updated_at": updated,
            "fork_of": str(state.get("forkedFrom") or ""),
            "surface": surface}


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(path: Path, store: Any, *, key: Any | None = None,
           stream: str | None = None, now: float | None = None,
           since_ms: int | None = None) -> dict[str, Any]:
    import time as _time

    from . import attachments as attachments_mod
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else _time.time()
    events, consumed = read_wire(path)
    if not events:
        return {"session_id": _session_dir(path).name, "skipped": "empty"}
    facts = _facts(path)
    session_id = facts["session_id"]
    stream = stream or f"local-kimi-code-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)

    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=consumed)
        return {"session_id": session_id, "skipped": "legacy_watermark",
                "host_seq": consumed}

    watermark = int(state.get("host_seq", -1))
    if consumed < watermark:
        return {"session_id": session_id, "skipped": "source_shrank"}
    selected = [
        (index, event) for index, event in enumerate(events)
        if index >= watermark
        and (since_ms is None or _ms(event.get("time")) > since_ms)
    ]
    if since_ms is not None and folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}

    key = key or _device_key()
    if folder is None:
        meta = {"host": "kimi-code", "host_session_id": session_id,
                "source": "host_import", "imported_from": str(path),
                "capture_point": "host_import"}
        if since_ms is not None:
            meta["capture_from_ms"] = since_ms
            first = min((_ms(e.get("time")) for e in events), default=0)
            meta["head_omitted"] = bool(first and first < since_ms)
        elif watermark >= 0:
            meta["head_omitted"] = True
        if facts["title"]:
            meta["title"] = facts["title"]
        if facts.get("surface"):
            meta["surface"] = facts["surface"]
        if facts["cwd"]:
            meta["project"] = facts["cwd"]
        if facts["fork_of"]:
            meta["fork_of"] = facts["fork_of"]
        fr = Provelog(stream, key=key, store=store, flush=False, float_text=True)
        fr.chain._clock = _clock_for(facts["created_at"] or int(stamp * 1000))
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

    counts = {"tool_calls": 0, "tool_results": 0}
    pending: list[dict[str, Any]] = []
    pending_ms = 0
    pending_images: list[tuple[bytes, dict[str, Any]]] = []
    assistant_seq = 0
    current_turn = 0

    def flush() -> None:
        nonlocal pending, pending_ms, assistant_seq
        if not pending:
            return
        assistant_seq += 1
        emit("assistant_message", {
            "id": f"kimi-{session_id}-{assistant_seq}",
            "content": pending,
        }, pending_ms or int(stamp * 1000))
        pending = []
        pending_ms = 0

    for _, event in selected:
        kind = event.get("type")
        if kind == "context.append_message":
            message = event.get("message") or {}
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "")
            text = _text(message.get("content"))
            ms = _ms(message.get("time") or event.get("time"))
            origin = message.get("origin")
            if role == "user":
                images = attachments_mod.inline_images(message.get("content"),
                                                       prefix="user-image")
                refs = attachments_mod.image_refs(message.get("content"),
                                                   prefix="user-image")
                if text or images or refs:
                    image_blocks = []
                    for block, data, _name in images:
                        pending_images.append((data, attachments_mod.entry_for(block["attachment"])))
                        image_blocks.append(block)
                    image_blocks.extend(refs)
                    if _person(origin):
                        current_turn += 1
                        emit("user_message", {
                            "content": ([{"type": "text", "text": text}] if text else []) + image_blocks,
                            "id": str(message.get("id") or f"kimi-{session_id}-u-{current_turn}"),
                            "origin": "user", "turn": current_turn,
                        }, ms)
                    else:
                        emit("user_message", {
                            "content": ([{"type": "text", "text": text}] if text else []) + image_blocks,
                            "id": str(message.get("id") or f"kimi-{session_id}-ctx-{current_turn}"),
                            "origin": "context",
                            **({"turn": current_turn} if current_turn else {}),
                        }, ms)
            elif role == "assistant" and text:
                flush()
                assistant_seq += 1
                emit("assistant_message", {
                    "id": str(message.get("id") or f"kimi-{session_id}-a-{assistant_seq}"),
                    "content": [{"type": "text", "text": text}],
                    **({"turn": current_turn} if current_turn else {}),
                }, ms)
            continue

        if kind == "context.apply_compaction":
                                                                           
                                                                         
                                                          
            flush()
            continue

        if kind == "forked":
                                                                             
                                                                           
                                                      
            flush()
            continue

        if kind != "context.append_loop_event":
            continue
        loop = event.get("event") or {}
        if not isinstance(loop, dict):
            continue
        loop_type = str(loop.get("type") or "")
        ms = _ms(event.get("time"))
        if loop_type == "step.begin":
            flush()
        elif loop_type == "content.part":
            part = loop.get("part") or {}
            if isinstance(part, dict):
                text = str(part.get("text") or "")
                if text:
                    block_type = "thinking" if part.get("type") == "thinking" else "text"
                    pending.append({"type": block_type, "text": text})
                    pending_ms = ms or pending_ms
        elif loop_type == "step.end":
            flush()
        elif loop_type == "tool.call":
            flush()
            call_id = str(loop.get("toolCallId") or "")
            args = loop.get("args")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"_raw": args}
            payload: dict[str, Any] = {"tool": str(loop.get("name") or ""),
                                       "call_id": call_id}
            payload.update(fr._content("args", args if isinstance(args, dict) else {}))
            if current_turn:
                payload["turn"] = current_turn
            emit("tool_call", payload, ms)
            counts["tool_calls"] += 1
        elif loop_type == "tool.result":
            result = loop.get("result") or {}
            if not isinstance(result, dict):
                result = {}
            output = str(result.get("output") or "")
            is_error = bool(result.get("isError"))
            execution = {}
            match = _EXIT.search(output)
            if is_error and match:
                execution["exit_code"] = int(match.group(1))
            result_images = attachments_mod.inline_images(result, prefix="tool-image")
            result_content = [{"type": "text", "text": output}] if output else []
            for image_block, data, _name in result_images:
                pending_images.append((data, attachments_mod.entry_for(image_block["attachment"])))
                result_content.append(image_block)
            result_content.extend(attachments_mod.image_refs(result, prefix="tool-image"))
            payload = {"call_id": str(loop.get("toolCallId") or ""),
                       "content": result_content,
                       "is_error": is_error}
            if execution:
                payload["execution"] = execution
            if current_turn:
                payload["turn"] = current_turn
            emit("tool_result", payload, ms)
            counts["tool_results"] += 1

    flush()

    committed = False
    if fr.chain._open:
        since_commit = stamp - float(state.get("commit_at") or 0)
        pending_count = int(fr.chain._seq) - int(state.get("commit_count") or 0)
        if records and (state.get("commit_count") is None
                        or pending_count >= COMMIT_EVERY_RECORDS
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
    return {"session_id": session_id, "stream": stream,
            "added": len(records), "tool_calls": counts["tool_calls"],
            "tool_results": counts["tool_results"], "committed": committed}


def watch_once(store: Any, homes: list[Path] | None = None,
               now: float | None = None, key: Any | None = None,
               since_ms: int | None = None) -> list[dict[str, Any]]:
    import time as _time

    stamp = now if now is not None else _time.time()
    out: list[dict[str, Any]] = []
    for home in homes or kimi_homes():
        for path in session_files(home):
            session_id = _session_dir(path).name
            try:
                stat = path.stat()
                state = store.session_capture(session_id)
                if (since_ms is not None and not state
                        and stat.st_mtime * 1000 < since_ms):
                    continue
                unchanged = (state.get("source_mtime") == stat.st_mtime
                             and state.get("source_size") == stat.st_size)
                if unchanged and state.get("commit_count") is not None:
                                                                           
                                                                              
                                                      
                    try:
                        store.update_title(
                            session_id, str(_state(path).get("title") or ""))
                    except (AttributeError, OSError):
                        pass
                    continue
                out.append(ingest(path, store, key=key, now=stamp,
                                  since_ms=since_ms))
            except Exception as exc:
                out.append({"session_id": session_id,
                            "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out
