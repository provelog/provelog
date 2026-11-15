"""Shared core for opencode-family session databases (opencode, kilo,
zcode), which all descend from the same storage layer: a SQLite database
whose session/message/part rows carry JSON payloads in `data` columns.

Verified against:
  - opencode: the real ~/.local/share/opencode/opencode.db
    (schema and payload shapes)
  - kilo: ~/.local/share/kilo/kilo.db - an opencode fork, same layout
  - zcode: ~/.zcode/cli/db/db.sqlite (source: github.com/zai-org/ZCode,
    apps/zcode-cli/packages/adapters/src/storage/session-store/rows.ts)

Shapes:
  session row: id (ses_…), directory (the real cwd), title, time_created/
    time_updated (ms epoch); parent_id set marks sub-agent sessions and
    time_archived set marks archived ones - both stay out of listings
  message.data: {"role": "user"|"assistant"|…, "time": {"created": ms},
    "modelID": "big-pickle", "tokens": {"total", "input", "output",
    "reasoning", "cache": {"read", "write"}}} - model/tokens on assistant
    rows (reasoning is a provider subset, not shown as its own line)
  session row also keeps authoritative running totals in tokens_input /
    tokens_output / tokens_cache_read / tokens_cache_write (newer
    schemas; selected only when the columns exist)
  part.data discriminators:
    text {"text"}                    message text (user prompts too)
    reasoning {"text"}               model thinking
    tool {"tool", "callID", "state": {"status": "pending"|"running"|
         "completed"|"error", "input": {…}, "output": str|object,
         "error": str, "title": str}}    - results are INLINE in state,
         no joining needed
    step-start / step-finish / snapshot / file / agent / timeline: step
         or UI bookkeeping, skipped

Bad payloads (invalid JSON) are skipped and counted, never fatal; every
connection is opened read-only (mode=ro) so a viewer bug cannot corrupt
the user's database.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..model import MessageView, SessionSummary, SessionView, ToolCallView, usage_dict


def _usage_of(tokens) -> dict | None:
    """message/session token payloads -> canonical keys."""
    if not isinstance(tokens, dict):
        return None
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    return usage_dict({
        "input": tokens.get("input"),
        "output": tokens.get("output"),
        "cache_read": cache.get("read"),
        "cache_write": cache.get("write"),
        "total": tokens.get("total"),
    })


def _connect(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)


def _iso(ms) -> str | None:
    if isinstance(ms, (int, float)) and ms > 0:
        import datetime
        return datetime.datetime.fromtimestamp(
            ms / 1000, tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _short(value) -> str | None:
    v = " ".join(value.split()) if isinstance(value, str) else (
        " ".join(str(x) for x in value) if isinstance(value, list) and
        all(isinstance(x, (str, int, float)) for x in value) else None)
    if v is None or not v.strip():
        return None
    return v if len(v) <= 120 else v[:117] + "…"


def _summarize(state: dict) -> str:
    title = _short(state.get("title"))
    if title:
        return title                                                 
    args = state.get("input")
    if isinstance(args, dict):
        for k in ("command", "cmd", "file_path", "filePath", "path",
                  "pattern", "url", "query", "prompt", "description",
                  "name", "skill"):
            v = _short(args.get(k))
            if v:
                return v
    raw = json.dumps(args, ensure_ascii=False) if args else ""
    return raw if len(raw) <= 120 else raw[:117] + "…"


def _output_text(output) -> str | None:
    if isinstance(output, str):
        return output or None
    if isinstance(output, dict):
        for k in ("output", "text", "content"):
            if isinstance(output.get(k), str):
                return output[k]
    if output is not None:
        try:
            return json.dumps(output, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(output)
    return None


def _tool_card(p: dict) -> ToolCallView:
    state = p.get("state") if isinstance(p.get("state"), dict) else {}
    status = state.get("status")
    output = _output_text(state.get("output"))
    if output is None and isinstance(state.get("error"), str):
        output = state["error"]
    return ToolCallView(
        name=str(p.get("tool") or "?"),
        summary=_summarize(state),
        input_text=(json.dumps(state.get("input"), ensure_ascii=False, indent=2)
                    if state.get("input") is not None else None),
        output=output,
        is_error=status == "error" or bool(state.get("error")),
    )


def _assemble(role: str, part_datas: list[str]) -> tuple[MessageView | None, int]:
    """One message from its parts' JSON strings (already ordered); also
    reports how many part payloads were too damaged to parse."""
    m = MessageView(role=role, ts=None)
    think, texts, bad = [], [], 0
    for raw in part_datas:
        try:
            p = json.loads(raw)
        except ValueError:
            bad += 1
            continue
        if not isinstance(p, dict):
            bad += 1
            continue
        pt = p.get("type")
        if pt == "text" and isinstance(p.get("text"), str) and p["text"]:
            texts.append(p["text"])
        elif pt == "reasoning" and isinstance(p.get("text"), str) and p["text"]:
            think.append(p["text"])
        elif pt == "tool":
            m.tool_calls.append(_tool_card(p))
                                                                         
    if not (think or texts or m.tool_calls):
        return None, bad
    m.thinking = "\n".join(think) or None
    m.text = "\n\n".join(texts)
    return m, bad


def discover_db(db: Path, host: str) -> list[SessionSummary]:
    if not db.is_file():
        return []
    out: list[SessionSummary] = []
    try:
        con = _connect(db)
        rows = con.execute(
            "SELECT id, title, directory, time_created, time_updated,"
            " (SELECT COUNT(*) FROM message m WHERE m.session_id = session.id)"
            " FROM session WHERE parent_id IS NULL AND time_archived IS NULL"
            " ORDER BY time_created DESC").fetchall()
        con.close()
    except sqlite3.Error:
        return []
    for sid, title, directory, created, updated, count in rows:
        out.append(SessionSummary(
            host=host,
            session_id=sid,
            title=str(title or sid)[:80],
            project=directory if isinstance(directory, str) else None,
            started_at=_iso(created),
            last_at=_iso(updated),
            message_count=int(count or 0),
            source_path=str(db),
        ))
    return out


def load_db(db: Path, host: str, session_id: str) -> SessionView | None:
    if not db.is_file():
        return None
    try:
        con = _connect(db)
                                                                        
                                                  
        cols = {r[1] for r in con.execute("PRAGMA table_info(session)")}
        tok_cols = [c for c in ("tokens_input", "tokens_output",
                                "tokens_cache_read", "tokens_cache_write")
                    if c in cols]
        tok_sql = (", " + ", ".join(tok_cols)) if tok_cols else ""
        row = con.execute(
            "SELECT title, directory, time_created, time_updated" + tok_sql +
            " FROM session WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            con.close()
            return None
        title, directory, created, updated, *toks = row
        msg_rows = con.execute(
            "SELECT id, data FROM message WHERE session_id = ?"
            " ORDER BY time_created, rowid", (session_id,)).fetchall()
        parts_by_msg: dict[str, list[str]] = {}
        for mid, pdata in con.execute(
                "SELECT message_id, data FROM part WHERE session_id = ?"
                " ORDER BY time_created, rowid", (session_id,)):
            parts_by_msg.setdefault(mid, []).append(pdata)
        con.close()
    except sqlite3.Error:
        return None

                                                                     
    canonical = {"tokens_input": "input", "tokens_output": "output",
                 "tokens_cache_read": "cache_read",
                 "tokens_cache_write": "cache_write"}
    usage_total = usage_dict({
        canonical[c]: v for c, v in zip(tok_cols, toks, strict=True)
        if v is not None}) if tok_cols else None

    m_bad = 0
    messages: list[MessageView] = []
    for mid, raw in msg_rows:
        try:
            d = json.loads(raw)
        except ValueError:
            m_bad += 1
            continue
        if not isinstance(d, dict) or d.get("role") not in ("user", "assistant"):
            continue                                                          
        m, bad = _assemble(d.get("role"), parts_by_msg.get(mid, []))
        m_bad += bad
        if m is not None:
            if isinstance(d.get("modelID"), str):
                m.model = d["modelID"]
            m.usage = _usage_of(d.get("tokens"))
            messages.append(m)

    title = str(title or "")
    if not title:
        title = next((m.text.splitlines()[0][:80] for m in messages
                      if m.role == "user" and m.text.strip()
                      and not m.text.lstrip().startswith("<")), "")
    return SessionView(
        host=host,
        session_id=session_id,
        title=title[:80] or session_id[:36],
        project=directory if isinstance(directory, str) else None,
        started_at=_iso(created),
        last_at=_iso(updated),
        messages=messages,
        bad_lines=m_bad,
        source_path=str(db),
        usage_total=usage_total,
    )
