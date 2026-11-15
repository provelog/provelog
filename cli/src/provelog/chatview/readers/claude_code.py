"""Claude Code reader: ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl

Format facts (Claude Code 2.1.x):

  - one JSON object per line; the interesting types:
      assistant  message.content = [blocks]; block types seen:
                 thinking {thinking}, text {text}, tool_use {id,name,input}
      user       message.content = str, or [blocks] with tool_result
                 {tool_use_id, content: str|[{type:"text",text}], is_error}
                 isMeta lines (command caveats, /command echoes) are CLI
                 bookkeeping, not conversation - skipped
      ai-title   {aiTitle} - the session's own title, written as the
                 conversation gets one
      summary    {summary} - title for continued sessions (older format)
      attachment / system / file-history-* / mode / queue-operation /
      cost-state / last-prompt / permission-mode / atis-latch - not
                 conversation, skipped
  - every line carries sessionId, cwd, timestamp (ISO), uuid/parentUuid;
    isSidechain=true marks sub-agent (Task tool) turns interleaved into the
    same file - excluded from the main flow

Bad lines (invalid JSON, unknown shape) are skipped and counted, never
fatal: a session renders with whatever parsed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..model import MessageView, SessionSummary, SessionView, ToolCallView, usage_dict

_ROOT = ("projects",)                           
_TITLE_SCAN_LINES = 400                                                   


def _config_root(home: Path) -> Path:
    """~/.claude, or $CLAUDE_CONFIG_DIR when Claude Code runs with its
    official config-dir override (it moves the whole directory: settings,
    projects/, everything - history included)."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else home / ".claude"


def install_dir(home: Path) -> Path:
    """The directory whose existence marks this CLI as installed."""
    return _config_root(home)


def _projects_dir(home: Path) -> Path:
    return _config_root(home).joinpath(*_ROOT)


def data_dir(home: Path) -> Path:
    return _projects_dir(home)


def _decode_project_dir(name: str) -> str:
    """Claude Code encodes the session's cwd as its dirname: '/' becomes
    '-' ('/home/k/myproject' -> '-home-k-myproject'). The mapping is
    ambiguous (a '-' IN a path segment encodes identically), so lines'
    real `cwd` field wins when available and this is only the fallback."""
    return "/" + name.lstrip("-") if name.startswith("-") else name


def _blocks(message: dict) -> list:
    content = message.get("content")
    return content if isinstance(content, list) else []


def _text_of(content) -> str:
    """user/assistant content can be a plain string or a block list; join
    the text-ish parts."""
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text") or "")
    return "\n".join(p for p in parts if p)


def _summarize_tool(name: str, tool_input) -> str:
    """One line of the collapsed card. Best-effort over the fields coding
    tools actually use; falls back to a compact JSON fragment."""
    if isinstance(tool_input, dict):
        for k in ("command", "cmd", "file_path", "path", "pattern", "url",
                  "query", "prompt", "description", "name", "skill"):
            v = tool_input.get(k)
            if isinstance(v, str) and v.strip():
                v = " ".join(v.split())
                return v if len(v) <= 120 else v[:117] + "…"
    raw = json.dumps(tool_input, ensure_ascii=False) if tool_input is not None else ""
    raw = " ".join(raw.split())
    return raw if len(raw) <= 120 else raw[:117] + "…"


def _pretty_input(tool_input) -> str | None:
    if tool_input is None:
        return None
    try:
        return json.dumps(tool_input, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(tool_input)


def _usage_of(msg: dict) -> dict | None:
    """assistant messages carry their model call's usage verbatim
    (input_tokens / output_tokens / cache_read_input_tokens /
    cache_creation_input_tokens); map into the canonical keys."""
    u = msg.get("usage")
    if not isinstance(u, dict):
        return None
    return usage_dict({
        "input": u.get("input_tokens"),
        "output": u.get("output_tokens"),
        "cache_read": u.get("cache_read_input_tokens"),
        "cache_write": u.get("cache_creation_input_tokens"),
    })


def _iter_session_files(projects: Path):
    """(project_dir_name, path) for every session file, sorted for stable
    listing. Tolerates a missing or cluttered root; skips provelog's own
    receipt chains, which sit beside the transcripts and end .jsonl too."""
    from . import is_receipt_file

    if not projects.is_dir():
        return
    for pd in sorted(p for p in projects.iterdir() if p.is_dir()):
        for f in sorted(pd.glob("*.jsonl")):
            if is_receipt_file(f):
                continue
            yield pd.name, f


def discover(home: Path) -> list[SessionSummary]:
    """Cheap listing: per file, scan the head for a title and timestamps,
    never parsing the whole conversation."""
    projects = _projects_dir(home)
    out: list[SessionSummary] = []
    for pd_name, path in _iter_session_files(projects):
        title, started, project = "", None, None
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i >= _TITLE_SCAN_LINES:
                        break
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if started is None and isinstance(o.get("timestamp"), str):
                        started = o["timestamp"]
                    if project is None and isinstance(o.get("cwd"), str):
                        project = o["cwd"]
                    if not title:
                        if o.get("type") == "ai-title" and o.get("aiTitle"):
                            title = str(o["aiTitle"])
                        elif o.get("type") == "summary" and o.get("summary"):
                            title = str(o["summary"])
                    if title:
                        continue                                            
                    if o.get("type") == "user" and not o.get("isMeta"):
                        text = _text_of((o.get("message") or {}).get("content")).strip()
                        if text and not text.startswith("<"):
                            title = text.splitlines()[0][:80]
        except OSError:
            continue                                                    
        mtime = path.stat().st_mtime
        last_ts, tail_title = _tail_facts(path)
        out.append(SessionSummary(
            host="claude-code",
            session_id=path.stem,
            title=tail_title or title or f"{path.stem[:8]}…",
            project=project or _decode_project_dir(pd_name),
            started_at=started,
            last_at=last_ts or _iso(mtime),
            message_count=0,                                               
                                                                           
            source_path=str(path),
        ))
    return out


def _tail_facts(path: Path) -> tuple[str | None, str | None]:
    """(last timestamp, latest title) from the file's tail - cheaper than a
    full scan, and where ai-title lines usually land (written as the
    conversation earns a title)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 65_536))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return None, None
    last_ts = title = None
    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if last_ts is None and isinstance(o.get("timestamp"), str):
            last_ts = o["timestamp"]
        if title is None and o.get("type") == "ai-title" and o.get("aiTitle"):
            title = str(o["aiTitle"])
        if title is None and o.get("type") == "summary" and o.get("summary"):
            title = str(o["summary"])
        if last_ts is not None and title is not None:
            break
    return last_ts, title


def _iso(mtime: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        mtime, tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(home: Path, session_id: str) -> SessionView | None:
    projects = _projects_dir(home)
    if not projects.is_dir():
        return None
                                                                    
                                                     
    target = None
    for pd_name, path in _iter_session_files(projects):
        if path.stem == session_id:
            target = (pd_name, path)
            break
    if target is None:
        return None
    pd_name, path = target

    messages: list[MessageView] = []
    pending: dict[str, ToolCallView] = {}                            
    bad = 0
    title = ""
    project: str | None = None
    started = last = None

    try:
        fh = open(path, encoding="utf-8", errors="replace")
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
            ts = o.get("timestamp") if isinstance(o.get("timestamp"), str) else None
            if ts:
                started = started or ts
                last = ts
            if project is None and isinstance(o.get("cwd"), str):
                project = o["cwd"]
            if o.get("type") == "ai-title" and o.get("aiTitle"):
                title = str(o["aiTitle"])                               
            elif o.get("type") == "summary" and o.get("summary"):
                title = str(o["summary"])                                

            t = o.get("type")
            if t == "assistant":
                if o.get("isSidechain"):
                    continue                                           
                msg = o.get("message") or {}
                m = MessageView(role="assistant", ts=ts)
                think, texts = [], []
                for b in _blocks(msg):
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "thinking" and b.get("thinking"):
                        think.append(b["thinking"])
                    elif bt == "text" and b.get("text"):
                        texts.append(b["text"])
                    elif bt == "tool_use":
                        card = ToolCallView(
                            name=str(b.get("name") or "?"),
                            summary=_summarize_tool(b.get("name"), b.get("input")),
                            input_text=_pretty_input(b.get("input")))
                        m.tool_calls.append(card)
                        if isinstance(b.get("id"), str):
                            pending[b["id"]] = card
                if think or texts or m.tool_calls:
                    m.thinking = "\n".join(think) or None
                    m.text = "\n\n".join(texts)
                    m.model = (msg.get("model")
                               if isinstance(msg.get("model"), str) else None)
                    m.usage = _usage_of(msg)
                    messages.append(m)

            elif t == "user":
                if o.get("isMeta") or o.get("isSidechain"):
                    continue                                              
                msg = o.get("message") or {}
                content = msg.get("content")
                results = [b for b in _blocks(msg)
                           if isinstance(b, dict) and b.get("type") == "tool_result"]
                if results:
                    for b in results:
                        card = pending.pop(b.get("tool_use_id"), None)
                        if card is None:
                            continue                                          
                                                                                       
                        card.output = _text_of(b.get("content")) or None
                        card.is_error = bool(b.get("is_error"))
                    continue
                text = _text_of(content).strip()
                if text:
                    messages.append(MessageView(role="user", ts=ts, text=text))

    if not title:
        title = next((m.text.splitlines()[0][:80] for m in messages
                      if m.role == "user" and m.text.strip()
                      and not m.text.lstrip().startswith("<")), "")
    return SessionView(
        host="claude-code",
        session_id=session_id,
        title=title or f"{session_id[:8]}…",
        project=project or _decode_project_dir(pd_name),
        started_at=started,
        last_at=last,
        messages=messages,
        bad_lines=bad,
        source_path=str(path),
    )
