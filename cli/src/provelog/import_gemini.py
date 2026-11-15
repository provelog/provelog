"""Import Gemini CLI session history.

Gemini CLI stores sessions under ``<GEMINI_CLI_HOME>/.gemini`` (or
``~/.gemini``) at ``tmp/<project-id>/chats/``.  Two generations coexist:
whole-session ``*.json`` files and replayable ``*.jsonl`` files.  Message
types ``user`` / ``gemini`` / ``model`` carry the conversation; assistant
messages may carry ``toolCalls``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .chatview.model import usage_dict
from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900


def gemini_root() -> Path:
    home = os.environ.get("GEMINI_CLI_HOME")
    if home:
        return Path(home).expanduser() / ".gemini"
    return Path.home() / ".gemini"


def qwen_root() -> Path:
    env = os.environ.get("QWEN_HOME")
    return Path(env).expanduser() if env else Path.home() / ".qwen"


def session_files(root: Path | None = None) -> list[Path]:
    base = root or gemini_root()
    out = []
    for pattern in ("tmp/*/chats/*.json", "tmp/*/chats/*.jsonl",
                    "projects/*/chats/*.json", "projects/*/chats/*.jsonl"):
        out.extend(base.glob(pattern))
    return sorted((p for p in out if p.is_file()),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def qwen_session_files(root: Path | None = None) -> list[Path]:
    return session_files(root or qwen_root())


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


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


def _tool_result_text(value: Any) -> str:
    """Text from a Gemini tool result, including functionResponse wrappers."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("text"), str):
                parts.append(item["text"])
            response = item.get("functionResponse") or item.get("function_response")
            if isinstance(response, dict):
                payload = response.get("response")
                if isinstance(payload, dict):
                    for key in ("output", "content", "result", "text", "message"):
                        if isinstance(payload.get(key), str):
                            parts.append(payload[key])
                            break
                    else:
                        parts.append(json.dumps(payload, ensure_ascii=False))
                elif isinstance(payload, str):
                    parts.append(payload)
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        function_response = value.get("functionResponse") or value.get("function_response")
        if isinstance(function_response, dict):
            return _tool_result_text(function_response.get("response"))
        for key in ("output", "content", "result", "text", "message"):
            if isinstance(value.get(key), str):
                return value[key]
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def _tool_result_error(value: Any) -> bool:
    if isinstance(value, dict):
        function_response = value.get("functionResponse") or value.get("function_response")
        if isinstance(function_response, dict):
            return _tool_result_error(function_response.get("response"))
        return bool(value.get("isError") or value.get("error")
                    or value.get("success") is False)
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and _tool_result_error(
                    item.get("functionResponse") or item.get("function_response")
                    or item):
                return True
    return False


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(str(p.get("text") or "") for p in value
                         if isinstance(p, dict) and p.get("text")).strip()
    if isinstance(value, dict):
        for key in ("parts", "content"):
            if key in value:
                return _text(value[key])
    return ""


def _message_text(record: dict[str, Any]) -> str:
    """Gemini/Qwen records wrap content differently: Gemini uses a top-level
    ``content``; Qwen 0.26 nests it under ``message.parts``."""
    nested = record.get("message")
    if isinstance(nested, dict):
        value = nested.get("parts", nested.get("content"))
        if value is not None:
            return _text(value)
    return _text(record.get("parts", record.get("content")))


def _message_parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    nested = message.get("message")
    if isinstance(nested, dict):
        parts = nested.get("parts")
        if isinstance(parts, list):
            return [part for part in parts if isinstance(part, dict)]
    parts = message.get("parts")
    return [part for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []


def _function_call(part: dict[str, Any]) -> dict[str, Any] | None:
    call = part.get("functionCall") or part.get("function_call")
    return call if isinstance(call, dict) else None


def _function_response(part: dict[str, Any]) -> dict[str, Any] | None:
    response = part.get("functionResponse") or part.get("function_response")
    return response if isinstance(response, dict) else None


def _records(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".jsonl":
        lines = [line for line in raw.splitlines() if line.strip()]
        header: dict[str, Any] = {}
        messages = []
        consumed = 0
        for index, line in enumerate(lines):
            try:
                row = json.loads(line)
            except ValueError:
                if index == len(lines) - 1:
                    break
                consumed = index + 1
                continue
            if not isinstance(row, dict):
                consumed = index + 1
                continue
            if row.get("type") in ("user", "assistant", "gemini", "model",
                                    "tool_result"):
                messages.append(row)
            elif not header and any(k in row for k in ("sessionId", "startTime", "lastUpdated")):
                header = row
            consumed = index + 1
        return header, messages, consumed
    doc = _json(raw)
    if not isinstance(doc, dict):
        return {}, [], 0
    messages = doc.get("messages") if isinstance(doc.get("messages"), list) else []
    return doc, [m for m in messages if isinstance(m, dict)], len(messages)


def _project(path: Path) -> str:
    project_dir = path.parent.parent
    try:
        return (project_dir / ".project_root").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(path: Path, store: Any, *, key: Any | None = None,
           stream: str | None = None, now: float | None = None,
           since_ms: int | None = None, host: str = "gemini") -> dict[str, Any]:
    from . import attachments as attachments_mod
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    header, messages, consumed = _records(path)
    if not header and messages:
        first = messages[0]
        header = {"sessionId": first.get("sessionId") or path.stem,
                  "startTime": first.get("timestamp"),
                  "cwd": first.get("cwd")}
    session_id = str(header.get("sessionId") or path.stem)
    stream = stream or f"local-{host}-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)
    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(session_id, host_seq=len(messages))
        return {"session_id": session_id, "skipped": "legacy_watermark"}

    watermark = int(state.get("host_seq", -1))
    selected = messages[max(0, watermark):]
    if since_ms is not None and folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}

    created = _ms(header.get("startTime"))
    if not created and messages:
        created = _ms(messages[0].get("timestamp"))
    title = ""
    for message in messages:
        if str(message.get("type") or "") == "user":
            text = _message_text(message)
            if text:
                title = text.splitlines()[0][:120]
                break
    title = title or session_id
    cwd = str(header.get("cwd") or "") or _project(path)

    key = key or _device_key()
    if folder is None:
        meta = {"host": host, "host_session_id": session_id,
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
    tool_calls = tool_results = 0
    pending_images: list[tuple[bytes, dict[str, Any]]] = []
    for index, message in enumerate(selected):
        kind = str(message.get("type") or "")
        ms = _ms(message.get("timestamp")) or created
        if kind == "user":
            turn += 1
            text = _message_text(message)
            images = attachments_mod.inline_images(_message_parts(message), prefix="user-image")
            refs = attachments_mod.image_refs(_message_parts(message), prefix="user-image")
            if text or images or refs:
                content = [{"type": "text", "text": text}] if text else []
                for block, data, _name in images:
                    pending_images.append((data, attachments_mod.entry_for(block["attachment"])))
                    content.append(block)
                content.extend(refs)
                emit("user_message", {
                    "content": content,
                    "id": str(message.get("id") or f"{session_id}-u-{turn}"),
                    "origin": "user", "turn": turn,
                }, ms)
        elif kind == "tool_result":
            for part in _message_parts(message):
                response = _function_response(part)
                if response is None:
                    continue
                result_content = [{"type": "text", "text": _tool_result_text(part)}]
                result_images = attachments_mod.inline_images(part, prefix="tool-image")
                for image_block, data, _name in result_images:
                    pending_images.append((data, attachments_mod.entry_for(image_block["attachment"])))
                    result_content.append(image_block)
                result_content.extend(attachments_mod.image_refs(part, prefix="tool-image"))
                payload = {"call_id": str(response.get("id") or ""),
                           "tool": str(response.get("name") or ""),
                           "content": result_content,
                           "is_error": _tool_result_error(part)}
                if turn:
                    payload["turn"] = turn
                emit("tool_result", payload, ms)
                tool_results += 1
        elif kind in ("assistant", "gemini", "model"):
            text = _message_text(message)
            blocks = [{"type": "text", "text": text}] if text else []
            images = attachments_mod.inline_images(_message_parts(message), prefix="assistant-image")
            for block, data, _name in images:
                pending_images.append((data, attachments_mod.entry_for(block["attachment"])))
                blocks.append(block)
            blocks.extend(attachments_mod.image_refs(_message_parts(message), prefix="assistant-image"))
            if blocks:
                body = {
                    "id": str(message.get("id") or f"{session_id}-a-{watermark + index + 1}"),
                    "content": blocks, "turn": turn,
                }
                model_name = str(message.get("model") or "")
                if model_name:
                    body["model"] = model_name
                    body["provider"] = "qwen" if host == "qwen" else "gemini"
                usage_meta = message.get("usageMetadata")
                if isinstance(usage_meta, dict):
                    usage = usage_dict({
                        "input": usage_meta.get("promptTokenCount"),
                        "output": usage_meta.get("candidatesTokenCount"),
                        "cache_read": usage_meta.get("cachedContentTokenCount"),
                    })
                    if usage:
                        body["usage"] = usage
                emit("assistant_message", body, ms)
            calls = message.get("toolCalls")
            if isinstance(calls, list):
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    name = str(call.get("name") or "")
                    call_id = str(call.get("id") or f"{session_id}-tool-{index}-{tool_calls}")
                    args = call.get("args") if isinstance(call.get("args"), dict) else {}
                    body = {"tool": name, "call_id": call_id}
                    body.update(fr._content("args", args))
                    if turn:
                        body["turn"] = turn
                    emit("tool_call", body, ms)
                    tool_calls += 1
                    result = call.get("result")
                    status = str(call.get("status") or "")
                    if result is not None or status:
                        text_result = _tool_result_text(result)
                        payload = {"call_id": call_id, "tool": name,
                                   "content": [{"type": "text", "text": text_result}],
                                   "is_error": status.lower() in ("error", "failed", "failure")
                                   or _tool_result_error(result)}
                        if turn:
                            payload["turn"] = turn
                        emit("tool_result", payload, ms)
                        tool_results += 1
            for part in _message_parts(message):
                call = _function_call(part)
                if call is None:
                    continue
                name = str(call.get("name") or "")
                call_id = str(call.get("id") or f"{session_id}-tool-{index}-{tool_calls}")
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                body = {"tool": name, "call_id": call_id}
                body.update(fr._content("args", args))
                if turn:
                    body["turn"] = turn
                emit("tool_call", body, ms)
                tool_calls += 1

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
        "host_seq": len(messages),
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
            "tool_calls": tool_calls, "tool_results": tool_results,
            "committed": committed}


def watch_once(store: Any, now: float | None = None, key: Any | None = None,
               since_ms: int | None = None) -> list[dict[str, Any]]:
    import time as _time
    stamp = now if now is not None else _time.time()
    out: list[dict[str, Any]] = []
    for host, paths in (("gemini", session_files()),
                        ("qwen", qwen_session_files())):
        for path in paths:
            session_id = path.stem
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
                out.append(ingest(path, store, key=key, now=stamp,
                                  since_ms=since_ms, host=host))
            except Exception as exc:
                out.append({"session_id": session_id,
                            "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out
