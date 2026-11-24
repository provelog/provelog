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

import hashlib
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

                                                                              
                                                                
_BLOBREF = re.compile(r"^blobref:([\w.+-]+/[^;]+);([0-9a-fA-F]{64})$")

                                                                   
_USAGE = {"inputOther": "input", "output": "output",
          "inputCacheRead": "cache_read", "inputCacheCreation": "cache_write"}


def _usage(raw: Any) -> dict[str, Any] | None:
    """One usage record mapped onto the viewer's canonical keys."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key, value in raw.items():
        name = _USAGE.get(str(key))
        if name and isinstance(value, (int, float)) and not isinstance(value, bool):
            out[name] = value
    return out or None


def _blob_image(block: Any, blobs: Path) -> tuple[dict[str, Any], bytes] | None:
    """Resolve one ``image_url`` blobref block to record block + bytes.

    The bytes are the agent's own copy beside the wire; only a 64-hex name is
    ever read, so a hostile transcript cannot point the importer elsewhere.
    """
    if not isinstance(block, dict):
        return None
    url = (block.get("image_url") or block.get("imageUrl")
           or block.get("url"))
    if isinstance(url, dict):
        url = url.get("url")
    if not isinstance(url, str):
        return None
    match = _BLOBREF.match(url)
    if match is None:
        return None
    media, name = match.group(1), match.group(2).lower()
    try:
        data = (blobs / name).read_bytes()
    except OSError:
        return None
    if not data:
        return None
    digest = hashlib.sha256(data).hexdigest()
    return ({"type": "image", "attachment": {
        "attachmentId": "sha256:" + digest, "mediaType": media,
        "bytes": len(data), "name": f"tool-image-{digest[:12]}"}}, data)


def _turns_meta(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-turn {start, end, reason} for the page's stopped/failed labels.

    Display metadata only: the signed records stay the source of truth. The
    host numbers turns from zero, the page from one.
    """
    turns: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        turn_no = event.get("turnId")
        if not isinstance(turn_no, int):
            continue
        row = turns.setdefault(str(turn_no + 1), {})
        kind = event.get("type")
        if kind == "agent.turn.started":
            row["start"] = _ms(event.get("time"))
        elif kind == "turn.ended":
            row["end"] = _ms(event.get("time"))
            reason = str(event.get("reason") or "")
            if reason:
                row["reason"] = reason
        elif kind == "turn.cancel":
            row.setdefault("reason", "cancelled")
        elif kind == "agent.turn.ended":
            outcome = str(event.get("outcome") or "")
            if outcome and outcome != "done":
                row.setdefault("reason", "cancelled" if outcome == "aborted" else outcome)
    return turns


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


def _retractions_meta(events: list[dict[str, Any]]) -> list[int]:
    """Host retraction turns, including ones older than the import watermark.

    `context.undone` is view metadata, like host_turns. Computing it from the
    full wire lets the viewer mark retractions in a session that was first
    imported before this field existed, without rewriting signed records.
    """
    turns: set[int] = set()
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "context.undone":
            continue
        try:
            start = int(event.get("fromTurnId"))
        except (TypeError, ValueError):
            continue
        try:
            count = int(event.get("turns") or 1)
        except (TypeError, ValueError):
            count = 1
        for offset in range(max(1, count)):
            turns.add(start + 1 + offset)
    return sorted(turns)


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
    turns_meta = _turns_meta(events)
    retracted_turns = _retractions_meta(events)
    if (folder is not None and turns_meta
            and manifest.get("host_turns") != turns_meta):
                                                                             
                                           
        store.set_session_fields(session_id, host_turns=turns_meta)
        manifest["host_turns"] = turns_meta
    if (folder is not None and retracted_turns
            and manifest.get("host_retracted_turns") != retracted_turns):
        store.set_session_fields(session_id, host_retracted_turns=retracted_turns)
        manifest["host_retracted_turns"] = retracted_turns

    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=consumed)
        return {"session_id": session_id, "skipped": "legacy_watermark",
                "host_seq": consumed}

    watermark = int(state.get("host_seq", -1))
    if consumed < watermark:
        return {"session_id": session_id, "skipped": "source_shrank"}
    deleted_at = int(state.get("deleted_at_ms") or 0)
    selected = [
        (index, event) for index, event in enumerate(events)
        if index >= watermark
        and (since_ms is None or _ms(event.get("time")) > since_ms)
        and (not deleted_at or _ms(event.get("time")) > deleted_at)
    ]
    if since_ms is not None and folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}
    if folder is None and state and not selected:
        return {"session_id": session_id, "skipped": "deleted_no_new_events"}

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
        if retracted_turns:
            meta["host_retracted_turns"] = retracted_turns
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

    counts = {"tool_calls": 0, "tool_results": 0, "model_calls": 0}
    pending: list[dict[str, Any]] = []
    pending_ms = 0
    pending_first = 0                                                          
                                                                               
                                                                             
                                                                         
    pending_images: list[tuple[bytes, dict[str, Any]]] = []
    current_turn = 0
    current_step = 0
    step_model: dict[str, str] = {}
    step_usage: dict[str, Any] | None = None
    step_model_call = False
    call_where: dict[str, tuple[int, int]] = {}
    approvals: dict[str, dict[str, Any]] = {}                              
    approval_for: dict[str, str] = {}                                     
    blobs = path.parent / "blobs"
                                                                               
                                                                        
    prompts = {str(event.get("promptId") or ""): event.get("turnId")
               for event in events if event.get("type") == "turn.prompt"}

    def number(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def place() -> dict[str, Any]:
        order: dict[str, Any] = {}
        if current_turn:
            order["turn"] = current_turn
        if current_step:
            order["step"] = current_step
        return order

    def flush() -> None:
        nonlocal pending, pending_ms, pending_first, step_model_call
        if not pending:
            return
        when = pending_ms or int(stamp * 1000)
        payload: dict[str, Any] = {
            "id": f"kimi-{session_id}-a-{pending_first}",
            "content": pending, **place(),
        }
        if step_model:
            payload["provider"] = step_model.get("provider", "")
            payload["model"] = step_model.get("model", "")
            if step_model.get("effort"):
                payload["reasoning_effort"] = step_model["effort"]
        if step_usage:
            payload["usage"] = step_usage
        emit("assistant_message", payload, when)
                                                                                
                                                                         
                                              
        if not step_model_call and (step_model or step_usage):
            model_call: dict[str, Any] = {
                "provider": step_model.get("provider", ""),
                "model": step_model.get("model", ""),
                "status": "ok", "derived_from": "assistant_message"}
            if step_usage:
                model_call["usage"] = step_usage
            if step_model.get("effort"):
                model_call["reasoning_effort"] = step_model["effort"]
            emit("model_call", model_call, when)
            step_model_call = True
            counts["model_calls"] += 1
        pending = []
        pending_ms = 0
        pending_first = 0

    for index, event in selected:
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
                        mapped = number(prompts.get(str(message.get("id") or "")))
                        current_turn = (mapped + 1) if mapped is not None else current_turn + 1
                        emit("user_message", {
                            "content": ([{"type": "text", "text": text}] if text else []) + image_blocks,
                            "id": str(message.get("id") or f"kimi-{session_id}-u-{current_turn}"),
                            "origin": "user", "turn": current_turn,
                        }, ms)
                    else:
                        emit("user_message", {
                            "content": ([{"type": "text", "text": text}] if text else []) + image_blocks,
                            "id": str(message.get("id") or f"kimi-{session_id}-ctx-{index}"),
                            "origin": "context",
                            **({"turn": current_turn} if current_turn else {}),
                        }, ms)
            elif role == "assistant" and text:
                flush()
                emit("assistant_message", {
                    "id": str(message.get("id") or f"kimi-{session_id}-a-{index}"),
                    "content": [{"type": "text", "text": text}],
                    **place(),
                }, ms)
            continue

        if kind == "context.apply_compaction":
                                                                           
                                                                         
                                                          
            flush()
            continue

        if kind == "forked":
                                                                             
                                                                           
                                                      
            flush()
            continue

        if kind == "turn.prompt":
            turn_no = number(event.get("turnId"))
            if turn_no is not None:
                current_turn = turn_no + 1
            continue

        if kind == "context.undone":
                                                                                
                                                                                
                                             
            taken_back = number(event.get("turns")) or 1
            host_turn = number(event.get("fromTurnId"))
            undone: dict[str, Any] = {"turns": taken_back, "reason": "user_undo",
                                      "source": "context.undone"}
            if host_turn is not None:
                undone["host_turn_id"] = host_turn
                undone["turn"] = host_turn + 1
            emit("retraction", undone, _ms(event.get("time")))
            continue

        if kind == "llm.request":
            step_model = {
                "provider": str(event.get("provider") or ""),
                "model": str(event.get("model") or event.get("modelAlias") or ""),
                "effort": str(event.get("thinkingEffort") or ""),
            }
            continue

        if kind == "usage.record":
            step_usage = _usage(event.get("usage"))
            continue

        if kind == "interaction.request":
                                                                            
                                                                             
            if str(event.get("kind") or "") != "approval":
                continue
            request = event.get("request") or {}
            if not isinstance(request, dict):
                request = {}
            approval_id = str(event.get("id") or request.get("id") or "")
            call_id = str(event.get("toolCallId") or request.get("toolCallId") or "")
            if not approval_id or not call_id:
                continue
            display = request.get("display") if isinstance(request.get("display"), dict) else {}
            entry: dict[str, Any] = {"requested_at_ms": _ms(event.get("time"))}
            action = str(request.get("action") or display.get("summary") or "")
            if action:
                entry["action"] = action
            tool_name = str(request.get("toolName") or "")
            if tool_name:
                entry["tool"] = tool_name
            approvals[approval_id] = entry
            approval_for[call_id] = approval_id
            continue

        if kind == "interaction.resolved":
            entry = approvals.get(str(event.get("id") or ""))
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            decision = str(response.get("decision") or "")
            if entry is not None and decision:
                entry["decision"] = decision
            continue

        if kind == "permission.record_approval_result":
            call_id = str(event.get("toolCallId") or "")
            approval_id = approval_for.get(call_id)
            result = event.get("result") if isinstance(event.get("result"), dict) else {}
            decision = str(result.get("decision") or "")
            if approval_id and decision:
                approvals.setdefault(approval_id, {})["decision"] = decision
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
            step_model, step_usage, step_model_call = {}, None, False
        turn_no = number(loop.get("turnId"))
        if turn_no is not None:
                                                                          
            current_turn = turn_no + 1
        step_no = number(loop.get("step"))
        if step_no is not None:
            current_step = step_no
        if loop_type == "step.begin":
            continue
        if loop_type == "content.part":
            part = loop.get("part") or {}
            if isinstance(part, dict):
                kind_part = str(part.get("type") or "")
                thinking = kind_part in ("think", "thinking", "reasoning")
                text = str((part.get("think") or part.get("text") or "")
                           if thinking else (part.get("text") or ""))
                if text:
                    if not pending:
                        pending_first = index
                    pending.append({"type": "thinking" if thinking else "text",
                                    "text": text})
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
            payload.update(place())
            approval = approvals.get(approval_for.get(call_id, ""))
            if approval:
                payload["approval"] = dict(approval)
            call_where[call_id] = (current_turn, current_step)
            emit("tool_call", payload, ms)
            counts["tool_calls"] += 1
        elif loop_type == "tool.result":
            result = loop.get("result") or {}
            if not isinstance(result, dict):
                result = {}
            output = result.get("output")
                                                                            
                                                                            
                                                                              
            texts: list[str] = []
            image_parts: list[Any] = []
            if isinstance(output, list):
                for part in output:
                    if not isinstance(part, dict):
                        continue
                    if str(part.get("type") or "") == "text":
                        value = str(part.get("text") or "")
                        if value:
                            texts.append(value)
                    elif str(part.get("type") or "") in ("image_url", "input_image",
                                                         "image", "file"):
                        image_parts.append(part)
            elif output is not None and str(output):
                texts.append(str(output))
            output_text = "\n".join(texts)
            is_error = bool(result.get("isError"))
            execution = {}
            match = _EXIT.search(output_text)
            if is_error and match:
                execution["exit_code"] = int(match.group(1))
            result_content = [{"type": "text", "text": output_text}] if output_text else []
            for image_block, data, _name in attachments_mod.inline_images(
                    image_parts, prefix="tool-image"):
                pending_images.append((data, attachments_mod.entry_for(image_block["attachment"])))
                result_content.append(image_block)
            for part in image_parts:
                resolved = _blob_image(part, blobs)
                if resolved is None:
                    continue
                image_block, data = resolved
                pending_images.append((data, attachments_mod.entry_for(image_block["attachment"])))
                result_content.append(image_block)
            for image_block, data, _name in attachments_mod.inline_images(result,
                                                                          prefix="tool-image"):
                pending_images.append((data, attachments_mod.entry_for(image_block["attachment"])))
                result_content.append(image_block)
            result_content.extend(attachments_mod.image_refs(result, prefix="tool-image"))
            payload = {"call_id": str(loop.get("toolCallId") or ""),
                       "content": result_content,
                       "is_error": is_error}
            if execution:
                payload["execution"] = execution
            where = call_where.get(str(loop.get("toolCallId") or ""))
            if where is not None:
                if where[0]:
                    payload["turn"] = where[0]
                if where[1]:
                    payload["step"] = where[1]
            else:
                payload.update(place())
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
        if folder is None and turns_meta:
                                                                          
                                                                       
            store.set_session_fields(session_id, host_turns=turns_meta)

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
            "tool_results": counts["tool_results"],
            "model_calls": counts["model_calls"], "committed": committed}


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
