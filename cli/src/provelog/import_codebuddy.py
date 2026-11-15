"""Import CodeBuddy IDE conversations from its local HistoryService store.

CodeBuddy ships two hosts:

* **CodeBuddy Code (CLI)** writes WorkBuddy JSONL under
  ``~/.codebuddy/projects/...``; the WorkBuddy reader covers that shape.
* **CodeBuddy IDE** keeps one directory per conversation under
  ``.../CodeBuddyExtension/Data/<uid>/CodeBuddyIDE/<uid>/history/
  <md5(workspace)>/<conversationId>/``.  ``index.json`` holds message order;
  the prose is in ``messages/<messageId>.json`` as an escaped JSON string.

This module reads the IDE shape.  The prose is not in
``codebuddy-sessions.vscdb`` (that only has title/cwd/status metadata), which
is why a VS Code ``state.vscdb`` reader alone finds nothing.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state
from .platform_paths import app_config_dir

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900
_CONTEXT = re.compile(
    r"<(user_info|git_status|additional_data|system_reminder|"
    r"artifact_directory_path)\b[\s\S]*?</\1>", re.I)
_QUERY = re.compile(r"<user_query>\s*([\s\S]*?)\s*</user_query>", re.I)
_WORKSPACE = re.compile(r"Workspace Folder:\s*([^\n]+)")
_EXIT = re.compile(r'"exitCode"\s*:\s*(-?\d+)')


def codebuddy_root() -> Path:
    env = os.environ.get("CODEBUDDY_IDE_HOME")
    if env:
        return Path(env).expanduser()
    return app_config_dir("CodeBuddyExtension")


def conversation_indexes(root: Path | None = None) -> list[Path]:
    base = root or codebuddy_root()
    out = []
    for path in base.glob("Data/*/CodeBuddyIDE/*/history/*/*/index.json"):
        if path.is_file() and (path.parent / "messages").is_dir():
            out.append(path)
                                                                           
                                                                          
    newest: dict[str, Path] = {}
    for path in out:
        key = path.parent.name
        if key not in newest or path.stat().st_mtime > newest[key].stat().st_mtime:
            newest[key] = path
    return sorted(newest.values(), key=lambda p: p.stat().st_mtime, reverse=True)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _safe(value: Any) -> Any:
    """Canonical JSON forbids floats; keep usage decimals as exact strings."""
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
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


def _session_meta(index_path: Path) -> dict[str, Any]:
    """Title/cwd from codebuddy-sessions.vscdb, when the IDE stored it."""
    db = Path(os.environ.get("CODEBUDDY_SESSIONS_DB")
              or (app_config_dir("CodeBuddy CN") / "codebuddy-sessions.vscdb")).expanduser()
    if not db.is_file():
        return {}
    conversation = index_path.parent.name
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        row = con.execute("select value from ItemTable where key=?",
                          (f"session:{conversation}",)).fetchone()
        con.close()
    except sqlite3.Error:
        return {}
    data = _json(row[0]) if row else None
    return data if isinstance(data, dict) else {}


def _user_text(raw: str) -> tuple[str, str]:
    """Return (real user text, workspace) from an IDE user message."""
    workspace = ""
    match = _WORKSPACE.search(raw)
    if match:
        workspace = match.group(1).strip()
    query = _QUERY.search(raw)
    if query:
        return query.group(1).strip(), workspace
    text = _CONTEXT.sub("", raw).strip()
    return text, workspace


def _tool_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False) if result is not None else ""
    inner = result.get("result")
    if isinstance(inner, dict):
        for key in ("stdout", "listing", "text", "output"):
            if isinstance(inner.get(key), str):
                return inner[key]
        if "files" in inner:
            return json.dumps(inner, ensure_ascii=False)
        return json.dumps(inner, ensure_ascii=False)
    if isinstance(inner, str):
        return inner
    return json.dumps(result, ensure_ascii=False)


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def ingest(index_path: Path, store: Any, *, key: Any | None = None,
           stream: str | None = None, now: float | None = None,
           since_ms: int | None = None) -> dict[str, Any]:
    from . import attachments as attachments_mod
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    conversation = index_path.parent.name
    index = _json(index_path.read_text(encoding="utf-8"))
    entries = index.get("messages") if isinstance(index, dict) else None
    if not isinstance(entries, list):
        return {"session_id": conversation, "skipped": "empty"}
    entries = [e for e in entries if isinstance(e, dict) and isinstance(e.get("id"), str)]
    if not entries:
        return {"session_id": conversation, "skipped": "empty"}

    stream = stream or f"local-codebuddy-{_bare(conversation)}"
    folder, manifest = _session_state(store, conversation)
    state = store.session_capture(conversation)
    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
        store.set_session_capture(conversation, host_seq=len(entries))
        return {"session_id": conversation, "skipped": "legacy_watermark"}

    watermark = int(state.get("host_seq", -1))
    selected = entries[max(0, watermark):]
    meta = _session_meta(index_path)
    requests = {}
    for request in (index.get("requests") or []) if isinstance(index, dict) else []:
        if isinstance(request, dict):
            requests[str(request.get("id") or "")] = request

    messages_dir = index_path.parent / "messages"
    selected_rows: list[dict[str, Any]] = []
    for entry in selected:
        path = messages_dir / f"{entry['id']}.json"
        file = _json(path.read_text(encoding="utf-8")) if path.is_file() else None
        if not isinstance(file, dict):
            continue
        inner = _json(file.get("message"))
        if not isinstance(inner, dict):
            continue
        selected_rows.append({"entry": entry, "file": file, "inner": inner})
    if since_ms is not None and folder is None and not selected_rows:
        return {"session_id": conversation, "skipped": "before_baseline"}

    created = _ms(selected_rows[0]["file"].get("createdAt")) if selected_rows else 0
    first_user = ""
    for row in selected_rows:
        if str(row["inner"].get("role") or "") == "user":
            text, workspace = _user_text(str((row["inner"].get("content") or [{}])[0].get("text") or ""))
            first_user = text
            if workspace and not meta.get("cwd"):
                meta["cwd"] = workspace
            break
    title = str(meta.get("title") or first_user or conversation).strip()[:120]
    cwd = str(meta.get("cwd") or "").strip()

    key = key or _device_key()
    if folder is None:
        meta_payload = {"host": "codebuddy", "host_session_id": conversation,
                        "source": "host_import", "imported_from": str(index_path),
                        "capture_point": "host_import", "title": title}
        if cwd:
            meta_payload["project"] = cwd
        if since_ms is not None:
            meta_payload["capture_from_ms"] = since_ms
            meta_payload["head_omitted"] = bool(created and created < since_ms)
        fr = Provelog(stream, key=key, store=store, flush=False, float_text=True)
        fr.chain._clock = _clock_for(created or int(stamp * 1000))
        records = [fr.start(meta_payload)]
    else:
        fr = _resume_recorder(store, conversation, stream, key, manifest, state)
        if fr is None:
            return {"session_id": conversation, "skipped": "no_chain"}
        records = []

    def emit(action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
        rec = _emit(fr, action, payload, ms)
        records.append(rec)
        return rec

    turn = 0
    tool_calls = tool_results = 0
    pending_images: list[tuple[bytes, dict[str, Any]]] = []

    for row in selected_rows:
        inner, file = row["inner"], row["file"]
        role = str(inner.get("role") or file.get("role") or "")
        ms = _ms(file.get("createdAt")) or created
        extra = _json(file.get("extra")) if isinstance(file.get("extra"), str) else file.get("extra")
        extra = extra if isinstance(extra, dict) else {}
        request = requests.get(str(extra.get("requestId") or "")) or {}
        usage = _safe(request.get("usage")) if isinstance(request.get("usage"), dict) else None
        model = str(extra.get("modelName") or extra.get("modelId") or meta.get("model") or "")
        blocks = inner.get("content")
        if isinstance(blocks, str):
            blocks = [{"type": "text", "text": blocks}]
        if not isinstance(blocks, list):
            blocks = []

        if role == "user":
            turn += 1
            texts = []
            workspace = ""
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "text":
                    continue
                text, ws = _user_text(str(block.get("text") or ""))
                if text:
                    texts.append(text)
                workspace = workspace or ws
            text = "\n".join(texts).strip()
            images = attachments_mod.inline_images(blocks, prefix="user-image")
            refs = attachments_mod.image_refs(blocks, prefix="user-image")
            if text or images or refs:
                content = [{"type": "text", "text": text}] if text else []
                for block, data, _name in images:
                    pending_images.append((data, attachments_mod.entry_for(block["attachment"])))
                    content.append(block)
                content.extend(refs)
                emit("user_message", {
                    "content": content,
                    "id": str(file.get("id") or ""), "origin": "user", "turn": turn,
                }, ms)
            continue

        if role == "tool":
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                result = block.get("result") if block.get("type") == "tool-result" else None
                call_id = str(block.get("toolCallId") or "") if isinstance(block, dict) else ""
                if result is None:
                    continue
                text = _tool_text(result)
                is_error = bool(result.get("isError")) if isinstance(result, dict) else False
                execution = {}
                if isinstance(result, dict):
                    inner_result = result.get("result")
                    if isinstance(inner_result, dict) and isinstance(inner_result.get("exitCode"), int):
                        execution = {"status": "succeeded" if inner_result["exitCode"] == 0 else "failed",
                                     "exit_code": inner_result["exitCode"]}
                result_images = attachments_mod.inline_images(result, prefix="tool-image")
                result_content = [{"type": "text", "text": text}] if text else []
                for image_block, data, _name in result_images:
                    pending_images.append((data, attachments_mod.entry_for(image_block["attachment"])))
                    result_content.append(image_block)
                result_content.extend(attachments_mod.image_refs(result, prefix="tool-image"))
                payload = {"call_id": call_id,
                           "content": result_content,
                           "is_error": is_error, "tool": str(block.get("toolName") or "")}
                if execution:
                    payload["execution"] = execution
                if turn:
                    payload["turn"] = turn
                emit("tool_result", payload, ms)
                tool_results += 1
            continue

        if role != "assistant":
            continue

        text_blocks: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "reasoning":
                text = str(block.get("text") or "")
                if text:
                    text_blocks.append({"type": "thinking", "text": text})
            elif kind == "text":
                text = str(block.get("text") or "")
                if text:
                    text_blocks.append({"type": "text", "text": text})
            elif kind == "tool-call":
                calls.append(block)
        if text_blocks:
            body: dict[str, Any] = {"id": str(file.get("id") or ""),
                                    "content": text_blocks, "turn": turn}
            if model:
                body["model"] = model
                body["provider"] = "codebuddy"
            if usage:
                body["usage"] = usage
            emit("assistant_message", body, ms)
            if usage and model:
                emit("model_call", {
                    "model": model, "provider": "codebuddy", "usage": usage,
                    "derived_from": "assistant_message",
                    "message_id": str(file.get("id") or ""),
                    **({"turn": turn} if turn else {}),
                }, ms)
        for call in calls:
            call_id = str(call.get("toolCallId") or "")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            body = {"tool": str(call.get("toolName") or ""), "call_id": call_id}
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
        store.rebuild_md(conversation)

    if title:
        try:
            store.update_title(conversation, title)
        except (AttributeError, OSError):
            pass
    capture = {
        "host_seq": len(entries),
        "source_path": str(index_path),
        "source_mtime": index_path.stat().st_mtime,
        "source_size": index_path.stat().st_size,
        "chain_id": fr.chain.session_id,
        "capture_point": "host_import",
        "verification_level": "signed_local",
    }
    if committed:
        capture["commit_at"] = stamp
        capture["commit_count"] = int(fr.chain._seq)
    store.set_session_capture(conversation, **capture)
    return {"session_id": conversation, "stream": stream, "added": len(records),
            "tool_calls": tool_calls, "tool_results": tool_results,
            "committed": committed}


def watch_once(store: Any, now: float | None = None, key: Any | None = None,
               since_ms: int | None = None) -> list[dict[str, Any]]:
    import time as _time
    stamp = now if now is not None else _time.time()
    out: list[dict[str, Any]] = []
    for index_path in conversation_indexes():
        conversation = index_path.parent.name
        try:
            stat = index_path.stat()
            state = store.session_capture(conversation)
            if (since_ms is not None and not state
                    and stat.st_mtime * 1000 < since_ms):
                continue
            if (state.get("source_mtime") == stat.st_mtime
                    and state.get("source_size") == stat.st_size
                    and state.get("commit_count") is not None):
                continue
            out.append(ingest(index_path, store, key=key, now=stamp,
                              since_ms=since_ms))
        except Exception as exc:
            out.append({"session_id": conversation,
                        "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out
