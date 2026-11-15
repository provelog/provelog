"""Gemini CLI reader: ~/.gemini/tmp/<projectId>/chats/session-*.jsonl

Format facts (source: github.com/google-gemni/gemini-cli
packages/core/src/services/chatRecordingService.ts +
chatRecordingTypes.ts + utils/thoughtUtils.ts):

  - first line: ConversationRecord {sessionId, startTime, lastUpdated,
    summary?, kind: 'main'|'subagent'} (messages stay empty on disk)
  - then one MessageRecord per line: {id, timestamp, type:
    user|gemini|info|error|warning, content: PartListUnion (string |
    {text} | mixed array)}; type 'gemini' rows additionally carry
    toolCalls: [{id, name, args, result: PartListUnion|null, status,
    timestamp}] (results INLINE - no joining needed) and thoughts:
    [{subject, description, timestamp}]
  - metadata update lines {"$set": {...}} and rewinds
    {"$rewindTo": "<msgId>"} are bookkeeping, skipped
  - <projectId> is a slug resolved through ~/.gemini/projects.json
    (path -> id); subagent sessions live one dir deeper
    (chats/<parentSessionId>/) and are skipped for the main listing
"""

from __future__ import annotations

import json
from pathlib import Path

from ..model import MessageView, SessionSummary, SessionView, ToolCallView

_CHATS = ("tmp", None, "chats")                                   


def install_dir(home: Path) -> Path:
    """The directory whose existence marks this CLI as installed (this
    host documents no config-dir environment variable - the standard
    home-relative path is the only official location)."""
    return home / ".gemini"


def data_dir(home: Path) -> Path:
    return home / ".gemini"


def _chats_root(home: Path) -> Path:
    return data_dir(home).joinpath("tmp")


def _project_label(home: Path, project_id: str) -> str:
    """projectId is a registry slug; projects.json maps the real path to
    it. Invert for display; the raw slug is the honest fallback."""
    reg = data_dir(home) / "projects.json"
    try:
        doc = json.loads(reg.read_text(encoding="utf-8"))
        if isinstance(doc, dict):
            for path, pid in doc.items():
                if pid == project_id and isinstance(path, str):
                    return path
    except (OSError, ValueError):
        pass
    return project_id


def _short(value) -> str | None:
    v = " ".join(value.split()) if isinstance(value, str) else (
        " ".join(str(x) for x in value) if isinstance(value, list) and
        all(isinstance(x, (str, int, float)) for x in value) else None)
    if v is None or not v.strip():
        return None
    return v if len(v) <= 120 else v[:117] + "…"


def _summarize_args(args) -> str:
    if isinstance(args, dict):
        for k in ("command", "cmd", "file_path", "path", "pattern", "url",
                  "query", "prompt", "description", "name", "skill"):
            v = _short(args.get(k))
            if v:
                return v
    raw = json.dumps(args, ensure_ascii=False) if args else ""
    return raw if len(raw) <= 120 else raw[:117] + "…"


def _text(content) -> str:
    """PartListUnion: string | {text} | mixed array -> plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return content.get("text") or "" if isinstance(content.get("text"), str) else ""
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
        return "\n".join(x for x in parts if x)
    return ""


def _iter_chats(root: Path):
    """Top-level session files across all projects (subagent dirs are one
    level deeper and excluded)."""
    if not root.is_dir():
        return
    for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        chats = project_dir / "chats"
        if chats.is_dir():
            yield project_dir.name, chats
            continue
                                                                 
        if any(project_dir.glob("session-*.jsonl")):
            yield project_dir.name, project_dir


def _read_header(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                if isinstance(o, dict) and "sessionId" in o:
                    return o
                break
    except (OSError, ValueError):
        pass
    return {}


def _iso_mtime(path: Path) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        path.stat().st_mtime, tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def discover(home: Path) -> list[SessionSummary]:
    from . import is_receipt_file

    out: list[SessionSummary] = []
    for project_id, chats in _iter_chats(_chats_root(home)):
        for f in sorted(chats.glob("session-*.jsonl")):
            if is_receipt_file(f):                                         
                continue
            head = _read_header(f)
            out.append(SessionSummary(
                host="gemini",
                session_id=f.stem,
                title=(head.get("summary") or f.stem.replace("session-", ""))[:80],
                project=_project_label(home, project_id),
                started_at=head.get("startTime"),
                last_at=head.get("lastUpdated") or _iso_mtime(f),
                message_count=0,
                source_path=str(f),
            ))
    return out


def load(home: Path, session_id: str) -> SessionView | None:
    from . import is_receipt_file

    target = None
    for project_id, chats in _iter_chats(_chats_root(home)):
        for f in chats.glob("session-*.jsonl"):
            if is_receipt_file(f):
                continue
            if f.stem == session_id:
                target = (project_id, f)
                break
        if target:
            break
    if target is None:
        return None
    project_id, path = target

    messages: list[MessageView] = []
    bad = 0
    title = ""
    started = last = None

    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return None
    with fh:
        first = True
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
            if first:
                first = False
                if "sessionId" in o:                                       
                    started = o.get("startTime")
                    last = o.get("lastUpdated")
                    if o.get("summary"):
                        title = str(o["summary"])[:80]
                    continue
            if "$set" in o or "$rewindTo" in o:
                if "$set" in o and isinstance(o["$set"], dict) and o["$set"].get("summary"):
                    title = str(o["$set"]["summary"])[:80]
                continue                                                    

            t = o.get("type")
            ts = o.get("timestamp") if isinstance(o.get("timestamp"), str) else None
            text = _text(o.get("displayContent") or o.get("content"))
            if t == "user":
                if text.strip():
                    messages.append(MessageView(role="user", ts=ts, text=text))
                continue
            if t == "gemini":
                m = MessageView(role="assistant", ts=ts)
                thought = [f"**{th.get('subject', '')}** {th.get('description', '')}"
                           for th in (o.get("thoughts") or [])
                           if isinstance(th, dict)]
                if thought:
                    m.thinking = "\n\n".join(thought)
                for c in o.get("toolCalls") or []:
                    if not isinstance(c, dict):
                        continue
                    status = c.get("status")
                    m.tool_calls.append(ToolCallView(
                        name=str(c.get("name") or "?"),
                        summary=_summarize_args(c.get("args")),
                        input_text=json.dumps(c.get("args"), ensure_ascii=False,
                                              indent=2) if c.get("args") else None,
                        output=_text(c.get("result")) or None,
                        is_error=status not in (None, "ok", "success", "done",
                                                "completed", ""),
                    ))
                if text.strip():
                    m.text = text
                if m.thinking or m.text or m.tool_calls:
                    messages.append(m)
                                                                          

    if not title:
        title = next((m.text.splitlines()[0][:80] for m in messages
                      if m.role == "user" and m.text.strip()), "")
    return SessionView(
        host="gemini",
        session_id=session_id,
        title=title or session_id.replace("session-", "")[:36],
        project=_project_label(home, project_id),
        started_at=started,
        last_at=last,
        messages=messages,
        bad_lines=bad,
        source_path=str(path),
    )
