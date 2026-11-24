"""Import WorkBuddy conversations from Tencent's own session log.

WorkBuddy (Tencent CodeBuddy desktop, 5.9.x) writes one append-only JSONL per
conversation under ``~/.workbuddy/projects/<project>/<session>.jsonl``.  The
interesting records are:

``message``
    ``role=user`` content is a mix of injected context (``<system-reminder>``)
    and the real prompt (``<user_query>``); ``role=assistant`` content is
    ``output_text``.  The assistant record also carries model and usage.
``reasoning``
    the model's thinking, linked to an assistant message by ``messageId``.
``function_call`` / ``function_call_result``
    tool name, call id, JSON arguments, status and output, linked by
    ``callId``.  Large outputs are stored beside the log under
    ``<session>/tool-results/`` and only a preview is inline.
``ai-title``
    the conversation title.

This module maps those records onto the same event vocabulary as the DSH
importer.  It is a read-only collector: it never changes WorkBuddy settings.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from . import attachments as attachments_mod
from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state
from .platform_paths import app_config_dir
from .textlines import read_jsonl_lines

LOG_SUFFIX = ".jsonl"
COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900

_USER_QUERY = re.compile(r"<user_query>(.*?)</user_query>", re.S)
_PERSISTED = re.compile(r"Full output saved to:\s*(\S+)")


def workbuddy_home(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    env = os.environ.get("WORKBUDDY_HOME")
    if env:
        return Path(env).expanduser()
    legacy = Path.home() / ".workbuddy"
    if legacy.is_dir():
        return legacy
                                                     
    native = app_config_dir("WorkBuddy")
    return native if native.is_dir() else legacy


def session_files(home: Path) -> list[Path]:
    """Every local WorkBuddy conversation log, newest first.

    A conversation can also be ``transport=cloud`` (the database says so but
    there is no local file); those are simply not here.  Tool output files and
    rollback journals live in sibling directories, so the strict
    ``projects/*/*.jsonl`` pattern excludes them.
    """
    files = [p for p in Path(home).glob("projects/*/*.jsonl") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def read_events(path: Path) -> tuple[list[dict[str, Any]], int]:
    """(events, number of complete lines consumed).

    WorkBuddy appends while we read.  A malformed trailing line is normal
    during a write; everything before it is still valid, so it is not fatal.
    A malformed line in the middle is skipped and counted as consumed so the
    next pass does not get stuck on it.
    """
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


def _event_ms(event: dict[str, Any]) -> int:
    value = event.get("timestamp")
    return int(value) if isinstance(value, (int, float)) else 0


def _iso(ms: int) -> str:
    from datetime import UTC, datetime
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _facts(events: list[dict[str, Any]], path: Path) -> dict[str, Any]:
    session_id = path.stem
    cwd = ""
    title = ""
    model = ""
    created = 0
    for event in events:
        sid = str(event.get("sessionId") or "")
        if sid and session_id == path.stem:
            session_id = sid
        if not cwd and event.get("cwd"):
            cwd = str(event["cwd"])
        if event.get("type") == "ai-title" and event.get("aiTitle"):
            title = str(event["aiTitle"])
        if event.get("type") == "message" and event.get("role") == "assistant":
            provider = event.get("providerData") or {}
            if not model and provider.get("model"):
                model = str(provider["model"])
        ts = _event_ms(event)
        if ts and (not created or ts < created):
            created = ts
    if not title:
        for event in events:
            if event.get("type") != "message" or event.get("role") != "user":
                continue
            text = "\n".join(_text_blocks(event.get("content"))).strip()
            if text:
                _context, query = _split_user_text(text)
                title = (query or text).splitlines()[0][:120]
                break
    return {"session_id": session_id, "cwd": cwd, "title": title,
            "model": model, "created_at": created}


def _split_user_text(text: str) -> tuple[str, str]:
    """(context, real user query) from WorkBuddy's wrapped user content."""
    match = _USER_QUERY.search(text)
    if not match:
        return text.strip(), ""
    query = match.group(1).strip()
    context = (text[:match.start()] + text[match.end():]).strip()
    return context, query


def _text_blocks(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [str(block.get("text") or "") for block in content
            if isinstance(block, dict) and block.get("type") in
            ("input_text", "output_text", "text")]


def _reasoning_text(event: dict[str, Any]) -> str:
    raw = event.get("rawContent")
    if isinstance(raw, list):
        parts = [str(b.get("text") or "") for b in raw
                 if isinstance(b, dict) and b.get("type") == "reasoning_text"]
        if parts:
            return "".join(parts)
    content = event.get("content")
    if isinstance(content, list):
        return "".join(str(b.get("text") or "") for b in content
                       if isinstance(b, dict))
    return ""


def _tool_output_text(output: Any) -> str:
    if isinstance(output, dict):
        if output.get("type") == "text":
            return str(output.get("text") or "")
        if "text" in output:
            return str(output["text"] or "")
        return json.dumps(output, ensure_ascii=False)
    return str(output or "")


def _usage(event: dict[str, Any]) -> dict[str, Any] | None:
    provider = event.get("providerData") or {}
    usage = provider.get("usage")
    if isinstance(usage, dict):
        return usage
    raw = provider.get("rawUsage")
    if isinstance(raw, dict):
        return raw
    return None


def _attachment_entry(path: Path) -> dict[str, Any] | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    digest = hashlib.sha256(data).hexdigest()
    return {
        "file": f"{digest}.txt",
        "path": f"attachments/{digest}.txt",
        "sha256": f"sha256:{digest}",
        "bytes": len(data),
        "media_type": "text/plain",
        "name": path.name,
    }


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(path: Path, store: Any, *, key: Any | None = None,
           stream: str | None = None, now: float | None = None,
           since_ms: int | None = None, host: str = "workbuddy") -> dict[str, Any]:
    """Append the events a WorkBuddy session has gained since the last poll."""
    import time as _time

    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else _time.time()
    events, consumed = read_events(path)
    if not events:
        return {"session_id": path.stem, "skipped": "empty"}
    facts = _facts(events, path)
    session_id = facts["session_id"]
    stream = stream or f"local-{host}-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)

    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=consumed)
        return {"session_id": session_id, "skipped": "legacy_watermark",
                "host_seq": consumed}

    watermark = int(state.get("host_seq", -1))
    if consumed < watermark:
        return {"session_id": session_id, "skipped": "source_shrank",
                "host_seq": watermark}
    deleted_at = state.get("deleted_at_ms")
    selected = [
        (index, event) for index, event in enumerate(events)
        if index >= watermark
        and (since_ms is None or _event_ms(event) > since_ms)
        and (not deleted_at or _event_ms(event) > int(deleted_at))
    ]
    if since_ms is not None and folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}
    if folder is None and state and not selected:
        return {"session_id": session_id, "skipped": "deleted_no_new_events"}

    key = key or _device_key()
    if folder is None:
        meta = {"host": host, "host_session_id": session_id,
                "source": "host_import", "imported_from": str(path),
                "capture_point": "host_import"}
        if since_ms is not None:
            meta["capture_from_ms"] = since_ms
            first_ms = min((_event_ms(e) for e in events), default=0)
            meta["head_omitted"] = bool(first_ms and first_ms < since_ms)
        elif watermark >= 0:
            meta["head_omitted"] = True
        if facts["title"]:
            meta["title"] = facts["title"]
        if facts["cwd"]:
            meta["project"] = facts["cwd"]
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

    counts = {"added": 0, "tool_calls": 0, "tool_results": 0}
    reasoning: dict[str, list[str]] = {}
    reasoning_ms: dict[str, int] = {}
    emitted_assistant: set[str] = set()
    pending_attachments: list[tuple[dict[str, Any], Path]] = []
    pending_images: list[tuple[bytes, dict[str, Any]]] = []
    current_turn = 0
    current_step = ""
    title = facts["title"]

    def assistant(mid: str, ms: int, text_parts: list[str],
                  model: str = "", usage: dict[str, Any] | None = None,
                  step: str = "", extra: dict[str, Any] | None = None) -> None:
        if mid in emitted_assistant:
            return
        blocks: list[dict[str, Any]] = []
        think = "".join(reasoning.pop(mid, [])).strip()
        if think:
            blocks.append({"type": "thinking", "text": think})
        blocks.extend({"type": "text", "text": t} for t in text_parts if t)
        if not blocks:
            return
        payload: dict[str, Any] = {"content": blocks, "id": mid}
        if model:
            payload["model"] = model
            payload["provider"] = host
        if usage:
            payload["usage"] = usage
        if current_turn:
            payload["turn"] = current_turn
        if step or current_step:
            payload["step"] = step or current_step
        if extra:
            payload.update(extra)
        emit("assistant_message", payload, ms)
        emitted_assistant.add(mid)
        if model or usage:
            emit("model_call", {
                "provider": host, "model": model, "status": "ok",
                "usage": usage, "derived_from": "assistant_message"}, ms)
        counts["added"] += 1

    for _, event in selected:
        kind = str(event.get("type") or "")
        provider = event.get("providerData") or {}
        mid = str(provider.get("messageId") or event.get("id") or "")
        step = str(provider.get("stepSeq") or "")
        if step:
            current_step = step
        ms = _event_ms(event)

        if kind == "message":
            role = str(event.get("role") or "")
            texts = _text_blocks(event.get("content"))
            if role == "user":
                images = attachments_mod.inline_images(event.get("content"),
                                                       prefix="user-image")
                image_blocks = []
                for block, data, _name in images:
                    pending_images.append((data, attachments_mod.entry_for(block["attachment"])))
                    image_blocks.append(block)
                image_blocks.extend(attachments_mod.image_refs(event.get("content"),
                                                                prefix="user-image"))
                text = "\n".join(texts)
                context, query = _split_user_text(text)
                if host == "codebuddy-code" and not query:
                                                                                 
                                                                                   
                    context, query = "", text.strip()
                if context:
                    emit("user_message", {
                        "content": [{"type": "text", "text": context}],
                        "id": f"{event.get('id') or mid}-context",
                        "origin": "context",
                        **({"turn": current_turn} if current_turn else {}),
                    }, ms)
                    counts["added"] += 1
                if query or image_blocks:
                    current_turn += 1
                    content = [{"type": "text", "text": query}] if query else []
                    content.extend(image_blocks)
                    emit("user_message", {
                        "content": content,
                        "id": str(event.get("id") or mid),
                        "origin": "user", "turn": current_turn,
                    }, ms)
                    counts["added"] += 1
                elif not context:
                    emit("user_message", {
                        "content": [{"type": "text", "text": text}],
                        "id": str(event.get("id") or mid),
                        "origin": "context",
                        **({"turn": current_turn} if current_turn else {}),
                    }, ms)
                    counts["added"] += 1
            elif role == "assistant":
                assistant(mid, ms, texts, model=str(provider.get("model") or ""),
                          usage=_usage(event), step=step)
            continue

        if kind == "reasoning":
            reasoning.setdefault(mid, []).append(_reasoning_text(event))
            reasoning_ms.setdefault(mid, ms)
            continue

        if kind == "function_call":
            call_id = str(event.get("callId") or "")
            raw = event.get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) and raw else raw
            except ValueError:
                args = {"_raw": raw}
            payload = {"tool": str(event.get("name") or ""),
                       "call_id": call_id}
            payload.update(fr._content("args", args))
            if current_turn:
                payload["turn"] = current_turn
            if step or current_step:
                payload["step"] = step or current_step
            emit("tool_call", payload, ms)
            counts["added"] += 1
            counts["tool_calls"] += 1
            continue

        if kind == "function_call_result":
            call_id = str(event.get("callId") or "")
            name = str(event.get("name") or "")
            status = str(event.get("status") or "")
            text = _tool_output_text(event.get("output"))
            truncated = None
            match = _PERSISTED.search(text)
            if match:
                ref = match.group(1).strip()
                source = Path(ref)
                if not source.is_absolute():
                    source = Path(path).parent / ref
                entry = _attachment_entry(source)
                if entry is not None:
                    pending_attachments.append((entry, source))
                    truncated = {"by": "workbuddy",
                                 "kind": "persisted_output",
                                 "external_ref": str(source),
                                 "sha256": entry["sha256"],
                                 "bytes": entry["bytes"]}
                else:
                    truncated = {"by": "workbuddy",
                                 "kind": "persisted_output",
                                 "external_ref": ref}
            payload = {
                "call_id": call_id,
                "content": [{"type": "text", "text": text}],
                "is_error": status in ("error", "failed", "cancelled"),
                "truncated": truncated,
            }
            if name:
                payload["tool"] = name
            if status:
                payload["status"] = status
            if current_turn:
                payload["turn"] = current_turn
            if step or current_step:
                payload["step"] = step or current_step
            emit("tool_result", payload, ms)
            counts["added"] += 1
            counts["tool_results"] += 1
            continue

        if kind == "ai-title" and event.get("aiTitle"):
            title = str(event["aiTitle"])
            continue
                                                                               
                                                                             

                                                                     
    for mid in list(reasoning):
        if mid in emitted_assistant:
            continue
        assistant(mid, reasoning_ms.get(mid, int(stamp * 1000)), [], step="")

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
    if title:
        try:
            store.set_session_fields(session_id, display_title=title)
        except (AttributeError, OSError):
            pass
    return {"session_id": session_id, "stream": stream,
            "added": len(records), "tool_calls": counts["tool_calls"],
            "tool_results": counts["tool_results"], "committed": committed}


def watch_once(store: Any, home: Path,
               now: float | None = None, key: Any | None = None,
               since_ms: int | None = None,
               host: str = "workbuddy") -> list[dict[str, Any]]:
    """One pass over every WorkBuddy session whose log grew."""
    import time as _time

    stamp = now if now is not None else _time.time()
    out: list[dict[str, Any]] = []
    for path in session_files(home):
        session_id = path.stem
        try:
            stat = path.stat()
            state = store.session_capture(session_id)
            if (since_ms is not None and not state
                    and stat.st_mtime * 1000 < since_ms):
                continue
            unchanged = (state.get("source_mtime") == stat.st_mtime
                         and state.get("source_size") == stat.st_size)
            if (unchanged and state.get("commit_count") is not None
                    and store.locate(session_id) is not None):
                continue
            out.append(ingest(path, store, key=key, now=stamp,
                              since_ms=since_ms, host=host))
        except Exception as exc:                                        
            out.append({"session_id": session_id,
                        "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out
