"""Capture points: what a boundary reports, before the store chains it.

The capture layer never signs and never touches the hash chain. It turns what
it sees at one boundary (a model request/response, a host log event) into
plain event payloads, and the store is the single writer that orders and
chains them.

Two things this module owns:

``identify``
    Which host and which host session a log event belongs to. Importers use
    the host's own session id; nothing here stores headers, only the derived
    id is kept.

``diff``
    A model request carries the whole conversation so far. Emitting every
    message on every call would store the history once per turn (quadratic).
    The watermark (count + digest of the message fingerprints) tells us which
    messages are new, and whether the host rewrote the context (compaction).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .canonical import canonicalize

CLAUDE_SESSION_HEADER = "x-claude-code-session-id"
DSH_SESSION_HEADER = "x-deepseek-harness-session-id"
CLAUDE_UA_PREFIX = "claude-cli"
DSH_UA_PREFIX = "deepseek-harness"

HOST_CLAUDE = "claude-code"
HOST_DSH = "dsh"


def _header(headers: Mapping[str, str] | None, name: str) -> str:
    if not headers:
        return ""
    for key, value in headers.items():
        if key.lower() == name:
            return value or ""
    return ""


def _ua_host(user_agent: str) -> str | None:
    ua = (user_agent or "").lower()
    if ua.startswith(CLAUDE_UA_PREFIX):
        return HOST_CLAUDE
    if ua.startswith(DSH_UA_PREFIX):
        return HOST_DSH
    return None


def body_session_key(body: Any) -> str:
    """A stable conversation key when the host sends no session header.

    Some custom-model clients carry the full message list but no stable
    session id. The first user message is stable for the life of a
    conversation, so its digest is the best available grouping key.
    """
    if not isinstance(body, dict):
        return ""
    messages = body.get("messages")
    if not isinstance(messages, list):
        messages = body.get("input") if isinstance(body.get("input"), list) else []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "") != "user":
            continue
        try:
            raw = canonicalize(message)
        except Exception:
            raw = json.dumps(message, sort_keys=True, default=str).encode()
        return "body-" + hashlib.sha256(raw).hexdigest()[:24]
    return ""


def _metadata_user_id(body: Any) -> dict[str, str]:
    """Claude Code sends ``metadata.user_id`` as a JSON *string*; parse it."""
    if not isinstance(body, dict):
        return {}
    raw = (body.get("metadata") or {}).get("user_id")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return {k: str(v) for k, v in parsed.items() if isinstance(v, (str, int))}
    if isinstance(raw, dict):
        return {k: str(v) for k, v in raw.items() if isinstance(v, (str, int))}
    return {}


def identify(headers: Mapping[str, str] | None, body: Any = None) -> dict[str, str]:
    """Best-effort identity of one model request: host, session, device.

    Returns ``{}`` values instead of raising: an unattributable request is
    still captured, it just lands in the unassigned stream.
    """
    user_agent = _header(headers, "user-agent")
    host = _ua_host(user_agent) or ""
    session_id = (_header(headers, CLAUDE_SESSION_HEADER)
                  or _header(headers, DSH_SESSION_HEADER))
    meta = _metadata_user_id(body)
    if not session_id:
        session_id = meta.get("session_id", "")
    if not host and session_id and meta:
        host = HOST_CLAUDE
    return {"host": host, "host_session_id": session_id,
            "device_id": meta.get("device_id", "")}


def asset_hash(value: Any) -> str:
    """Stable digest of a session asset (system prompt / tool list / params)."""
    try:
        from .canonical import canonicalize, sha256_hex
        return "sha256:" + sha256_hex(canonicalize(value))
    except Exception:
        import json
        return "sha256:" + __import__("hashlib").sha256(
            json.dumps(value, default=str).encode()).hexdigest()


def fingerprint(message: Any) -> str:
    """Stable digest of one message, used for the request-diff watermark."""
    try:
        raw = canonicalize(message)
    except Exception:
        raw = json.dumps(message, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def prefix_digest(fingerprints: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(fingerprints).encode()).hexdigest()


def diff(seen_count: int, seen_digest: str, messages: Sequence[Any]
         ) -> tuple[list[Any], bool, dict[str, Any]]:
    """Split a request's message list into the part already stored and the new part.

    Returns ``(new_messages, rewritten, watermark)``. ``rewritten`` is True
    when the incoming list is not a prefix-extension of what we stored, which
    is what a host-side compaction looks like from here.
    """
    fps = [fingerprint(m) for m in messages]
    n = max(0, min(int(seen_count or 0), len(fps)))
    matches = bool(seen_digest) and n > 0 and prefix_digest(fps[:n]) == seen_digest
    if n == 0:
        matches = not seen_digest or seen_count == 0
    new_messages = list(messages[n:]) if matches else list(messages)
    watermark = {"count": len(fps), "digest": prefix_digest(fps)} if fps else {
        "count": 0, "digest": ""}
    return new_messages, (not matches and seen_count > 0), watermark


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return ""


def split_message(message: Any) -> list[dict[str, Any]]:
    """Turn one provider message into capture events (user / tool result / assistant)."""
    if not isinstance(message, dict):
        return []
    role = str(message.get("role") or "")
    content = message.get("content")
    events: list[dict[str, Any]] = []
    blocks = content if isinstance(content, list) else None

    if role == "tool" or (isinstance(message, dict) and message.get("tool_call_id")):
        events.append({"kind": "tool_result",
                       "call_id": str(message.get("tool_call_id") or ""),
                       "name": str(message.get("name") or ""),
                       "content": content})
        return events

    if role == "user":
        if blocks:
            results = [b for b in blocks
                       if isinstance(b, dict) and b.get("type") in ("tool_result", "tool")]
            if results:
                for b in results:
                    events.append({
                        "kind": "tool_result",
                        "call_id": str(b.get("tool_use_id") or b.get("tool_call_id") or ""),
                        "content": b.get("content"),
                        "is_error": bool(b.get("is_error")),
                    })
                return events
        text = _text_of(content)
        events.append({"kind": "user_message", "content": content if blocks else text})
        return events

    if role == "assistant":
        events.append({"kind": "assistant_message", "content": content})
        return events

    return events


def assemble_sse(text: str, provider: str) -> dict[str, Any]:
    """Best-effort assembly of a streamed response into one assistant message.

    We deliberately do not keep chunk timing or boundaries; what is kept is
    the text, the thinking text, the tool calls and (when present) the usage,
    i.e. the same content a non-streamed response would have carried.
    """
    out: dict[str, Any] = {"text": "", "thinking": "", "tool_calls": [], "usage": None,
                           "model": "", "stop_reason": None}
    texts: list[str] = []
    thinking: list[str] = []
    calls: dict[str, dict[str, Any]] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
                   
        if obj.get("type") == "content_block_delta":
            delta = obj.get("delta") or {}
            if delta.get("type") == "text_delta":
                texts.append(str(delta.get("text") or ""))
            elif delta.get("type") == "thinking_delta":
                thinking.append(str(delta.get("thinking") or ""))
            elif delta.get("type") == "input_json_delta":
                idx = str(obj.get("index", ""))
                slot = calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                slot["arguments"] += str(delta.get("partial_json") or "")
        elif obj.get("type") == "content_block_start":
            block = obj.get("content_block") or {}
            if block.get("type") == "tool_use":
                idx = str(obj.get("index", ""))
                calls[idx] = {"id": str(block.get("id") or ""),
                              "name": str(block.get("name") or ""), "arguments": ""}
        elif obj.get("type") == "message_start":
            msg = obj.get("message") or {}
            out["model"] = str(msg.get("model") or out["model"])
        elif obj.get("type") == "message_delta":
            out["stop_reason"] = (obj.get("delta") or {}).get("stop_reason", out["stop_reason"])
                             
        elif obj.get("object") == "chat.completion.chunk" or "choices" in obj:
            out["model"] = str(obj.get("model") or out["model"])
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    texts.append(str(delta["content"]))
                if delta.get("reasoning_content"):
                    thinking.append(str(delta["reasoning_content"]))
                for tc in delta.get("tool_calls") or []:
                    idx = str(tc.get("index", ""))
                    slot = calls.setdefault(idx, {"id": str(tc.get("id") or ""),
                                                  "name": "", "arguments": ""})
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = str(fn["name"])
                    if fn.get("arguments"):
                        slot["arguments"] += str(fn["arguments"])
                if choice.get("finish_reason"):
                    out["stop_reason"] = choice["finish_reason"]
        if isinstance(obj.get("usage"), dict):
            out["usage"] = obj["usage"]
    out["text"] = "".join(texts)
    out["thinking"] = "".join(thinking)
    out["tool_calls"] = list(calls.values())
    return out


def assistant_blocks(assembled: dict[str, Any]) -> list[dict[str, Any]]:
    """Provider-neutral content blocks for an assembled assistant message."""
    blocks: list[dict[str, Any]] = []
    if assembled.get("thinking"):
        blocks.append({"type": "thinking", "text": assembled["thinking"]})
    if assembled.get("text"):
        blocks.append({"type": "text", "text": assembled["text"]})
    for call in assembled.get("tool_calls") or []:
        raw = call.get("arguments") or ""
        try:
            args = json.loads(raw) if raw else {}
        except ValueError:
            args = {"_raw": raw}
        blocks.append({"type": "tool_use", "id": call.get("id", ""),
                       "name": call.get("name", ""), "input": args})
    return blocks


def blocks_from_response(obj: Any, provider: str = "") -> list[dict[str, Any]]:
    """Provider-neutral content blocks for a complete (non-streamed) response."""
    if isinstance(obj, str):
        return [{"type": "text", "text": obj}]
    if not isinstance(obj, dict):
        return [{"type": "text", "text": json.dumps(obj, ensure_ascii=False, default=str)}]
    blocks: list[dict[str, Any]] = []
    content = obj.get("content")
    if isinstance(content, list):                                           
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("text", "thinking", "tool_use"):
                blocks.append(b)
        if blocks:
            return blocks
    choices = obj.get("choices")                                               
    if isinstance(choices, list) and choices:
        msg = (choices[0] or {}).get("message") or {}
        if msg.get("content"):
            blocks.append({"type": "text", "text": msg["content"]})
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw)
            except ValueError:
                args = {"_raw": raw}
            blocks.append({"type": "tool_use", "id": str(tc.get("id") or ""),
                           "name": str(fn.get("name") or ""), "input": args})
        if blocks:
            return blocks
    candidates = obj.get("candidates")                          
    if isinstance(candidates, list) and candidates:
        parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
        for part in parts:
            if part.get("text"):
                blocks.append({"type": "text", "text": part["text"]})
            call = part.get("functionCall")
            if isinstance(call, dict):
                blocks.append({"type": "tool_use", "id": "",
                               "name": str(call.get("name") or ""),
                               "input": call.get("args") or {}})
        if blocks:
            return blocks
    return [{"type": "text", "text": json.dumps(obj, ensure_ascii=False, default=str)}]


                                                                              
                                                                             
                                                                                
                                                                   
_TRUNCATION_MARKERS: tuple[tuple[str, str], ...] = (
    (r"<persisted-output>", "persisted-output"),
    (r"Output too large", "size-notice"),
    (r"Full output saved to[:\s]+(?P<path>\S+)", "saved-to-file"),
    (r"\[(?:output |content )?truncated", "truncation-marker"),
    (r"(?:\.\.\.|…)\s*\(truncated", "truncation-marker"),
    (r"\(truncated \d+", "truncation-marker"),
)


def detect_truncation(value: Any) -> dict[str, Any] | None:
    """Best-effort marker when a host truncated a tool result.

    Heuristic by nature, so the result says so: ``detected: "heuristic"``.
    ``external_ref`` is the file the host pointed at, when it named one.
    """
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(_text_of(block))
        text = "\n".join(p for p in parts if p)
    elif isinstance(value, dict):
        text = "\n".join(str(v) for v in value.values() if isinstance(v, str))
    else:
        return None
    if not text:
        return None
    import re as _re
    kind = ""
    ref = ""
    for pattern, marker_kind in _TRUNCATION_MARKERS:
        match = _re.search(pattern, text, _re.IGNORECASE)
        if not match:
            continue
        if not kind:
            kind = marker_kind
        try:
            ref = ref or (match.group("path") or "")
        except IndexError:
            pass
    if not kind:
        return None
    return {"by": "host", "kind": kind, "detected": "heuristic", "external_ref": ref}


                                                                              
                                                                           
                                                                         
                                                  
 
                                                                              
                                                                          
                                                                             
                                                                             
                                                                                
                                                                           

                                                                           
                                                                           
INJECTED_PREFIXES = ("Current runtime context", "<system-reminder>",
                     "Time sampled while preparing")


def is_injected_context(text: str) -> bool:
    return any(str(text or "").startswith(prefix) for prefix in INJECTED_PREFIXES)


def _flatten_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _turn_step(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(data.get("turn"), int):
        out["turn"] = data["turn"]
    if isinstance(data.get("step"), int):
        out["step"] = data["step"]
    return out


def _exit_code(detail: Any) -> int | None:
    if not isinstance(detail, str):
        return None
    match = re.search(r"exit code:\s*(-?\d+)", detail)
    return int(match.group(1)) if match else None


def host_event_records(event: Any, jobs: dict[str, Any] | None = None
                       ) -> list[tuple[str, dict[str, Any]]]:
    """Translate one DSH canonical event into zero or more records.

    ``jobs`` carries job/settled results (exit code, duration) forward to the
    tool/result that follows them in the log; without it those facts are lost.
    """
    if not isinstance(event, dict):
        return []
    kind = str(event.get("type") or "")
    data = event.get("data") or {}
    if kind == "user/message":
        content = data.get("content")
        origin = str((data.get("source") or {}).get("kind") or "user")
        if origin == "user" and is_injected_context(_flatten_text(content)):
            origin = "context"
        return [("user_message", {
            "content": content,
            "id": str(data.get("id") or ""),
            "origin": origin,
            **_turn_step(data)})]
    if kind == "assistant/message":
        message = data.get("message") or {}
        blocks = message.get("content")
        if not isinstance(blocks, list):
            blocks = [{"type": "text", "text": _flatten_text(blocks)}]
        model = provider = ""
        source = message.get("source") or {}
        if isinstance(source, dict):
            model = str(source.get("model") or "")
            provider = str(source.get("provider") or "")
        return [("assistant_message", {
            "content": blocks, "model": model, "provider": provider,
            "usage": data.get("usage") if isinstance(data.get("usage"), dict) else None,
            "id": str(message.get("id") or ""),
            **_turn_step(data)})]
    if kind == "tool/call":
        raw = data.get("arguments")
        try:
            import json as _json
            args = _json.loads(raw) if isinstance(raw, str) and raw else raw
        except ValueError:
            args = {"_raw": raw}
        return [("tool_call", {"tool": str(data.get("name") or ""),
                               "call_id": str(data.get("callId") or ""),
                               "args": {"content": args},
                               **_turn_step(data)})]
    if kind == "job/settled":
        if jobs is None:
            return []
        call = (data.get("toolCall") or {}).get("callId")
        if not call:
            return []
        started, finished = data.get("startedAt"), data.get("finishedAt")
        jobs[str(call)] = {
            "status": str(data.get("status") or ""),
            "exit_code": _exit_code(data.get("detail")),
            "duration_ms": (int(finished) - int(started))
            if isinstance(started, (int, float)) and isinstance(finished, (int, float))
            else None,
        }
        return []
    if kind == "tool/result":
        message = data.get("message") or {}
        content = message.get("content")
        call_id = str(data.get("toolCallId") or message.get("toolCallId") or "")
        execution = (jobs or {}).pop(call_id, None) if jobs is not None else None
        payload: dict[str, Any] = {
            "call_id": call_id,
            "content": content,
            "is_error": bool(message.get("isError") or data.get("error")),
            "truncated": detect_truncation(content),
            **_turn_step(data)}
        if execution:
            payload["execution"] = execution
            if execution.get("exit_code") is not None:
                payload["exit_code"] = execution["exit_code"]
        return [("tool_result", payload)]
                                                                          
                                                                                
                                                        
                                                                               
                                                           
    return []


def dsh_turns(events: list[Any]) -> dict[str, dict[str, Any]]:
    """Per-turn {start, end, reason} from DSH turn/start and turn/end events.

    This is display metadata for the viewer (duration and stopped/failed
    labels); the signed event records remain the source of truth.
    """
    turns: dict[str, dict[str, Any]] = {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind not in ("turn/start", "turn/end"):
            continue
        data = event.get("data") or {}
        number = data.get("turn")
        if not isinstance(number, int):
            continue
        row = turns.setdefault(str(number), {})
        at = event.get("time")
        if isinstance(at, (int, float)):
            row["start" if kind == "turn/start" else "end"] = int(at)
        if kind == "turn/end":
            reason = data.get("reason") or {}
            row["reason"] = str(reason.get("kind") or "") if isinstance(reason, dict) else ""
    return turns


def dsh_session_meta(events: list[Any], header: dict[str, Any] | None = None
                     ) -> dict[str, Any]:
    """(id, title, parent) from a DSH session header and its event list.

    DSH writes titles as ``fallback`` (first user message), ``provider`` (small
    LLM), and ``user`` (an explicit rename). An explicit user title is the
    latest host title and wins over provider/fallback; provider wins over the
    fallback truncation. Renames can arrive after the first import, so callers
    must refresh the manifest title on later passes too.
    """
    header = header or {}
    fallback = provider = user = ""
    parent = str(header.get("parentSession") or "")
    seeded = bool(header.get("isSeeded"))
    depth = int(header.get("delegationDepth") or 0)
    for event in events or []:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "session/title":
            continue
        data = event.get("data") or {}
        value = str(data.get("title") or "")
        if not value:
            continue
        kind = str((data.get("source") or {}).get("kind") or "")
        if kind == "user":
            user = value
        elif kind == "provider":
            provider = value
        else:
            fallback = value
    return {"session_id": str(header.get("id") or ""),
            "title": user or provider or fallback,
            "parent_session": parent, "cwd": str(header.get("cwd") or ""),
                                                                              
                                                                 
            "is_fork": bool(parent and seeded and depth == 0),
            "delegation_depth": depth,
            "seeded_prefix": bool(seeded),
            "created_at": header.get("createdAt")}
