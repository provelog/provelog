"""Import the consumer Doubao desktop app's cached conversations.

Doubao (the chat app, not Doubao Work) is a Chromium wrapper.  It does not
write a JSONL transcript; the IM state is stored in IndexedDB:

    ~/Library/Application Support/Doubao/Default/IndexedDB/
      chrome_doubao-chat_0.indexeddb.leveldb/000004.log

The values are V8 structured-clone blobs inside LevelDB write batches.  This
reader parses the LevelDB log, extracts the ``mainTaskDataMap`` IM state, and
maps the user/assistant/tool content blocks onto ProveLog's trajectory records.

This is deliberately a bounded reader: it reads the local cache the app
already produced.  It never calls Doubao's servers and never modifies the app.
"""

from __future__ import annotations

import os
import struct
import time
from pathlib import Path
from typing import Any

from .import_dsh import _bare, _clock_for, _resume_recorder, _session_state
from .platform_paths import app_config_dir

try:  # pragma: no cover - import failure is reported as an empty reader
    from v8serialize import loads as v8_loads
except ImportError:  # pragma: no cover
    v8_loads = None

BLOCK_SIZE = 32 * 1024
TEXT_BLOCK = 10000
THINKING_BLOCK = 10040
FILE_OPERATION_BLOCK = 10019


def doubao_app_db() -> Path | None:
    env = os.environ.get("DOUBAO_APP_HOME")
    base = Path(env).expanduser() if env else app_config_dir("Doubao")
    db = base / "Default" / "IndexedDB" / "chrome_doubao-chat_0.indexeddb.leveldb"
    return db if db.is_dir() else None


def _logical_records(data: bytes) -> list[bytes]:
    """Reassemble LevelDB log records (including fragmented records)."""
    out: list[bytes] = []
    pos = 0
    current: bytearray | None = None
    while pos + 7 <= len(data):
        block_offset = pos % BLOCK_SIZE
        if block_offset + 7 > BLOCK_SIZE:
            pos += BLOCK_SIZE - block_offset
            continue
        _crc, length, kind = struct.unpack_from("<IHB", data, pos)
        pos += 7
        if kind == 0 or pos + length > len(data):
            continue
        fragment = data[pos:pos + length]
        pos += length
        if kind == 1:
            out.append(fragment)
        elif kind == 2:
            current = bytearray(fragment)
        elif kind == 3 and current is not None:
            current.extend(fragment)
        elif kind == 4 and current is not None:
            current.extend(fragment)
            out.append(bytes(current))
            current = None
    return out


def _varint(data: bytes, index: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while index < len(data):
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index
        shift += 7
    raise ValueError("truncated varint")


def _write_batch_values(record: bytes) -> list[bytes]:
    """Values from one LevelDB WriteBatch."""
    if len(record) < 12:
        return []
    _sequence, count = struct.unpack_from("<QI", record, 0)
    index = 12
    values: list[bytes] = []
    for _ in range(count):
        if index >= len(record):
            break
        tag = record[index]
        index += 1
        key_len, index = _varint(record, index)
        index += key_len
        if tag != 1:
            continue
        value_len, index = _varint(record, index)
        values.append(record[index:index + value_len])
        index += value_len
    return values


def _decode(value: bytes) -> Any | None:
    if v8_loads is None:
        return None
    for index in range(min(len(value), 64)):
        if value[index] == 0xFF and index + 1 < len(value) and value[index + 1] == 0x0F:
            try:
                return v8_loads(value[index:])
            except Exception:
                return None
    return None


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    try:
        if hasattr(obj, "get"):
            return obj.get(key, default)
        return getattr(obj, key, default)
    except Exception:
        return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "values"):
        try:
            return list(value.values())
        except Exception:
            pass
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _blocks(message: Any) -> list[Any]:
    extra = _get(message, "extra") or {}
    blocks = _get(extra, "content_blocks_v2") or _get(extra, "content_blocks") or []
    return _as_list(blocks)


def _text_block(block: Any) -> str:
    content = _get(block, "content") or {}
    text = _get(content, "text_block") or {}
    return str(_get(text, "text") or "").strip()


def _image_urls(value: Any) -> list[str]:
    """Collect generated-image URLs without fetching them.

    Doubao stores generated images in JSObject values (V8 structured clone),
    not plain dicts. Use ``_get`` for both object shapes.
    """
    out: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            if value not in out:
                out.append(value)

    def walk(item: Any) -> None:
        is_object = isinstance(item, dict) or type(item).__name__.endswith("JSObject")
        if type(item).__name__.endswith("JSArray"):
            for index in range(len(item)):
                walk(item[index])
            return
        if isinstance(item, list) or (hasattr(item, "__iter__") and not is_object
                                      and not isinstance(item, (str, bytes))):
            for child in item:
                walk(child)
            return
        if not isinstance(item, dict) and not hasattr(item, "get"):
            return
        for key in ("image_ori", "image_preview", "image_thumb", "thumb_url",
                    "full_size_url", "uri"):
            child = _get(item, key)
            if isinstance(child, dict) or hasattr(child, "get"):
                add(_get(child, "url"))
            elif isinstance(child, str):
                add(child)
        for key in ("images", "media", "image", "content",
                    "gen_image_block", "rich_media_layout_block"):
            child = _get(item, key)
            if isinstance(child, (list, dict)) or hasattr(child, "get"):
                walk(child)

    walk(value)
    return out

def _image_blocks(blocks: list[Any]) -> list[dict[str, Any]]:
    urls = _image_urls(blocks)
    return [{"type": "image", "url": url, "name": f"doubao-image-{i}"}
            for i, url in enumerate(urls, 1)]


def _message_ts(message: Any) -> int:
    extra = _get(message, "extra") or {}
    value = _get(extra, "create_time") or _get(message, "createTime") or 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number * 1000 if number < 10_000_000_000 else number


def _collect_messages(db: Path) -> dict[str, dict[str, Any]]:
    """conversation_id -> {title, messages: [...]} from cached IM state."""
    conversations: dict[str, dict[str, Any]] = {}
    messages: dict[tuple[str, str], dict[str, Any]] = {}
    logs = sorted(db.glob("*.log"))
    for log in logs:
        try:
            raw = log.read_bytes()
        except OSError:
            continue
        for record in _logical_records(raw):
            for value in _write_batch_values(record):
                state = _decode(value)
                if state is None:
                    continue
                tasks = _get(_get(state, "state") or {}, "mainTaskDataMap") or {}
                for session in _as_list(tasks):
                    if session is None:
                        continue
                    local_to_real: dict[str, str] = {}
                    for kind in ("sentMessages", "receivedMessages"):
                        for message in _as_list(_get(session, kind) or {}):
                            if message is None:
                                continue
                            cid = str(_get(message, "conversationId") or "")
                            if cid and not cid.startswith("local_"):
                                local_to_real[cid] = cid
                    for kind in ("sentMessages", "receivedMessages"):
                        for message in _as_list(_get(session, kind) or {}):
                            if message is None:
                                continue
                            cid = str(_get(message, "conversationId") or "")
                            if cid.startswith("local_"):
                                real = next(iter(local_to_real.values()), "")
                                if real:
                                    cid = real
                            if not cid:
                                continue
                            extra = _get(message, "extra") or {}
                            uid = str(_get(message, "uniqueId")
                                      or _get(extra, "local_message_id")
                                      or _get(extra, "message_id") or "")
                            if not uid:
                                continue
                            blocks = _blocks(message)
                            key = (cid, uid)
                            old = messages.get(key)
                            if old is None or len(blocks) >= len(old["blocks"]):
                                messages[key] = {
                                    "cid": cid,
                                    "uid": uid,
                                    "kind": kind,
                                    "ts": _message_ts(message),
                                    "blocks": blocks,
                                }
    for message in messages.values():
        cid = message["cid"]
        conv = conversations.setdefault(cid, {"title": "", "messages": []})
        conv["messages"].append(message)
        for block in message["blocks"]:
            text = _text_block(block)
            if text and not conv["title"] and message["kind"] == "sentMessages":
                conv["title"] = text.splitlines()[0][:120]
    for conv in conversations.values():
        conv["messages"].sort(key=lambda m: (m["ts"], 0 if m["kind"] == "sentMessages" else 1))
                                                                            
                                                                             
                                                                      
    for local_id in [cid for cid in conversations if cid.startswith("local_")]:
        local = conversations[local_id]
        first = next((m for m in local["messages"]
                      if m["kind"] == "sentMessages"), None)
        if first is None:
            continue
        first_text = "\n".join(filter(None, (_text_block(b)
                                           for b in first["blocks"]))).strip()
        for real_id, real in conversations.items():
            if real_id.startswith("local_") or real_id == local_id:
                continue
            if real["title"] != first_text:
                continue
            existing = {(m["kind"], m["uid"]) for m in real["messages"]}
            real["messages"].extend(m for m in local["messages"]
                                    if (m["kind"], m["uid"]) not in existing)
            real["messages"].sort(key=lambda m: (m["ts"], 0 if m["kind"] == "sentMessages" else 1))
            del conversations[local_id]
            break
    return conversations


def conversation_count() -> int:
    db = doubao_app_db()
    if db is None or v8_loads is None:
        return 0
    try:
        return len(_collect_messages(db))
    except OSError:
        return 0


def _source_stat(db: Path) -> tuple[float, int]:
    mtime = 0.0
    size = 0
    for path in db.glob("*.log"):
        try:
            stat = path.stat()
        except OSError:
            continue
        mtime = max(mtime, stat.st_mtime)
        size += stat.st_size
    return mtime, size


def _tool_from_block(block: Any) -> tuple[str, dict[str, Any], str, bool] | None:
    content = _get(block, "content") or {}
    operation = _get(content, "file_operation_block") or {}
    if not operation:
        return None
    display = _get(operation, "display_content") or {}
    command = str(_get(display, "operation") or "")
    result = str(_get(display, "result") or "")
    name = str(_get(operation, "file_type") or "shell")
    return name, {"command": command}, result, result.lower().startswith("error")


def ingest(store: Any, cid: str, conv: dict[str, Any], *,
           key: Any | None = None, stream: str | None = None,
           now: float | None = None, since_ms: int | None = None) -> dict[str, Any]:
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    all_messages = conv["messages"]
    session_id = cid
    stream = stream or f"local-doubao-app-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)
    if state and folder is None:
        state = {}
    last_ms = int(state.get("last_ms", -1))
    selected = [message for message in all_messages if message["ts"] > last_ms]
    if not selected:
        return {"session_id": session_id, "skipped": "unchanged"}

    key = key or _device_key()
    if folder is None:
        meta = {"host": "doubao", "host_session_id": session_id,
                "source": "host_import", "imported_from": str(doubao_app_db() or ""),
                "capture_point": "host_import"}
        if conv["title"]:
            meta["title"] = conv["title"]
        fr = Provelog(stream, key=key, store=store, flush=False, float_text=True)
        fr.chain._clock = _clock_for(selected[0]["ts"] or int(stamp * 1000))
        records = [fr.start(meta)]
    else:
        fr = _resume_recorder(store, session_id, stream, key, manifest, state)
        if fr is None:
            return {"session_id": session_id, "skipped": "no_chain"}
        records = []

    def emit(action: str, payload: dict[str, Any], ms: int) -> None:
        fr.chain._clock = _clock_for(ms or int(stamp * 1000))
        records.append(fr.record(action, payload))

    turn = int(state.get("last_turn", 0))
    tool_calls = tool_results = 0
    for message in selected:
        ms = message["ts"] or int(stamp * 1000)
        if message["kind"] == "sentMessages":
            text = "\n".join(filter(None, (_text_block(block) for block in message["blocks"]))).strip()
            image_blocks = _image_blocks(message["blocks"])
            if text or image_blocks:
                turn += 1
                content = [{"type": "text", "text": text}] if text else []
                content.extend(image_blocks)
                emit("user_message", {
                    "content": content,
                    "id": f"doubao-{session_id}-u-{turn}",
                    "origin": "user", "turn": turn,
                }, ms)
            continue
        text_parts: list[str] = []
        for block in message["blocks"]:
            text = _text_block(block)
            if text:
                text_parts.append(text)
        image_blocks = _image_blocks(message["blocks"])
        if text_parts or image_blocks:
            content = [{"type": "text", "text": "\n".join(text_parts)}] if text_parts else []
            content.extend(image_blocks)
            emit("assistant_message", {
                "id": f"doubao-{session_id}-a-{message['uid']}",
                "content": content,
                "turn": turn or 1,
            }, ms)
        for block in message["blocks"]:
            tool = _tool_from_block(block)
            if tool is None:
                continue
            name, args, result, is_error = tool
            call_id = str(_get(block, "block_id") or f"{session_id}-tool-{tool_calls}")
            body = {"tool": name, "call_id": call_id, "turn": turn or 1}
            body.update(fr._content("args", args))
            emit("tool_call", body, ms)
            tool_calls += 1
            emit("tool_result", {
                "call_id": call_id, "tool": name,
                "content": [{"type": "text", "text": result}],
                "is_error": is_error, "turn": turn or 1,
            }, ms)
            tool_results += 1

    if records:
        store.append_records(stream, records, defer_index=False)
        store.rebuild_md(session_id)

    if conv["title"]:
        try:
            store.update_title(session_id, conv["title"])
        except (AttributeError, OSError):
            pass
    db = doubao_app_db()
    mtime, size = _source_stat(db) if db else (stamp, 0)
    capture = {
        "host_seq": len(all_messages),
        "source_path": str(db or ""),
        "source_mtime": mtime,
        "source_size": size,
        "last_ms": max((m["ts"] for m in selected), default=last_ms),
        "last_turn": turn,
        "chain_id": fr.chain.session_id,
        "capture_point": "host_import",
        "verification_level": "signed_local",
    }
    store.set_session_capture(session_id, **capture)
    return {"session_id": session_id, "stream": stream, "added": len(records),
            "tool_calls": tool_calls, "tool_results": tool_results, "committed": False}


def watch_once(store: Any, *, since_ms: int | None = None,
               now: float | None = None) -> list[dict[str, Any]]:
    db = doubao_app_db()
    if db is None or v8_loads is None:
        return []
    stamp = now if now is not None else time.time()
    try:
        conversations = _collect_messages(db)
    except OSError:
        return []
    out = []
    for cid, conv in conversations.items():
        try:
            out.append(ingest(store, cid, conv, now=stamp, since_ms=since_ms))
        except Exception as exc:
            out.append({"session_id": cid, "error": f"{type(exc).__name__}: {exc}"})
    return out
