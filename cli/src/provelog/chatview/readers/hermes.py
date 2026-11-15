"""Hermes Agent reader: ~/.hermes/state.db (SQLite).

Hermes persists every session into one SQLite database (schema and write
path: agent/session_persistence.py, hermes_state_common.py SCHEMA_SQL;
schema_version 30):

  sessions: id 'YYYYMMDD_HHMMSS_<hex>', display_name/title, cwd, unix-float
            started_at/last_activity_at, message_count, hidden/archived
            + (newer schemas) model, input/output/cache_read/cache_write/
            reasoning token totals on the row
  messages: one row per message, OpenAI shape - role, content,
            tool_calls (JSON: [{id, function:{name, arguments}}]),
            tool_call_id + tool_name on role='tool' result rows,
            reasoning (thinking), unix-float timestamp
  session_model_usage: one row per (session, model, task) with token
            counts and first_seen/last_seen - so a session that switched
            models lists them all

Models are SESSION-level here (no per-message model exists), so the reader
fills SessionView.models (the sessions row's model first, then every model
session_model_usage names, in first-use order) instead of per-message
bylines - a session-level model is never passed off as a per-message one.
Cost/billing columns are deliberately not selected: the viewer shows
tokens, not money. reasoning_tokens has no slot in the canonical usage
shape (input/output/cache_read/cache_write/total) and stays unshown
rather than re-labeled as something it is not.

The database is opened with SQLite's read-only URI mode - the viewer can
physically not write to it. Timestamps are unix floats stored as text, so
ordering goes through CAST(... AS REAL) and values convert to ISO for the
unified format.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

from ..model import MessageView, SessionSummary, SessionView, ToolCallView, usage_dict

_DB = ("state.db",)

                                                                      
                                                                          
_SESS_EXTRA_COLS = ("model", "input_tokens", "output_tokens",
                    "cache_read_tokens", "cache_write_tokens")


def install_dir(home: Path) -> Path:
    """The directory whose existence marks this CLI as installed (this
    host documents no config-dir environment variable - the standard
    home-relative path is the only official location)."""
    return home / ".hermes"


def data_dir(home: Path) -> Path:
    return home / ".hermes"


def _db_path(home: Path) -> Path:
    return data_dir(home).joinpath(*_DB)


def _connect(home: Path) -> sqlite3.Connection | None:
    """Read-only connection, or None when there is nothing to open (no
    database, or unreadable - an absent host is not an error)."""
    p = _db_path(home)
    if not p.is_file():
        return None
    try:
        return sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None


def _iso(value) -> str | None:
    """unix-float (text or number) -> ISO 8601 UTC."""
    try:
        return datetime.datetime.fromtimestamp(float(value),
                                               tz=datetime.UTC
                                               ).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _display_name(row) -> str:
    id_, display, title, cwd = row[0], row[1], row[2], row[3]
    return display or title or (cwd or "").rstrip("/").split("/")[-1] or id_


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,)).fetchone()
    return row is not None


def _order_models(primary, pairs: list) -> list[str]:
    """The session's models: the sessions row's own model first (the
    primary), then the usage table's models in first-use order."""
    out: list[str] = []
    if isinstance(primary, str) and primary.strip():
        out.append(primary)
    for model, _first in sorted(pairs, key=lambda p: (p[1] is None,
                                                     p[1] or 0.0, str(p[0]))):
        if isinstance(model, str) and model.strip() and model not in out:
            out.append(model)
    return out


def _usage_total(vals: dict) -> dict | None:
    """Canonical session totals from the sessions row's token columns."""
    return usage_dict({
        "input": vals.get("input_tokens"),
        "output": vals.get("output_tokens"),
        "cache_read": vals.get("cache_read_tokens"),
        "cache_write": vals.get("cache_write_tokens"),
    })


def discover(home: Path) -> list[SessionSummary]:
    con = _connect(home)
    if con is None:
        return []
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
        extra = [c for c in _SESS_EXTRA_COLS if c in cols]
        rows = con.execute(
            "SELECT id, display_name, title, cwd, started_at, "
            "       last_activity_at, message_count"
            + (", " + ", ".join(extra) if extra else "") +
            " FROM sessions WHERE hidden = 0 "
            "ORDER BY CAST(COALESCE(last_activity_at, started_at) AS REAL) DESC"
        ).fetchall()
        per_model: dict[str, list] = {}
        has_usage = _has_table(con, "session_model_usage")
        if has_usage:
            for sid, model, first in con.execute(
                    "SELECT session_id, model, MIN(first_seen) "
                    "FROM session_model_usage GROUP BY session_id, model"):
                per_model.setdefault(sid, []).append((model, first))
    except sqlite3.Error:
        return []
    finally:
        con.close()
    model_col = "model" in cols
    out = []
    for r in rows:
        vals = dict(zip(extra, r[7:], strict=False))
        out.append(SessionSummary(
            host="hermes",
            session_id=r[0],
            title=_display_name(r)[:80],
            project=r[3],
            started_at=_iso(r[4]),
            last_at=_iso(r[5]) or _iso(r[4]),
            message_count=int(r[6] or 0),
            source_path=str(_db_path(home)),
            models=_order_models(vals.get("model"), per_model.get(r[0], []))
            if (model_col or has_usage) else None,
            usage_total=_usage_total(vals),
        ))
    return out


def _summarize_args(name: str, arguments) -> str:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            raw = " ".join(arguments.split())
            return raw if len(raw) <= 120 else raw[:117] + "…"
    if isinstance(arguments, dict):
        for k in ("command", "cmd", "file_path", "path", "pattern", "url",
                  "query", "prompt", "description", "name", "skill"):
            v = arguments.get(k)
            if isinstance(v, str) and v.strip():
                v = " ".join(v.split())
                return v if len(v) <= 120 else v[:117] + "…"
    raw = json.dumps(arguments, ensure_ascii=False) if arguments is not None else ""
    return raw if len(raw) <= 120 else raw[:117] + "…"


def load(home: Path, session_id: str) -> SessionView | None:
    con = _connect(home)
    if con is None:
        return None
    try:
        sess_cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
        extra = [c for c in _SESS_EXTRA_COLS if c in sess_cols]
        sess = con.execute(
            "SELECT id, display_name, title, cwd, started_at, "
            "       last_activity_at"
            + (", " + ", ".join(extra) if extra else "") +
            " FROM sessions WHERE id = ?",
            (session_id,)).fetchone()
        if sess is None:
            return None
        vals = dict(zip(extra, sess[6:], strict=False))
        pairs: list = []
        has_usage = _has_table(con, "session_model_usage")
        if has_usage:
            pairs = con.execute(
                "SELECT model, MIN(first_seen) FROM session_model_usage "
                "WHERE session_id = ? GROUP BY model",
                (session_id,)).fetchall()
        models = (_order_models(vals.get("model"), pairs)
                  if ("model" in sess_cols or has_usage) else None)
                                                                           
        cols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
        tok_col = ", token_count" if "token_count" in cols else ""
        rows = con.execute(
            "SELECT role, content, tool_calls, tool_call_id, tool_name, "
            "       reasoning, timestamp" + tok_col + " FROM messages "
            "WHERE session_id = ? ORDER BY CAST(timestamp AS REAL), rowid",
            (session_id,)).fetchall()
                                                                           
                                                                           
                                                                           
                                                                         
                                                                      
                                                                          
                                                                    
        seen: set = set()
        unique: list = []
        for row in rows:
            if row in seen:
                continue
            seen.add(row)
            unique.append(row)
        rows = unique
    except sqlite3.Error:
        return None
    finally:
        con.close()

    messages: list[MessageView] = []
    pending: dict[str, ToolCallView] = {}
    bad = 0
    for row in rows:
        (role, content, tool_calls, tool_call_id, tool_name, reasoning,
         ts, *rest) = row
        t = _iso(ts)
        text = content if isinstance(content, str) else (content or "")

        if role == "assistant":
            m = MessageView(role="assistant", ts=t)
            think = reasoning if isinstance(reasoning, str) else None
                                                                          
                                                                     
            if rest and isinstance(rest[0], (int, float)) \
                    and not isinstance(rest[0], bool):
                m.usage = usage_dict({"total": rest[0]})
            if tool_calls:
                try:
                    calls = json.loads(tool_calls) if isinstance(tool_calls, str) \
                        else tool_calls
                except ValueError:
                    calls, bad = None, bad + 1
                for c in calls or []:
                    if not isinstance(c, dict):
                        continue
                    fn = c.get("function") or {}
                    card = ToolCallView(
                        name=str(fn.get("name") or tool_name or "?"),
                        summary=_summarize_args(fn.get("name"), fn.get("arguments")))
                    card.input_text = (fn.get("arguments")
                                       if isinstance(fn.get("arguments"), str) else
                                       json.dumps(fn.get("arguments"),
                                                  ensure_ascii=False, indent=2)
                                       if fn.get("arguments") is not None else None)
                    m.tool_calls.append(card)
                    if isinstance(c.get("id"), str):
                        pending[c["id"]] = card
            if think:
                m.thinking = think
            if isinstance(text, str) and text.strip():
                m.text = text
            if m.thinking or m.text or m.tool_calls:
                messages.append(m)

        elif role == "user":
            if isinstance(text, str) and text.strip():
                messages.append(MessageView(role="user", ts=t, text=text))

        elif role == "tool":
            card = pending.pop(tool_call_id or "", None)
            if card is not None:
                card.output = text if isinstance(text, str) and text.strip() else None
                                                                     

    return SessionView(
        host="hermes",
        session_id=session_id,
        title=_display_name(sess)[:80],
        project=sess[3],
        started_at=_iso(sess[4]),
        last_at=_iso(sess[5]) or _iso(sess[4]),
        messages=messages,
        bad_lines=bad,
        source_path=str(_db_path(home)),
        models=models,
        usage_total=_usage_total(vals),
    )
