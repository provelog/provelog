"""Choosing which conversation `provelog export` writes.

Pure helpers over the rows from ``SessionStore.export_candidates()``; only
``choose`` does I/O (a prompt on stderr), so the policy is testable without a
terminal.
"""

from __future__ import annotations

import datetime as _datetime
import os
import sys
from collections.abc import Iterable
from typing import Any

                                                                          
PICKER_LIMIT = 5

_AGENT_LABELS = {
    "dsh": "DeepSeek Harness",
    "claude-code": "Claude Code",
    "codex": "Codex",
    "hermes": "Hermes",
    "kilo": "Kilo",
    "pi": "pi",
    "openclaw": "OpenClaw",
    "zcode": "ZCode",
    "workbuddy": "WorkBuddy",
    "codebuddy": "CodeBuddy",
    "qoder": "Qoder",
    "gemini": "Gemini CLI",
    "copilot": "GitHub Copilot CLI",
    "cline": "Cline",
    "roo-code": "Roo Code",
    "opencode": "OpenCode",
    "qwen-code": "Qwen Code",
    "kimi": "Kimi Code",
    "omp": "omp",
    "minimax": "MiniMax Code",
    "doubao-work": "Doubao Work",
    "doubao": "Doubao",
}


def agent_key(value: str, rows=()) -> str | None:
    names = {k.casefold(): k for k in _AGENT_LABELS}
    names.update({v.casefold(): k for k, v in _AGENT_LABELS.items()})
    names["claude"] = "claude-code"
    for row in rows:
        key = str(row.get("agent") or "")
        if key:
            names[key.casefold()] = key
    return names.get(value.strip().casefold())


def agent_label(agent: str) -> str:
    return _AGENT_LABELS.get(agent, agent)


def _real(path: str) -> str:
    try:
        return os.path.realpath(os.path.expanduser(path))
    except (OSError, ValueError):
        return path


def usable_project(project: str) -> bool:
    """False for values that say nothing about a project: empty, the filesystem
    root, the home directory, and temp directories."""
    if not project:
        return False
    real = _real(project)
    if real in ("/", _real("~")):
        return False
    return not real.startswith(
        ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
    ) and real not in ("/tmp", "/private/tmp")


def project_matches(cwd: str, project: str) -> bool:
    """True when `cwd` is the project directory or lies inside it. Only that
    direction: standing in a parent of many projects selects none of them."""
    if not usable_project(project):
        return False
    c, p = _real(cwd), _real(project)
    return c == p or c.startswith(p.rstrip("/") + "/")


def order_candidates(cands: list[dict[str, Any]], cwd: str, pinned: str = "") -> list[dict[str, Any]]:
    """Rows annotated with ``this_project`` / ``current``; this project's first,
    each group newest first. Never filters: no match leaves the full list."""
    matches = [
        _real(str(c.get("project") or "")) for c in cands if project_matches(cwd, str(c.get("project") or ""))
    ]
    nearest = max(matches, key=len) if matches else None
    rows = []
    for c in cands:
        row = dict(c)
        row["this_project"] = bool(nearest and _real(str(c.get("project") or "")) == nearest)
        row["current"] = bool(pinned) and c.get("stream_id") == pinned
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("last_activity") or ""), reverse=True)
    rows.sort(key=lambda r: not r["this_project"])                              
    return rows


                                                                                


def _row_date(row: dict[str, Any]) -> _datetime.date | None:
    raw = str(row.get("last_activity") or "")
    try:
        return _datetime.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def filter_rows(
    rows: list[dict[str, Any]],
    *,
    agents: Iterable[str] = (),
    project: str | None = None,
    since: _datetime.date | None = None,
    until: _datetime.date | None = None,
    search: str | None = None,
    stamped: bool | None = None,
) -> list[dict[str, Any]]:
    """Keep the rows matching every given filter. Unknown values never match.

    ``agents`` accepts the internal key or the display label (``dsh`` or
    ``DeepSeek Harness``). ``project`` keeps sessions under that directory.
    ``since``/``until`` are inclusive dates over ``last_activity``.
    ``stamped`` True keeps fully stamped rows, False keeps the rest.
    """
    wanted = {a.strip().lower() for a in agents if a and a.strip()}
    needle = (search or "").strip().lower()
    out: list[dict[str, Any]] = []
    for row in rows:
        if wanted:
            names = {str(row.get("agent") or "").lower(), agent_label(str(row.get("agent") or "")).lower()}
            if not (names & wanted):
                continue
        if project and not project_matches(project, str(row.get("project") or "")):
            continue
        if since is not None or until is not None:
            day = _row_date(row)
            if day is None:
                continue
            if since is not None and day < since:
                continue
            if until is not None and day > until:
                continue
        if needle:
            hay = " ".join(str(row.get(k) or "") for k in ("title", "session_id", "stream_id")).lower()
            if needle not in hay:
                continue
        if stamped is True:
            if int(row.get("stamped") or 0) < int(row.get("records") or 0):
                continue
        elif stamped is False:
            if int(row.get("stamped") or 0) >= int(row.get("records") or 0):
                continue
        out.append(row)
    return out


def sort_rows(rows: list[dict[str, Any]], key: str = "recent") -> list[dict[str, Any]]:
    """Return a new list ordered by `key`; unknown keys fall back to recent."""
    out = list(rows)
    out.sort(key=lambda r: str(r.get("last_activity") or ""), reverse=True)
    if key == "records":
        out.sort(key=lambda r: int(r.get("records") or 0), reverse=True)
    elif key == "agent":
        out.sort(key=lambda r: agent_label(str(r.get("agent") or "")).lower())
    elif key == "project":
        out.sort(key=lambda r: str(r.get("project") or "").lower())
    if key == "recent":
        out.sort(key=lambda r: not r.get("this_project", False))
    return out


def group_rows(rows: list[dict[str, Any]], by: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group in first-seen order; `by` is "agent" or "project"."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if by == "project":
            key = str(row.get("project") or "") or "(no project)"
        else:
            key = agent_label(str(row.get("agent") or "")) or "(unknown agent)"
        groups.setdefault(key, []).append(row)
    return list(groups.items())


                                                                                


def _title(row: dict[str, Any], width: int = 48) -> str:
    title = " ".join(str(row.get("title") or "Untitled conversation").split())
    return title if len(title) <= width else title[: width - 1] + "…"


def _when(iso: str) -> str:
    try:
        dt = _datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except (ValueError, TypeError):
        return "time unknown"
    now = _datetime.datetime.now().astimezone()
    seconds = max(0, (now - dt).total_seconds())
    days = (now.date() - dt.date()).days
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if days == 0:
        return f"{int(seconds // 3600)} h ago"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} d ago"
    return dt.strftime("%Y-%m-%d")


def turns(row):
    n = row.get("turns")
    return "—" if n is None else f"{n} " + ("turn" if n == 1 else "turns")


def describe(row: dict[str, Any]) -> str:
    detail = agent_label(str(row.get("agent") or "unknown agent"))
    if row.get("turns") is not None:
        detail += ", " + turns(row)
    return f"{_title(row)} ({detail})"


def render_line(index: int, row: dict[str, Any]) -> str:
    tail = "   [fork]" if row.get("fork_of") else ""
    line = (
        f"  {index:>2}. {_title(row)}  {agent_label(str(row.get('agent') or ''))}  "
        f"{turns(row)}  {_when(str(row.get('last_activity') or ''))}{tail}"
    )
    if row.get("project"):
        line += "\n     " + " ".join(str(row["project"]).split())
    return line


def render_summary(rows):
    return f"{len(rows)} " + ("conversation" if len(rows) == 1 else "conversations")


def render_list(
    rows: list[dict[str, Any]],
    limit: int | None = None,
    *,
    with_stream: bool = False,
    group_by: str | None = None,
) -> str:
    shown = rows if limit is None else rows[:limit]
    lines: list[str] = []
    index = 0
    if group_by:
        for label, group in group_rows(shown, group_by):
            if lines:
                lines.append("")
            lines.append(f"{label} — {len(group)} conversations")
            for row in group:
                index += 1
                lines.append(render_line(index, row))
                if with_stream:
                    lines.append(f"      stream: {row['stream_id']}")
    else:
        for row in shown:
            index += 1
            lines.append(render_line(index, row))
            if with_stream:
                lines.append(f"      stream: {row['stream_id']}")
    if limit is not None and len(rows) > limit:
        lines.append(f'  ... {len(rows) - limit} more: run "provelog export --list"')
    return "\n".join(lines)


                                                                                


def parse_selection(text: str, n: int) -> list[int] | None:
    """0-based indices for a picker answer, or None when the answer is invalid.

    "" -> [0]; "q"/"quit" -> []; "a"/"all" -> every index; "1,3,5-7" -> those.
    """
    raw = text.strip().lower()
    if raw == "":
        return [0] if n else []
    if raw in ("q", "quit"):
        return []
    if raw in ("a", "all"):
        return list(range(n))
    picks: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            return None
        if not part.isdigit():
            return None
        value = int(part)
        if value < 1 or value > n:
            return None
        picks.append(value - 1)
    return sorted(set(picks))


def choose(rows, *, interactive, agent=None, on_all=None):
    if not interactive:
        if len(rows) == 1:
            return rows, 0
        label = f"{agent_label(agent)} " if agent else ""
        print(
            f"Multiple {label}conversations are available.\n\n"
            "Run this command in an interactive terminal to choose,\n"
            "or use --stream <id> to select one explicitly.\n"
            'Run "provelog export --list" to find stream IDs.\n\nNo file was written.',
            file=sys.stderr,
        )
        return [], 2
    page = 0
    while True:
        start = page * PICKER_LIMIT
        shown = rows[start : start + PICKER_LIMIT]
        label = f"{agent_label(agent)} " if agent else ""
        print(
            f"\nChoose {label}conversations to export\n"
            if len(rows) != 1
            else "\nChoose a conversation to export\n",
            file=sys.stderr,
        )
        for i, row in enumerate(shown, start + 1):
            print(render_line(i, row) + "\n", file=sys.stderr)
        order = "current project first" if any(r.get("this_project") for r in rows) else "newest first"
        count = (
            f"Showing {start + 1}–{start + len(shown)} of {len(rows)}"
            if len(rows) > PICKER_LIMIT
            else render_summary(rows)
        )
        print(count + " · " + order + "\n", file=sys.stderr)
        options = []
        if page:
            options.append("[p] previous page")
        if start + PICKER_LIMIT < len(rows):
            options.append("[n] next page")
        if len(rows) > 1:
            options.append(f"[a] all {len(rows)}")
        if options:
            print(" · ".join(options), file=sys.stderr)
        print("[b] browse in browser · [q] cancel\n", file=sys.stderr)
        print(
            f"Select [{start + 1}] — enter a number or several, such as 1,3: ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = "q"
        if answer == "n" and start + PICKER_LIMIT < len(rows):
            page += 1
            continue
        if answer == "p" and page:
            page -= 1
            continue
        if answer == "b":
            return [], -1
        if answer == "":
            answer = str(start + 1)
        picks = parse_selection(answer, len(rows))
        if picks is None:
            print(
                "Invalid selection.\n"
                "Enter a listed number, numbers separated by commas, or one of the options.",
                file=sys.stderr,
            )
            continue
        if not picks:
            print("Export cancelled.\nNo file was written.", file=sys.stderr)
            return [], 1
        if answer in ("a", "all") and on_all:
            on_all()
        return [rows[i] for i in picks], 0
