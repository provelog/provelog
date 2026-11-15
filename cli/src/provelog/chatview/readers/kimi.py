"""Kimi CLI reader: ~/.kimi/sessions/<md5(cwd)>/<session-uuid>/context.jsonl

Format facts (source: github.com/MoonshotAI/kimi-cli - session layout from
src/session/*.ts, line payload from the vendored kosong message library
packages/kosong/src/kosong/message.py, which the CLI writes via
Message.model_dump_json(exclude_none=True)):

  - one kosong Message per line:
      {"role": <str>, "content": <str | [parts]>, "tool_calls"?: [...],
       "tool_call_id"?: <str>}
    a single TextPart collapses to a bare string; part discriminators:
      text {"text"} / think {"think"} / image_url / audio_url / video_url
  - roles: user / assistant / tool / system, plus internal bookkeeping
    roles prefixed '_' (_system_prompt, _usage, _checkpoint) - skipped
  - tool calls are OpenAI-style on the assistant message:
      tool_calls: [{type: "function", id, function: {name, arguments(str)}}]
    results arrive as role "tool" with tool_call_id + text content
  - Message carries NO timestamp: session times come from the file's
    mtime (wire.jsonl has per-turn times but a second format to drift)
  - state.json beside the file: {"custom_title"?, "archived"?}
  - kimi.json at ~/.kimi maps work_dirs[].path; the <md5(cwd)> dir name
    is inverted through it for the project label (raw hash is the
    honest fallback)

Bad lines (invalid JSON, unknown shape) are skipped and counted, never
fatal.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..model import MessageView, SessionSummary, SessionView, ToolCallView, usage_dict


def install_dir(home: Path) -> Path:
    """The directory whose existence marks this CLI as installed (this
    host documents no config-dir environment variable - the standard
    home-relative path is the only official location)."""
    return home / ".kimi"


def data_dir(home: Path) -> Path:
    return home / ".kimi"


def _sessions_root(home: Path) -> Path:
    return data_dir(home) / "sessions"


def _project_label(home: Path, dir_name: str) -> str:
    """The session dir name is md5(cwd) (remote stores prefix it with the
    kaos name). Invert through kimi.json's work_dirs; fall back to the
    raw name rather than guess."""
    index = data_dir(home) / "kimi.json"
    candidates = [dir_name]
    if "_" in dir_name:
        candidates.append(dir_name.split("_", 1)[1])
    try:
        doc = json.loads(index.read_text(encoding="utf-8"))
        for wd in (doc.get("work_dirs") or [] if isinstance(doc, dict) else []):
            if not (isinstance(wd, dict) and isinstance(wd.get("path"), str)):
                continue
            digest = hashlib.md5(wd["path"].encode("utf-8")).hexdigest()
            if digest in candidates:
                return wd["path"]
    except (OSError, ValueError):
        pass
    return dir_name


def _iter_sessions(root: Path):
    """(md5_dir_name, session_dir) for every session; tolerates a missing
    or cluttered root."""
    if not root.is_dir():
        return
    for md_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for sdir in sorted(p for p in md_dir.iterdir() if p.is_dir()):
            yield md_dir.name, sdir


def _read_state(sdir: Path) -> dict:
    try:
        o = json.loads((sdir / "state.json").read_text(encoding="utf-8"))
        return o if isinstance(o, dict) else {}
    except (OSError, ValueError):
        return {}


def _parts(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]                         
    return [b for b in content or [] if isinstance(b, dict)]


def _text(content) -> str:
    return "\n".join(p for p in (b.get("text") for b in _parts(content)
                                 if b.get("type") == "text") if p)


def _short(value) -> str | None:
    v = " ".join(value.split()) if isinstance(value, str) else (
        " ".join(str(x) for x in value) if isinstance(value, list) and
        all(isinstance(x, (str, int, float)) for x in value) else None)
    if v is None or not v.strip():
        return None
    return v if len(v) <= 120 else v[:117] + "…"


def _summarize(arguments) -> str:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return _short(arguments) or ""
    if isinstance(arguments, dict):
        for k in ("command", "cmd", "file_path", "filePath", "path",
                  "pattern", "url", "query", "prompt", "description",
                  "name", "skill"):
            v = _short(arguments.get(k))
            if v:
                return v
    raw = json.dumps(arguments, ensure_ascii=False) if arguments else ""
    return raw if len(raw) <= 120 else raw[:117] + "…"


def _pretty_args(arguments) -> str | None:
    if arguments is None:
        return None
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return arguments or None
    try:
        return json.dumps(arguments, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(arguments)


def _iso_mtime(path: Path) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(
        path.stat().st_mtime, tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_user_text(path: Path) -> str:
    """Head scan for the title fallback (the conversation's first user
    turn); cheap and best-effort."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if isinstance(o, dict) and o.get("role") == "user":
                    text = _text(o.get("content")).strip()
                    if text and not text.startswith("<"):
                        return text.splitlines()[0][:80]
    except OSError:
        pass
    return ""


def discover(home: Path) -> list[SessionSummary]:
    out: list[SessionSummary] = []
    for dir_name, sdir in _iter_sessions(_sessions_root(home)):
        ctx = sdir / "context.jsonl"
        if not ctx.is_file():
            continue                                                 
                                                                         
        state = _read_state(sdir)
        if state.get("archived"):
            continue
        title = str(state.get("custom_title")) if state.get("custom_title") \
            else _first_user_text(ctx)
        stamp = _iso_mtime(ctx)
        out.append(SessionSummary(
            host="kimi",
            session_id=sdir.name,
            title=title[:80] or sdir.name[:36],
            project=_project_label(home, dir_name),
            started_at=stamp,
            last_at=stamp,
            message_count=0,
            source_path=str(ctx),
        ))
    return out


def load(home: Path, session_id: str) -> SessionView | None:
    target = None
    for dir_name, sdir in _iter_sessions(_sessions_root(home)):
        if sdir.name == session_id and (sdir / "context.jsonl").is_file():
            target = (dir_name, sdir / "context.jsonl")
            break
    if target is None:
        return None
    dir_name, path = target

    messages: list[MessageView] = []
    pending: dict[str, ToolCallView] = {}                              
    bad = 0

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
            role = o.get("role")

            if role == "tool":
                card = pending.pop(o.get("tool_call_id") or "", None)
                text = _text(o.get("content"))
                if card is not None and text:
                    card.output = text
                continue
            if role == "user":
                text = _text(o.get("content")).strip()
                if text:
                    messages.append(MessageView(role="user", ts=None, text=text))
                continue
            if role == "assistant":
                m = MessageView(role="assistant", ts=None)
                think, texts = [], []
                for b in _parts(o.get("content")):
                    bt = b.get("type")
                    if bt == "text" and b.get("text"):
                        texts.append(b["text"])
                    elif bt == "think" and b.get("think"):
                        think.append(b["think"])
                                                                      
                for c in o.get("tool_calls") or []:
                    if not isinstance(c, dict):
                        continue
                    fn = c.get("function") if isinstance(c.get("function"), dict) else {}
                    args = fn.get("arguments")
                    card = ToolCallView(
                        name=str(fn.get("name") or "?"),
                        summary=_summarize(args))
                    card.input_text = _pretty_args(args)
                    m.tool_calls.append(card)
                    if isinstance(c.get("id"), str):
                        pending[c["id"]] = card
                if think or texts or m.tool_calls:
                    m.thinking = "\n".join(think) or None
                    m.text = "\n\n".join(texts)
                    messages.append(m)
            elif role == "_usage":
                                                                       
                                                                        
                                                                        
                                                                     
                              
                c = o.get("content")
                if isinstance(c, dict) and isinstance(c.get("token_count"),
                                                      (int, float)) \
                        and not isinstance(c.get("token_count"), bool):
                    last = next((m for m in reversed(messages)
                                 if m.role == "assistant"), None)
                    if last is not None:
                        last.usage = usage_dict({"total": c["token_count"]})
                                                                           
                                           

    state = _read_state(path.parent)
    title = str(state.get("custom_title")) if state.get("custom_title") else ""
    if not title:
        title = next((m.text.splitlines()[0][:80] for m in messages
                      if m.role == "user" and m.text.strip()
                      and not m.text.lstrip().startswith("<")), "")
    stamp = _iso_mtime(path)
    return SessionView(
        host="kimi",
        session_id=session_id,
        title=title[:80] or session_id[:36],
        project=_project_label(home, dir_name),
        started_at=stamp,
        last_at=stamp,
        messages=messages,
        bad_lines=bad,
        source_path=str(path),
    )
