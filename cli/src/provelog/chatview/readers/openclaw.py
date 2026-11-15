"""OpenClaw reader: ~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl

Format facts (source: github.com/openclaw/openclaw - src/config/sessions/
transcript-header.ts + transcript-append.test-support.ts and
packages/llm-core/src/types.ts):

  - first line is a header: {"type":"session","version","id","timestamp",
    "cwd","parentSession"?} - parentSession marks sub-agent sessions,
    which stay out of the listing
  - then one message per line: {"type":"message","id","parentId",
    "timestamp","message":<AgentMessage>}
  - AgentMessage shapes that matter here:
      user       content: str | [{type:"text"|"image",…}]
      assistant  content: [{type:"text",text} | {type:"thinking",thinking}
                          | {type:"toolCall",id,name,arguments(object)}]
      toolResult {toolCallId, toolName, content, isError} - joins to its
                 toolCall card by toolCallId
    (bashExecution / custom / branchSummary / compactionSummary rows are
    not conversation: skipped)
  - topic forks land beside the session as <id>-topic-<tid>.jsonl and
    deleted sessions as .jsonl.deleted.<ts> - neither is listed
  - NEWER openclaw stores sessions in per-agent SQLite with zstd-
    compressed event payloads; that store is NOT read by this build -
    the JSONL artifacts above are, and a machine with only the SQLite
    store reports the host as empty, never as faked history
"""

from __future__ import annotations

import json
from pathlib import Path

from ..model import MessageView, SessionSummary, SessionView, ToolCallView, usage_dict


def install_dir(home: Path) -> Path:
    """The directory whose existence marks this CLI as installed (this
    host documents no config-dir environment variable - the standard
    home-relative path is the only official location)."""
    return home / ".openclaw"


def data_dir(home: Path) -> Path:
    return home / ".openclaw"


def _iter_session_files(root: Path):
    """(agent_id, path) for every plain session transcript (topic forks,
    deleted archives, stray files and provelog's own receipt chains
    excluded); sorted for stable lists."""
    from . import is_receipt_file

    agents = root / "agents"
    if not agents.is_dir():
        return
    for agent_dir in sorted(p for p in agents.iterdir() if p.is_dir()):
        sessions = agent_dir / "sessions"
        if not sessions.is_dir():
            continue
        for f in sorted(sessions.glob("*.jsonl")):
            if "-topic-" in f.stem or ".deleted." in f.name \
                    or is_receipt_file(f):
                continue
            yield agent_dir.name, f


def _text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text" \
                and isinstance(b.get("text"), str):
            parts.append(b["text"])
    return "\n".join(p for p in parts if p)


def _short(value) -> str | None:
    v = " ".join(value.split()) if isinstance(value, str) else (
        " ".join(str(x) for x in value) if isinstance(value, list) and
        all(isinstance(x, (str, int, float)) for x in value) else None)
    if v is None or not v.strip():
        return None
    return v if len(v) <= 120 else v[:117] + "…"


def _summarize(arguments) -> str:
    if isinstance(arguments, dict):
        for k in ("command", "cmd", "file_path", "filePath", "path",
                  "pattern", "url", "query", "prompt", "description",
                  "name", "skill"):
            v = _short(arguments.get(k))
            if v:
                return v
    raw = json.dumps(arguments, ensure_ascii=False) if arguments else ""
    return raw if len(raw) <= 120 else raw[:117] + "…"


def _header_facts(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                if isinstance(o, dict) and o.get("type") == "session":
                    return o
                break
    except (OSError, ValueError):
        pass
    return {}


def _first_user_text(path: Path) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(o, dict) or o.get("type") != "message":
                    continue
                msg = o.get("message")
                if isinstance(msg, dict) and msg.get("role") == "user":
                    text = _text(msg.get("content")).strip()
                    if text and not text.startswith("<"):
                        return text.splitlines()[0][:80]
    except OSError:
        pass
    return ""


def _iso_mtime(path: Path) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        path.stat().st_mtime, tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def discover(home: Path) -> list[SessionSummary]:
    out: list[SessionSummary] = []
    for _agent, path in _iter_session_files(data_dir(home)):
        head = _header_facts(path)
        if head.get("parentSession"):
            continue                                        
        out.append(SessionSummary(
            host="openclaw",
            session_id=path.stem,
            title=_first_user_text(path) or path.stem[:36],
            project=head.get("cwd") if isinstance(head.get("cwd"), str) else None,
            started_at=head.get("timestamp")
            if isinstance(head.get("timestamp"), str) else None,
            last_at=_iso_mtime(path),
            message_count=0,
            source_path=str(path),
        ))
    return out


def load(home: Path, session_id: str) -> SessionView | None:
    target = next((f for _a, f in _iter_session_files(data_dir(home))
                   if f.stem == session_id), None)
    if target is None:
        return None

    messages: list[MessageView] = []
    pending: dict[str, ToolCallView] = {}                            
    bad = 0
    project: str | None = None
    started: str | None = None

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
            if t == "session":
                if isinstance(o.get("cwd"), str):
                    project = o["cwd"]
                if isinstance(o.get("timestamp"), str):
                    started = o["timestamp"]
                continue
            if t != "message":
                continue                                                  
            msg = o.get("message")
            if not isinstance(msg, dict):
                bad += 1
                continue
            ts = o.get("timestamp") if isinstance(o.get("timestamp"), str) else None
            role = msg.get("role")

            if role == "user":
                text = _text(msg.get("content")).strip()
                if text:
                    messages.append(MessageView(role="user", ts=ts, text=text))
            elif role == "assistant":
                m = MessageView(role="assistant", ts=ts)
                think, texts = [], []
                for b in msg.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text" and b.get("text"):
                        texts.append(b["text"])
                    elif bt == "thinking" and b.get("thinking"):
                        think.append(b["thinking"])
                    elif bt == "toolCall":
                        args = b.get("arguments")
                        card = ToolCallView(
                            name=str(b.get("name") or "?"),
                            summary=_summarize(args))
                        card.input_text = (
                            json.dumps(args, ensure_ascii=False, indent=2)
                            if args is not None else None)
                        m.tool_calls.append(card)
                        if isinstance(b.get("id"), str):
                            pending[b["id"]] = card
                if think or texts or m.tool_calls:
                    m.thinking = "\n".join(think) or None
                    m.text = "\n\n".join(texts)
                    if isinstance(msg.get("model"), str):
                        m.model = msg["model"]
                                                                       
                                                                          
                                                              
                    tk = msg.get("tokens")
                    if isinstance(tk, dict):
                        cache = tk.get("cache") if isinstance(tk.get("cache"),
                                                               dict) else {}
                        m.usage = usage_dict({
                            "input": tk.get("input"),
                            "output": tk.get("output"),
                            "cache_read": cache.get("read"),
                            "cache_write": cache.get("write"),
                            "total": tk.get("total"),
                        })
                    messages.append(m)
            elif role == "toolResult":
                card = pending.pop(msg.get("toolCallId") or "", None)
                if card is not None:
                    card.output = _text(msg.get("content")) or None
                    card.is_error = bool(msg.get("isError"))
                                                                         
                                                 

    title = next((m.text.splitlines()[0][:80] for m in messages
                  if m.role == "user" and m.text.strip()
                  and not m.text.lstrip().startswith("<")), "")
    return SessionView(
        host="openclaw",
        session_id=session_id,
        title=title or session_id[:36],
        project=project,
        started_at=started,
        last_at=_iso_mtime(target),
        messages=messages,
        bad_lines=bad,
        source_path=str(target),
    )
