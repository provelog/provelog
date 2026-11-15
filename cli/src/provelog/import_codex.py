"""Import Codex CLI rollout JSONL.

Codex writes one append-only JSONL per thread under
``<CODEX_HOME or ~/.codex>/sessions/YYYY/MM/DD/rollout-*.jsonl`` (and old
threads under ``archived_sessions/``).  The first line is ``session_meta``;
``response_item`` lines are Responses-API shaped (message / reasoning /
function_call / function_call_output), and ``turn_context`` carries the model.
Only real user/assistant messages and tool calls are conversation; internal
environment/developer messages are skipped.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .chatview.model import usage_dict
from .codex_context import is_internal_user_text
from .import_dsh import _anchor_after_pass, _bare, _clock_for, _resume_recorder, _session_state
from .textlines import read_jsonl_lines

COMMIT_EVERY_RECORDS = 300
COMMIT_EVERY_S = 900


def codex_root() -> Path:
    env = os.environ.get("CODEX_HOME")
    return Path(env).expanduser() if env else Path.home() / ".codex"


def host_titles() -> dict[str, str]:
    """Thread titles the Codex app/CLI keeps in ``state_5.sqlite``.

    A user rename lives in ``threads.name``; ``threads.title`` is the automatic
    title (usually the first message). Neither is written into the rollout
    JSONL, so without this lookup a rename never reaches the viewer and an
    imported conversation keeps showing the prompt that opened it.

    Read-only and best-effort: an older Codex without the table, a locked or
    still-migrating database, or a renamed column all fall back to the
    rollout-derived title.
    """
    db = codex_root() / "state_5.sqlite"
    if not db.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
        try:
            rows = con.execute("SELECT id, name, title FROM threads").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    out: dict[str, str] = {}
    for sid, name, title in rows:
        label = str(name or "").strip() or str(title or "").strip()
        if sid and label:
            out[str(sid)] = label[:120]
    return out


def _refresh_titles(store: Any, titles: dict[str, str]) -> None:
    """Push a changed host title into an already-imported session.

    The rollout file does not change when the user renames a thread, so the
    normal ``mtime/size`` skip would keep the old display title forever.
    """
    for sid, title in titles.items():
        try:
            store.update_title(sid, title)
        except (AttributeError, OSError):
            continue


def session_files(root: Path | None = None) -> list[Path]:
    base = root or codex_root()
    from .chatview.readers import is_receipt_file
    out = []
    for sub in ("sessions", "archived_sessions"):
        folder = base / sub
        if not folder.is_dir():
            continue
        out.extend(p for p in folder.rglob("rollout-*.jsonl")
                   if p.is_file() and not is_receipt_file(p))
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
        parts = []
        for block in value:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts).strip()
    return ""


def _output(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(b.get("text") or "") for b in value
                         if isinstance(b, dict) and b.get("text"))
    if isinstance(value, dict):
        for key in ("output", "text", "content", "message"):
            if isinstance(value.get(key), str):
                return value[key]
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def _usage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    usage = usage_dict({
        "input": value.get("input_tokens"),
        "output": value.get("output_tokens"),
        "cache_read": value.get("cached_input_tokens"),
        "cache_write": value.get("cache_write_input_tokens"),
        "total": value.get("total_tokens"),
    })
    return _safe(usage) if usage else None


def _safe(value: Any) -> Any:
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    return value


def _error_flag(payload: dict[str, Any]) -> bool | None:
    """Read an explicit failure flag; do not invent success when it is absent."""
    for key in ("is_error", "isError", "error", "failed", "failure"):
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value:
            return True
        if isinstance(value, dict) and value:
            return True
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status.lower() in ("error", "failed", "failure", "cancelled", "canceled")
    output = payload.get("output")
    if isinstance(output, dict):
        for key in ("is_error", "isError", "error"):
            if isinstance(output.get(key), bool):
                return bool(output[key])
    return None


def _emit(fr: Any, action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
    fr.chain._clock = _clock_for(ms or int(time.time() * 1000))
    return fr.record(action, payload)


def _source_key(path: Path) -> str:
    """Identity of one rollout file, not just of its session.

    Codex writes one session as several rollout files (the main log plus
    continuation/subagent segments). A watermark keyed only by session id is
    shared by all of them, so one file resets the other's position and the
    watcher re-imports the same rows forever. Keep a position per file.
    """
    return str(path)


def _source_entry(state: dict[str, Any], path: Path) -> dict[str, Any] | None:
    """The per-file watermark, migrating the old single-file state."""
    offsets = state.get("source_offsets")
    if isinstance(offsets, dict):
        entry = offsets.get(_source_key(path))
        return entry if isinstance(entry, dict) else None
                                                                         
    if str(state.get("source_path") or "") == _source_key(path):
        return {
            "seq": int(state.get("host_seq", -1)),
            "mtime": state.get("source_mtime"),
            "size": state.get("source_size"),
        }
    return None


def ingest(path: Path, store: Any, *, key: Any | None = None,
           stream: str | None = None, now: float | None = None,
           since_ms: int | None = None,
           host_title: str | None = None) -> dict[str, Any]:
    from . import attachments as attachments_mod
    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else time.time()
    rows, consumed = _read(path)
    if not rows:
        return {"session_id": path.stem, "skipped": "empty"}
    meta = next((r.get("payload") for r in rows
                 if r.get("type") == "session_meta" and isinstance(r.get("payload"), dict)), {})
    session_id = str(meta.get("id") or meta.get("session_id") or path.stem)
    from .session_kind import codex_kind
    role, parent = codex_kind(meta)
    if role == "internal":
        return {"session_id": session_id, "skipped": "internal_session"}
    if role == "subagent" and (not parent or store.locate(parent) is None):
        return {"session_id": session_id, "skipped": "parent_unavailable"}
    stream = stream or f"local-codex-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)
    source_key = _source_key(path)
                                                                             
                                                                            
                                                                            
                                                                
    legacy_multi = bool(state.get("legacy_multi"))
    if (not state.get("source_offsets") and state.get("source_path")
            and folder is not None and int(manifest.get("records") or 0) > 0):
        legacy_multi = True
    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
                                                                           
                                                                            
        try:
            st = path.stat()
            offsets = {source_key: {"seq": consumed, "mtime": st.st_mtime,
                                    "size": st.st_size}}
        except OSError:
            offsets = {}
        store.set_session_capture(
            session_id, host_seq=consumed, source_path=source_key,
            source_mtime=state.get("source_mtime"), source_size=state.get("source_size"),
            source_offsets=offsets, legacy_multi=True)
        return {"session_id": session_id, "skipped": "legacy_watermark"}

    entry = _source_entry(state, path)
    if entry is not None:
        watermark = int(entry.get("seq", -1))
    elif isinstance(state.get("source_offsets"), dict):
                                                                         
                                                                            
                                                                              
                                            
        watermark = len(rows) if legacy_multi else 0
    elif str(state.get("source_path") or "") == source_key:
        watermark = int(state.get("host_seq", -1))
    elif folder is not None and int(manifest.get("records") or 0) > 0:
                                                                            
                                                                           
                                                                           
        watermark = len(rows)
    else:
        watermark = -1
    selected = [(i, r) for i, r in enumerate(rows) if i >= watermark]
    if since_ms is not None and folder is None and not selected:
        return {"session_id": session_id, "skipped": "before_baseline"}

                                                                          
                                                                          
                               
    usage_by_assistant: dict[str, dict[str, Any]] = {}
    scan_assistant = ""
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if (row.get("type") == "response_item"
                and payload.get("type") == "message"
                and payload.get("role") == "assistant"):
            scan_assistant = str(payload.get("id") or "")
        elif (row.get("type") == "event_msg"
              and payload.get("type") == "token_count" and scan_assistant):
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            usage = _usage(info.get("last_token_usage"))
            if usage:
                usage_by_assistant[scan_assistant] = usage

    cwd = str(meta.get("cwd") or "")
    version = str(meta.get("cli_version") or "")
    originator = str(meta.get("originator") or "").lower()
    if "vscode" in originator or "vs-code" in originator:
        surface = "vscode"
    elif "app" in originator or "desktop" in originator:
        surface = "app"
    elif "cli" in originator:
        surface = "cli"
    else:
        surface = ""
    provider = str(meta.get("model_provider") or "openai")
    created = _ms(meta.get("timestamp"))
    title = ""
    for row in rows:
        if row.get("type") != "response_item":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if payload.get("type") != "message" or payload.get("role") != "user":
            continue
        text = _text(payload.get("content"))
        if text and not is_internal_user_text(text):
            title = text.splitlines()[0][:120]
            break
                                                                         
                                                                        
    if host_title is None:
        host_title = host_titles().get(session_id)
                                                                         
                                                        
    title = host_title or title or str(
        manifest.get("display_title") or manifest.get("title") or "")
    title = title or session_id

    key = key or _device_key()
    if folder is None:
        meta_payload = {"host": "codex", "host_session_id": session_id,
                        "source": "host_import", "imported_from": str(path),
                        "capture_point": "host_import", "title": title}
        if role == "subagent":
            meta_payload.update(parent_session=parent, session_role=role)
        if cwd:
            meta_payload["project"] = cwd
        if version:
            meta_payload["host_version"] = version
        if surface:
            meta_payload["surface"] = surface
        if since_ms is not None:
            meta_payload["capture_from_ms"] = since_ms
            meta_payload["head_omitted"] = bool(created and created < since_ms)
        fr = Provelog(stream, key=key, store=store, flush=False, float_text=True)
        fr.chain._clock = _clock_for(created or int(stamp * 1000))
        records = [fr.start(meta_payload)]
    else:
        fr = _resume_recorder(store, session_id, stream, key, manifest, state)
        if fr is None:
            return {"session_id": session_id, "skipped": "no_chain"}
        records = []

    def emit(action: str, payload: dict[str, Any], ms: int) -> dict[str, Any]:
        rec = _emit(fr, action, payload, ms)
        records.append(rec)
        return rec

                                                                           
                                                                         
    turn = int(state.get("last_turn") or 0)
    model = ""
    held_thinking = ""
    tool_calls = tool_results = 0
    pending_images: list[tuple[bytes, dict[str, Any]]] = []

    for _, row in selected:
        kind = row.get("type")
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        ms = _ms(row.get("timestamp"))
        if kind == "turn_context":
            if isinstance(payload.get("model"), str) and payload["model"]:
                model = payload["model"]
            if not cwd and isinstance(payload.get("cwd"), str):
                cwd = payload["cwd"]
            continue
        if kind == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            usage = _usage(info.get("last_token_usage"))
            continue
        if kind != "response_item":
            continue
        ptype = payload.get("type")
        if ptype == "message":
            role = payload.get("role")
            if role == "user":
                text = _text(payload.get("content"))
                images = attachments_mod.inline_images(payload.get("content"),
                                                       prefix="user-image")
                refs = attachments_mod.image_refs(payload.get("content"), prefix="user-image")
                if (not text and not images and not refs) or is_internal_user_text(text):
                    continue
                turn += 1
                content = [{"type": "text", "text": text}] if text else []
                for block, data, _name in images:
                    pending_images.append((data, attachments_mod.entry_for(block["attachment"])))
                    content.append(block)
                content.extend(refs)
                emit("user_message", {
                    "content": content,
                    "id": str(payload.get("id") or f"{session_id}-u-{turn}"),
                    "origin": "user", "turn": turn,
                }, ms)
            elif role == "assistant":
                text = _text(payload.get("content"))
                if not text:
                    continue
                body = {"id": str(payload.get("id") or ""),
                        "content": [{"type": "text", "text": text}],
                        "turn": turn}
                if held_thinking:
                    body["content"].insert(0, {"type": "thinking", "text": held_thinking})
                    held_thinking = ""
                if model:
                    body["model"] = model
                    body["provider"] = provider
                usage = usage_by_assistant.get(str(payload.get("id") or ""))
                if usage:
                    body["usage"] = usage
                emit("assistant_message", body, ms)
        elif ptype == "reasoning":
            parts = [str(s.get("text") or "") for s in payload.get("summary") or []
                     if isinstance(s, dict) and s.get("text")]
            if parts:
                held_thinking = "\n".join(parts)
        elif ptype in ("function_call", "custom_tool_call"):
            name = str(payload.get("name") or "")
            call_id = str(payload.get("call_id") or "")
            raw = payload.get("arguments")
            if isinstance(raw, str):
                try:
                    args = json.loads(raw)
                except ValueError:
                    args = {"_raw": raw}
            elif isinstance(raw, dict):
                args = raw
            else:
                args = {}
            body = {"tool": name, "call_id": call_id}
            body.update(fr._content("args", args if isinstance(args, dict) else {}))
            if turn:
                body["turn"] = turn
            emit("tool_call", body, ms)
            tool_calls += 1
        elif ptype in ("function_call_output", "custom_tool_call_output"):
            call_id = str(payload.get("call_id") or "")
            output = payload.get("output")
            result_content = [{"type": "text", "text": _output(output)}]
            result_images = attachments_mod.inline_images(output, prefix="tool-image")
            for image_block, data, _name in result_images:
                pending_images.append((data, attachments_mod.entry_for(image_block["attachment"])))
                result_content.append(image_block)
            body = {"call_id": call_id, "content": result_content}
            error = _error_flag(payload)
            if error is not None:
                body["is_error"] = error
            if turn:
                body["turn"] = turn
            emit("tool_result", body, ms)
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
        attachments_mod.save_pending(store, session_id, store.locate(session_id),
                                     pending_images)
        store.rebuild_md(session_id)

    if title:
        try:
            store.update_title(session_id, title)
        except (AttributeError, OSError):
            pass
    try:
        st = path.stat()
        file_entry = {"seq": consumed, "mtime": st.st_mtime, "size": st.st_size}
    except OSError:
        file_entry = {"seq": consumed}
    offsets = state.get("source_offsets")
    offsets = dict(offsets) if isinstance(offsets, dict) else {}
    offsets[source_key] = file_entry
    capture = {
        "host_seq": consumed,
        "source_path": source_key,
        "source_mtime": file_entry.get("mtime"),
        "source_size": file_entry.get("size"),
        "source_offsets": offsets,
        "last_turn": turn,
        "legacy_multi": legacy_multi,
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
    titles = host_titles()
    from .session_kind import codex_kind, codex_meta
    paths = session_files()
    paths.sort(key=lambda p: codex_kind(codex_meta(p))[0] != "main")
    for path in paths:
        sid = _session_id(path)
        try:
            stat = path.stat()
            state = store.session_capture(sid)
            if (since_ms is not None and not state
                    and stat.st_mtime * 1000 < since_ms):
                continue
            entry = _source_entry(state, path)
            if (entry is not None
                    and entry.get("mtime") == stat.st_mtime
                    and entry.get("size") == stat.st_size
                    and state.get("commit_count") is not None):
                continue
            out.append(ingest(path, store, key=key, now=stamp,
                              since_ms=since_ms, host_title=titles.get(sid)))
        except Exception as exc:
            out.append({"session_id": sid,
                        "error": f"{type(exc).__name__}: {exc}"})
    _refresh_titles(store, titles)
    _anchor_after_pass(store, out)
    return out


def _session_id(path: Path) -> str:
                                                                          
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                if isinstance(row, dict) and row.get("type") == "session_meta":
                    payload = row.get("payload")
                    if isinstance(payload, dict):
                        return str(payload.get("id") or payload.get("session_id") or path.stem)
                break
    except (OSError, ValueError):
        pass
    return path.stem
