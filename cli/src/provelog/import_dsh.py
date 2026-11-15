"""Backfill a DSH session from the harness's own log.

DSH writes ``$DSH_HOME/sessions/<workspace>/<session>/session.v4.jsonl.zstd``:
a header line plus canonical events, zstd-framed. The file is a complete record
of the conversation, so it can be imported into the evidence store directly.

Imported records are marked ``source: host_import`` and the folder remembers
``imported_from``, because a host-owned log is a weaker source than the wire
capture: it is a local file the host could have rewritten.
"""

from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path
from typing import Any

from . import attachments as attachments_mod
from . import capture as capture_mod
from .textlines import split_text_lines

LOG_NAME = "session.v4.jsonl.zstd"

                                                                                
                                                                               
                                                                                  
                                                                                 
                                      
COMMIT_EVERY_S = 15 * 60
COMMIT_EVERY_RECORDS = 300
TITLE_SYNC_VERSION = 3
TURN_SYNC_VERSION = 1


def _collect_attachments(events: list[Any]) -> list[dict[str, Any]]:
    """Every attachment block a session's events reference (deduped by hash)."""
    seen: dict[str, dict[str, Any]] = {}
    for event in events:
        data = event.get("data") or {}
        message = data.get("message") or {}
        blocks = []
        for value in (data.get("content"), message.get("content")):
            if isinstance(value, list):
                blocks.extend(value)
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            meta = block.get("attachment")
            if not isinstance(meta, dict) or not meta.get("attachmentId"):
                continue
            entry = attachments_mod.entry_for(meta)
            if entry is None:
                continue
            seen.setdefault(entry["file"], entry)
    return list(seen.values())


def _attachments_home(log_path: Path) -> Path:
    """The harness home a session log belongs to (``<home>/sessions/<ws>/<sid>/``)."""
    parts = Path(log_path).parents
    if len(parts) > 3 and parts[2].name == "sessions":
        return parts[3]
    return dsh_home()


def _store_attachments(store: Any, session_id: str, home: Path,
                       entries: list[dict[str, Any]]) -> int:
    """Copy the bytes into the session folder; return how many were added.

    The host's attachment store is a cache the user can clear, so the record
    keeps its own copy - the blob is what the hash in the record commits to.
    """
    folder = store.locate(session_id)
    if folder is None or not entries:
        return 0
    added = []
    for entry in entries:
        source = attachments_mod.dsh_object(home, entry["sha256"])
        if source is None:
            continue
        if attachments_mod.copy_into(Path(folder), source, entry):
            added.append(entry)
    if added and hasattr(store, "add_attachments"):
        store.add_attachments(session_id, added)
    return len(added)


def _bare(session_id: str) -> str:
    """The id without the harness prefix, so a stream name stays readable."""
    raw = str(session_id or "")
    for prefix in ("session-", "session_", "sess-", "sess_"):
        if raw.lower().startswith(prefix) and len(raw) > len(prefix) + 4:
            return raw[len(prefix):]
    return raw


def dsh_home(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    env = os.environ.get("DSH_HOME")
    return Path(env).expanduser() if env else Path.home() / ".dsh"


_SCRATCH_WORKSPACE = re.compile(r"^--(?:private-)?(?:tmp|var-folders)-")


def session_files(home: Path) -> list[Path]:
    """Every persisted DSH session log, newest first.

    Test/scratch workspaces are not user conversations. Importing them would
    put fixture rows at the top of the viewer and keep refreshing their
    ``last_activity`` on every watch pass.
    """
    files = [p for p in home.glob(f"sessions/*/*/{LOG_NAME}")
             if not _SCRATCH_WORKSPACE.match(p.parent.parent.name)]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def read_log(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(session header, events) from a framed zstd JSONL log."""
    try:
        import zstandard  # noqa: PLC0415 - optional at call time
    except Exception as exc:  # pragma: no cover - depends on env
        raise RuntimeError("importing a DSH log needs the zstandard package") from exc
    raw = path.read_bytes()
    out: list[bytes] = []
    rest = raw
    magic = b"\x28\xb5\x2f\xfd"
    while rest and rest.startswith(magic):
        obj = zstandard.ZstdDecompressor().decompressobj()
        try:
            out.append(obj.decompress(rest))
        except Exception:
                                                                              
                                                                           
            break
        rest = obj.unused_data
    header: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    for line in split_text_lines(b"".join(out)):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") == "session" and not header:
            header = row
            continue
        if isinstance(row.get("seq"), int):
            events.append(row)
    events.sort(key=lambda e: e["seq"])
    return header, events


                                                                                    

def _session_state(store: Any, session_id: str) -> tuple[Any, dict[str, Any]]:
    folder = store.locate(session_id)
    if folder is None:
        return None, {}
    try:
        return folder, json.loads((Path(folder) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return folder, {}


def _resume_recorder(store: Any, session_id: str, stream: str, key: Any,
                     manifest: dict[str, Any], state: dict[str, Any] | None = None):
    """Reopen ONE session's chain at its last record.

    The folder also holds the parent's records (and any subagent's), so the
    resume point must come from this chain only - taking the folder's last
    record resumed the parent at a child's seq and the store refused it.
    """
    from .chain import Chain
    from .sdk import Provelog

    chain_id = str((state or {}).get("chain_id")
                   or manifest.get("chain_id") or "")
    envelopes = store.session_envelopes(session_id)
    records = [env.get("record") or {} for env in envelopes]
    if chain_id:
        records = [r for r in records if str(r.get("session_id") or "") == chain_id]
    if not records:
        return None
    last = records[-1]
    hashes = [str(r.get("record_hash") or "") for r in records]
    chain = Chain.resume(stream, key, chain_id or str(last.get("session_id") or ""),
                         int(last.get("seq") or 0) + 1,
                         str(last.get("record_hash") or ""),
                         hashes)
    return Provelog(stream, key=key, store=store, flush=False, chain=chain,
                    float_text=True)


def _clock_for(ms: int):
    """A chain clock pinned to the host event's own time.

    Import happens later than the conversation did: stamping records with
    "now" would make a two-hour-old turn look like it was recorded today at
    import time. The event's timestamp is what the record must carry.
    """
    from datetime import UTC, datetime

    stamp = datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return lambda: stamp


def _event_ms(event: dict[str, Any], header: dict[str, Any]) -> int:
    for value in (event.get("time"), header.get("createdAt")):
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def ingest(path: Path, store: Any, *, key: Any | None = None,
           stream: str | None = None,
           now: float | None = None, since_ms: int | None = None
           ) -> dict[str, Any]:
    """Append the events a DSH session has gained since the last poll.

    The chain stays open between polls. Every ``COMMIT_EVERY_S`` seconds of activity
    (or ``COMMIT_EVERY_RECORDS`` new records) the batch ends with a commitment that
    fixes everything so far; a later poll appends after it and commits again. Nothing
    here waits for the conversation to end, because it never does.
    """
    import time as _time

    from .easy import _device_key
    from .sdk import Provelog

    stamp = now if now is not None else _time.time()
    header, events = read_log(path)
    facts = capture_mod.dsh_session_meta(events, header)
    turns = capture_mod.dsh_turns(events)
    session_id = facts["session_id"] or Path(path).parent.name
    stream = stream or f"local-dsh-{_bare(session_id)}"
    folder, manifest = _session_state(store, session_id)
    state = store.session_capture(session_id)
    if folder is not None and facts["title"] and (
            manifest.get("title") != facts["title"]
            or manifest.get("display_title") != facts["title"]):
                                                                            
                                                                               
                                                                            
                                                                          
        store.update_title(session_id, facts["title"])
        manifest["title"] = facts["title"]
        manifest["display_title"] = facts["title"]
    if folder is not None and turns and manifest.get("host_turns") != turns:
                                                                              
                                                    
        store.set_session_fields(session_id, host_turns=turns)
        manifest["host_turns"] = turns
    if not state and folder is not None and int(manifest.get("records") or 0) > 0:
                                                                         
        legacy_max = max((int(e.get("seq", -1)) for e in events), default=-1)
        store.set_session_capture(session_id, host_seq=legacy_max,
                                  title_sync=TITLE_SYNC_VERSION,
                                  turn_sync=TURN_SYNC_VERSION)
        return {"session_id": session_id, "skipped": "legacy_watermark",
                "host_seq": legacy_max}
    watermark = int(state.get("host_seq", -1))
    deleted_at = state.get("deleted_at_ms")
    eligible = [e for e in events
                if int(e.get("seq", -1)) > watermark
                and (since_ms is None or _event_ms(e, header) > since_ms)
                                                                                  
                                                               
                and (not deleted_at or _event_ms(e, header) > int(deleted_at))]
    if since_ms is not None and folder is None and not eligible:
        return {"session_id": session_id, "skipped": "before_baseline"}
    if folder is None and state and not eligible:
                                                                              
                                                                         
        store.set_session_capture(session_id, title_sync=TITLE_SYNC_VERSION,
                                  turn_sync=TURN_SYNC_VERSION)
        return {"session_id": session_id, "skipped": "deleted_no_new_events"}

    key = key or _device_key()
    if folder is None:
        meta = {"host": "dsh", "host_session_id": session_id,
                "source": "host_import", "imported_from": str(path),
                "capture_point": "host_import"}
        if since_ms is not None:
            first = min((int(e.get("seq", 0)) for e in eligible), default=0)
            meta["capture_from_ms"] = since_ms
            meta["head_omitted"] = first > int(events[0].get("seq", 0)) if events else True
        elif int(state.get("host_seq", -1)) >= 0:
                                                                                
                                                                                    
                                                                           
            meta["head_omitted"] = True
        if facts["title"]:
            meta["title"] = facts["title"]
        if turns:
            meta["host_turns"] = turns
        if facts.get("cwd"):
            meta["project"] = facts["cwd"]
        if facts.get("is_fork") and facts["parent_session"]:
                                                                           
                                                                             
                                                              
            meta["fork_of"] = facts["parent_session"]
            meta["seeded_prefix"] = bool(facts.get("seeded_prefix"))
        elif facts["parent_session"]:
            meta["parent_session"] = facts["parent_session"]
        fr = Provelog(stream, key=key, store=store, flush=False, float_text=True)
        records = [fr.start(meta)]
    else:
        fr = _resume_recorder(store, session_id, stream, key, manifest, state)
        if fr is None:
            store.set_session_capture(session_id, title_sync=TITLE_SYNC_VERSION,
                                      turn_sync=TURN_SYNC_VERSION)
            return {"session_id": session_id, "skipped": "no_chain"}
        records = []

    jobs: dict[str, dict] = dict(manifest.get("jobs") or {})
    current_turn = manifest.get("turn", "")
    current_step = manifest.get("step", "")
    counts = {"added": 0, "tool_calls": 0, "tool_results": 0}
    current_effort = ""
    for event in events:
        kind = str(event.get("type") or "")
        data = event.get("data") or {}
        if kind == "request/header":
            cfg = ((data.get("header") or {}).get("config") or {})
            current_effort = str(cfg.get("reasoningEffort") or "") or current_effort
        if kind == "turn/start" and isinstance(data.get("turn"), int):
            current_turn, current_step = data["turn"], ""
        elif kind == "step/start":
            if isinstance(data.get("turn"), int):
                current_turn = data["turn"]
            if isinstance(data.get("step"), int):
                current_step = data["step"]
        if int(event.get("seq", -1)) <= watermark:
            continue
        if since_ms is not None and _event_ms(event, header) <= since_ms:
            continue
        if deleted_at and _event_ms(event, header) <= int(deleted_at):
            continue
        if isinstance(event.get("time"), (int, float)):
            fr.chain._clock = _clock_for(int(event["time"]))
        for action, payload in capture_mod.host_event_records(event, jobs):
            if current_turn != "" and "turn" not in payload:
                payload = {**payload, "turn": current_turn}
            if current_step != "" and "step" not in payload:
                payload = {**payload, "step": current_step}
            if action == "tool_call":
                args = (payload.get("args") or {}).get("content")
                order = {k: payload[k] for k in ("turn", "step") if k in payload}
                records.append(fr.record("tool_call", {
                    "tool": payload.get("tool", ""), "call_id": payload.get("call_id", ""),
                    **order, **fr._content("args", args)}))
                counts["tool_calls"] += 1
                continue
            if action == "assistant_message" and current_effort:
                payload = {**payload, "reasoning_effort": current_effort}
            records.append(fr.record(action, payload))
            if action == "assistant_message":
                records.append(fr.record("model_call", {
                    "provider": payload.get("provider") or "deepseek-official",
                    "model": payload.get("model") or "", "status": "ok",
                    "usage": payload.get("usage"),
                    "reasoning_effort": current_effort or None,
                    "derived_from": "assistant_message"}))
            elif action == "tool_result":
                counts["tool_results"] += 1
        counts["added"] += 1

                                                                                
                                                                              
                                                                                 
    committed = False
    if fr.chain._open:
        since_commit = stamp - float(state.get("commit_at") or 0)
        pending = int(fr.chain._seq) - int(state.get("commit_count") or 0)
        if records and (pending >= COMMIT_EVERY_RECORDS or since_commit >= COMMIT_EVERY_S):
            records.append(fr.chain.commit(reason="auto"))
            committed = True
        elif not records and state and state.get("commit_count") is None:
                                                                                
                                                                           
            records.append(fr.chain.commit(reason="backfill"))
            committed = True
    if records:
        receipts = store.append_records(stream, records, defer_index=False)
        _store_attachments(store, session_id, _attachments_home(path),
                           _collect_attachments(events))
        if (receipts and hasattr(store, "mark_orphan")
                and all(r.get("reason") == "parent_missing" for r in receipts)):
                                                                               
                                                                       
            store.mark_orphan(session_id, facts["parent_session"])
            return {"session_id": session_id, "skipped": "parent_missing",
                    "parent": facts["parent_session"]}
    max_seq = max((int(e.get("seq", -1)) for e in events), default=-1)
    if records:
                                                                                 
        store.rebuild_md(session_id)
    capture = {
        "host_seq": max(max_seq, watermark),
        "source_path": str(path),
        "source_mtime": path.stat().st_mtime,
        "source_size": path.stat().st_size,
        "chain_id": fr.chain.session_id,
        "capture_point": "host_import",
        "verification_level": "signed_local",
        "title_sync": TITLE_SYNC_VERSION,
        "turn_sync": TURN_SYNC_VERSION,
    }
    if committed:
        capture["commit_at"] = stamp
        capture["commit_count"] = int(fr.chain._seq)
    store.set_session_capture(session_id, **capture)
    return {"session_id": session_id, "stream": stream, "added": counts["added"],
            "tool_calls": counts["tool_calls"], "tool_results": counts["tool_results"],
            "committed": committed}


def watch_once(store: Any, home: Path,
               now: float | None = None, key: Any | None = None,
               since_ms: int | None = None) -> list[dict[str, Any]]:
    """One pass: ingest every DSH session whose log grew since the last poll.

    There is no "finalize" decision here on purpose: a session is not a process and
    a conversation has no end event. Anything that looks finished is simply quiet;
    if it wakes up later, the same chain is resumed and a new commitment covers the
    new tail.
    """
    import time as _time

    stamp = now if now is not None else _time.time()
    out: list[dict[str, Any]] = []
    orphans = store.orphans() if hasattr(store, "orphans") else {}
    for path in session_files(home):
        session_key = Path(path).parent.name
        parent = orphans.get(session_key)
        if parent is not None:
            if store.locate(parent) is None:
                continue                                                          
            store.clear_orphan(session_key)
        stat = path.stat()
        state = store.session_capture(Path(path).parent.name)
        if (since_ms is not None and not state
                and stat.st_mtime * 1000 < since_ms):
                                                                          
                                                                            
                                       
            continue
        unchanged = state.get("source_mtime") == stat.st_mtime and \
            state.get("source_size") == stat.st_size
                                                                                 
                                                                           
        if (unchanged and state.get("commit_count") is not None
                and state.get("title_sync") == TITLE_SYNC_VERSION
                and state.get("turn_sync") == TURN_SYNC_VERSION):
            continue
        try:
            out.append(ingest(path, store, key=key, now=stamp,
                              since_ms=since_ms))
        except Exception as exc:                                        
            out.append({"session_id": Path(path).parent.name,
                        "error": f"{type(exc).__name__}: {exc}"})
    _anchor_after_pass(store, out)
    return out


                                                                                


def _iso_ms(value: str) -> int:
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def _own_sessions(store: Any):
    """Yield (session_id, stream, folder, manifest) for every recorded session."""
    from .session_store import own_stream_id

    for row in store.sessions():
        session_id = str(row.get("session_id") or "")
        folder = store.locate(session_id)
        if folder is None:
            continue
        manifest = store._manifest(folder)
        stream = own_stream_id(manifest)
        if stream:
            yield session_id, stream, folder, manifest


def _append_lifecycle(store: Any, session_id: str, stream: str,
                      manifest: dict[str, Any], key: Any,
                      action: str, payload: dict[str, Any]) -> bool:
    """Append one signed control record to an existing conversation chain."""
    capture = store.session_capture(session_id)
    recorder = _resume_recorder(store, session_id, stream, key, manifest, capture)
    if recorder is None:
        return False
    record = recorder.record(action, payload)
    store.append_records(stream, [record])
    return True


def announce_pause(store: Any, key: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Write a signed pause record to every recorded conversation, once.

    Called after a final catch-up pass.  The pause time is the moment the
    collector acts, not the moment the user typed the command: everything the
    collector has already seen is legitimate pre-pause evidence.
    """
    from . import pause

    paused_at = pause.now_iso()
    streams: dict[str, dict[str, Any]] = {}
    for session_id, stream, _folder, manifest in _own_sessions(store):
        capture = store.session_capture(session_id)
        records = store.get_records(stream)
        if not records:
            continue
        turn = None
        for row in reversed(records):
            value = ((row.get("record") or {}).get("payload") or {}).get("turn")
            if isinstance(value, int):
                turn = value
                break
        payload = {"at": paused_at, "reason": "user",
                   "host_seq": int(capture.get("host_seq", -1))}
        if isinstance(turn, int):
            payload["turn"] = turn
        if not _append_lifecycle(store, session_id, stream, manifest, key,
                                 "lifecycle.pause", payload):
            continue
        streams[stream] = {
            "session_id": session_id,
            "host_seq": int(capture.get("host_seq", -1)),
            "source_path": str(capture.get("source_path") or ""),
            "turn": turn,
        }
    return {"announced": True, "paused_at": paused_at, "streams": streams}


def process_resume(store: Any, key: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Close a paused interval with one signed resume record per conversation.

    Strict pause: the events that arrived while paused are skipped, and the
    resume record names the gap.  Events after ``resumed_at`` are left for the
    normal pass, so a late resume never swallows the conversation that happened
    after it.  The paused interval is deliberately not read back here.
    """
    from . import pause

    resumed_at = str(state.get("resumed_at") or pause.now_iso())
    cutoff_ms = _iso_ms(resumed_at)
    out: dict[str, Any] = {}
    for stream, info in (state.get("streams") or {}).items():
        session_id = str(info.get("session_id") or "")
        folder = store.locate(session_id)
        if folder is None:
            continue
        manifest = store._manifest(folder)
        old_seq = int(info.get("host_seq", -1))
        path = Path(str(info.get("source_path") or ""))
        gap: dict[str, Any] | None = None
        max_seq = old_seq
        max_turn = None
        if path.is_file():
            header, events = read_log(path)
            paused = [e for e in events if _event_ms(e, header) <= cutoff_ms]
            max_seq = max((int(e.get("seq", -1)) for e in paused), default=old_seq)
            turns = [e.get("data", {}).get("turn") for e in paused
                     if isinstance(e.get("data", {}).get("turn"), int)]
            max_turn = max(turns) if turns else None
            if max_seq > old_seq:
                gap = {"from_host_seq": old_seq + 1, "to_host_seq": max_seq,
                       "from_turn": (info.get("turn") + 1
                                     if isinstance(info.get("turn"), int) else None),
                       "to_turn": max_turn,
                       "paused_at": state.get("paused_at") or state.get("at"),
                       "resumed_at": resumed_at}
        else:
            gap = {"unavailable": True, "from_host_seq": old_seq + 1,
                   "paused_at": (state.get("paused_at")
                                 or state.get("at")),
                   "resumed_at": resumed_at}

        records = store.get_records(stream)
        already = bool(records) and (records[-1].get("record") or {}).get("action_type") == "lifecycle.resume" \
            and ((records[-1].get("record") or {}).get("payload") or {}).get("at") == resumed_at
        if not already:
            payload = {"at": resumed_at, "reason": "user", "gap": gap,
                       }
            if not _append_lifecycle(store, session_id, stream, manifest, key,
                                     "lifecycle.resume", payload):
                continue

                                                                           
                                                                              
                                                                            
                                                                            
                           
        store.set_session_capture(session_id, host_seq=max(max_seq, old_seq),
                                  source_path=str(path))
        out[stream] = gap
    return {"done": True, "resumed_at": resumed_at, "streams": out}


                                                                             
                                                       
CATCHUP_PER_PASS = 5
                                                                              
CATCHUP_EVERY_N_PASSES = 4
_catchup_tick = 0

                                                                           
                                                                                        
_tail_watch: set[str] = set()

                                                                                     
                                                                                  
                                                                   
_last_ok: dict[str, float] = {}

                                                                                     
                                                              
PUSHES_PER_PASS = 50

                                                                               
                                                                                 
                                                      
_pending_anchor_rows: list[dict[str, Any]] = []
_in_anchor_pass = False


def begin_anchor_pass() -> None:
    """Start accumulating importer rows; the collector calls this once per loop."""
    global _in_anchor_pass
    _in_anchor_pass = True
    _pending_anchor_rows.clear()


def flush_anchor_pass(store: Any) -> None:
    """Run one anchor pass over everything the importers reported this loop."""
    global _in_anchor_pass
    rows = list(_pending_anchor_rows)
    _pending_anchor_rows.clear()
    _in_anchor_pass = False
    _flush_anchor_rows(store, rows)


def _push_budget() -> int:
    """How many claims this pass may attempt.

    Halved for a few minutes after the service says we are too fast: a client that
    keeps hammering the limit only spreads the rejection over more streams.
    """
    try:
        from . import anchoring
        limited = anchoring.rate_limited_recently()
    except Exception:  # pragma: no cover - pacing must never break the pass
        limited = False
    return max(5, PUSHES_PER_PASS // 2) if limited else PUSHES_PER_PASS


def _anchor_after_pass(store: Any, rows: list[dict[str, Any]]) -> None:
    """Queue this importer's rows and (outside a watch loop) flush immediately.

    Importers call this after every pass; when the collector has begun an anchor
    pass with ``begin_anchor_pass``, the rows are accumulated and flushed exactly
    once by ``flush_anchor_pass``, so 20+ importers cannot each trigger a claim.
    A direct caller (tests, a standalone import) still gets an immediate flush.
    """
    if _in_anchor_pass:
        _pending_anchor_rows.extend(rows)
        return
    _flush_anchor_rows(store, rows)


def _flush_anchor_rows(store: Any, rows: list[dict[str, Any]]) -> None:
    """Submit one claim per stream that grew, and collect finished receipts.

    Runs after every pass and never raises: stamping is the product's only network
    dependency, and it must not be able to stop collection. A stream that went
    quiet right after a push still gets its receipt picked up here, otherwise its
    manifest would say "queued" until the conversation moved again.
    """
    try:
        from . import anchoring
    except Exception:  # pragma: no cover - import failures must not kill the loop
        return
    touched: list[str] = []
    for row in rows:
        stream = row.get("stream")
        if stream and stream not in touched:
            touched.append(stream)
    import time as _time

    now = _time.time()
    for stream in touched:
        _tail_watch.add(stream)
                                                                                
                                                                           
    order = sorted(touched, key=lambda s: (_last_ok.get(s, 0.0), random.random()))
    budget = _push_budget()
    for stream in order[:budget]:
        try:
            out = anchoring.maybe_anchor(store, stream)
        except Exception:
            continue
        if out is not None:
            _last_ok[stream] = now
                                                                               
                                                                                  
                                                                             
                                                              
                                                                    
    quiet = [s for s in _tail_watch if s not in touched]
    random.shuffle(quiet)
    for stream in quiet[:budget]:
        try:
            anchoring.maybe_anchor(store, stream, force=True)
        except Exception:
            pass
        _tail_watch.discard(stream)
                                                                                
                                                                                
                                                   
    global _catchup_tick
    _catchup_tick += 1
    if _catchup_tick % CATCHUP_EVERY_N_PASSES == 0 and hasattr(store, "streams_needing_stamp"):
        try:
            from . import onboarding
            behind = store.streams_needing_stamp(created_after_ms=onboarding.baseline_ms())
        except Exception:
            behind = []
        for row in behind[:CATCHUP_PER_PASS]:
            try:
                                                                               
                                   
                anchoring.maybe_anchor(store, row["stream_id"], force=True,
                                       lane="delayed")
            except Exception:
                pass
                                                                                
                                                                                   
                                                                                  
    try:
        anchoring.pickup_pending(store)
    except Exception:
        pass
