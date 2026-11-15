"""Codex CLI reader: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl

Format facts (source: github.com/openai/codex codex-rs, rollout/src/list.rs
"Directory layout" comment + history/src/rollout_payload.rs
RolloutItemWire):

  - one JSON object per line: {"type": <snake_case>, "payload": {...}}
  - session_meta (first line): id, timestamp, cwd, originator, cli_version,
    git {branch, ...}
  - response_item (the conversation): OpenAI Responses-API shaped -
    message {role, content: [{type: input_text|output_text|..., text}]},
    reasoning {summary: [...], encrypted_content}, function_call {name,
    arguments (JSON string), call_id}, function_call_output {call_id,
    output (string or structured items)}, custom_tool_call(+_output),
    web_search_call, local_shell_call
  - turn_context: {cwd, model, ...} - the model applies to every assistant
    message after it until the next turn_context says otherwise
  - event_msg token_count: info.total_token_usage is the session's
    cumulative usage (snake_case input_tokens/cached_input_tokens/
    cache_write_input_tokens/output_tokens/total_tokens) and
    info.last_token_usage the most recent turn's - the turn-level numbers
    land on the last assistant message already parsed
  - other event_msg / compacted / world_state ... : not conversation, skipped
  - old sessions get background-compressed to .jsonl.zst; without a zstd
    implementation (stdlib gained one in 3.14; this package supports 3.11)
    those files are skipped and reported, never faked
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ...codex_context import is_internal_user_text
from ..model import MessageView, SessionSummary, SessionView, ToolCallView, usage_dict

_SESSION_DIRS = ("sessions", "archived_sessions")


def _config_root(home: Path) -> Path:
    """~/.codex, or $CODEX_HOME when codex runs with its official home
    override (sessions move with it)."""
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env else home / ".codex"


def install_dir(home: Path) -> Path:
    """The directory whose existence marks this CLI as installed."""
    return _config_root(home)


def data_dir(home: Path) -> Path:
    return _config_root(home)


def _iter_session_files(root: Path):
    from . import is_receipt_file

    for sub in _SESSION_DIRS:
        base = root / sub
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("rollout-*.jsonl")):
            if is_receipt_file(f):                                         
                continue
            yield f


def _first_line_meta(path: Path) -> dict:
    """session_meta from the head, best effort ({} when unreadable)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                if isinstance(o, dict) and o.get("type") == "session_meta":
                    return o.get("payload") or {}
                break
    except (OSError, ValueError):
        pass
    return {}


def _iso_mtime(path: Path) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        path.stat().st_mtime, tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text_of_content(content) -> str:
    """[{type: input_text|output_text|..., text}] -> joined text."""
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict) and isinstance(b.get("text"), str):
            parts.append(b["text"])
    return "\n".join(p for p in parts if p)


def _short(value) -> str | None:
    v = " ".join(value.split()) if isinstance(value, str) else (
        " ".join(str(x) for x in value) if isinstance(value, list) and
        all(isinstance(x, (str, int, float)) for x in value) else None)
    if v is None or not v.strip():
        return None
    return v if len(v) <= 120 else v[:117] + "…"


def _summarize(name: str, arguments) -> str:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return _short(arguments) or ""
    if isinstance(arguments, dict):
        for k in ("command", "cmd", "file_path", "path", "pattern", "url",
                  "query", "prompt", "description", "name", "skill"):
            v = _short(arguments.get(k))
            if v:
                return v
    raw = json.dumps(arguments, ensure_ascii=False) if arguments else ""
    return raw if len(raw) <= 120 else raw[:117] + "…"


def _output_text(output) -> str | None:
    if isinstance(output, str):
        return output or None
    if isinstance(output, dict):
        for k in ("output", "text", "content", "message"):
            if isinstance(output.get(k), str):
                return output[k]
    if isinstance(output, list):
        text = _text_of_content(output)
        return text or None
    if output is not None:
        return json.dumps(output, ensure_ascii=False)
    return None


def discover(home: Path) -> list[SessionSummary]:
    root = data_dir(home)
    out: list[SessionSummary] = []
    if not root.is_dir():
        return out
    for path in _iter_session_files(root):
        meta = _first_line_meta(path)
        cwd = meta.get("cwd") if isinstance(meta.get("cwd"), str) else None
        ts = meta.get("timestamp") if isinstance(meta.get("timestamp"), str) else None
        out.append(SessionSummary(
            host="codex",
            session_id=path.stem,
            title=(path.stem.replace("rollout-", "")[:36]),
            project=cwd,
            started_at=ts,
            last_at=_iso_mtime(path),
            message_count=0,
            source_path=str(path),
        ))
    return out


def _usage_of(u) -> dict | None:
    """TokenUsage shape (snake_case) -> canonical keys. reasoning_output_
    tokens is a provider-side subset of output; it is not displayed as its
    own line, and total_tokens is taken verbatim when present."""
    if not isinstance(u, dict):
        return None
    return usage_dict({
        "input": u.get("input_tokens"),
        "output": u.get("output_tokens"),
        "cache_read": u.get("cached_input_tokens"),
        "cache_write": u.get("cache_write_input_tokens"),
        "total": u.get("total_tokens"),
    })


def load(home: Path, session_id: str) -> SessionView | None:
    root = data_dir(home)
    if not root.is_dir():
        return None
    target = next((f for f in _iter_session_files(root) if f.stem == session_id),
                  None)
    if target is None:
        return None

    messages: list[MessageView] = []
    pending: dict[str, ToolCallView] = {}
    bad = 0
    project: str | None = None
    started: str | None = None

                                                                        
                                                             
    held_thinking: str | None = None
                                                        
    held_model: str | None = None
    usage_total: dict | None = None                                

    try:
        fh = open(target, encoding="utf-8", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                o = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if not isinstance(o, dict):
                bad += 1
                continue
            t = o.get("type")
            p = o.get("payload") if isinstance(o.get("payload"), dict) else {}

            if t == "session_meta":
                if isinstance(p.get("cwd"), str):
                    project = p["cwd"]
                if isinstance(p.get("timestamp"), str):
                    started = p["timestamp"]
            elif t == "turn_context":
                if project is None and isinstance(p.get("cwd"), str):
                    project = p["cwd"]
                if isinstance(p.get("model"), str) and p["model"]:
                    held_model = p["model"]

            elif t == "event_msg" and p.get("type") == "token_count":
                info = p.get("info") if isinstance(p.get("info"), dict) else {}
                u = _usage_of(info.get("last_token_usage"))
                if u is not None:
                    last_assistant = next(
                        (m for m in reversed(messages)
                         if m.role == "assistant"), None)
                    if last_assistant is not None:
                        last_assistant.usage = u
                u = _usage_of(info.get("total_token_usage"))
                if u is not None:
                    usage_total = u

            elif t == "response_item":
                pt = p.get("type")
                if pt == "message":
                    role = p.get("role")
                    text = _text_of_content(p.get("content"))
                    if role == "user" and text.strip() and not is_internal_user_text(text):
                        messages.append(MessageView(role="user", ts=None, text=text))
                    elif role == "assistant":
                        m = MessageView(role="assistant", ts=None,
                                        thinking=held_thinking, text=text)
                        held_thinking = None
                        if m.thinking or m.text:
                            m.model = held_model
                            messages.append(m)
                elif pt == "reasoning":
                    parts = []
                    for s in p.get("summary") or []:
                        if isinstance(s, dict) and isinstance(s.get("text"), str):
                            parts.append(s["text"])
                    if parts:
                        held_thinking = "\n".join(parts)
                elif pt in ("function_call", "custom_tool_call"):
                    args = p.get("arguments")
                    card = ToolCallView(
                        name=str(p.get("name") or "?"),
                        summary=_summarize(str(p.get("name") or ""), args))
                    card.input_text = args if isinstance(args, str) else (
                        json.dumps(args, ensure_ascii=False, indent=2)
                        if args is not None else None)
                    m = MessageView(role="assistant", ts=None, model=held_model)
                    m.tool_calls.append(card)
                    messages.append(m)
                    if isinstance(p.get("call_id"), str):
                        pending[p["call_id"]] = card
                elif pt in ("function_call_output", "custom_tool_call_output"):
                    card = pending.pop(p.get("call_id") or "", None)
                    if card is not None:
                        card.output = _output_text(p.get("output"))
                                                                         
                                                                         

    title = next((m.text.splitlines()[0][:80] for m in messages
                  if m.role == "user" and m.text.strip()
                  and not m.text.lstrip().startswith("<")), "")
    return SessionView(
        host="codex",
        session_id=session_id,
        title=title or session_id.replace("rollout-", "")[:36],
        project=project,
        started_at=started,
        last_at=_iso_mtime(target),
        messages=messages,
        bad_lines=bad,
        source_path=str(target),
        usage_total=usage_total,
    )
