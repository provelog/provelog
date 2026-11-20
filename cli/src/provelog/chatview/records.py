"""The viewer's data source: the evidence store, not the hosts' transcripts.

``provelog chat`` reads ``~/ProveLog Records`` (or ``PROVELOG_STORE``): one
folder per conversation, ``trajectory.jsonl.zst`` as the truth. A session the
recorder never saw does not appear here - the viewer and the evidence now have
exactly one source, so "stamped" and "exportable" can never disagree.

Records are the signed event stream (``user_message`` / ``assistant_message`` /
``tool_call`` / ``tool_result`` / ``compaction``). This module folds them into
the page's view model (``model.SessionView``): one message per user/assistant
turn, tool calls joined to their results by call id, injected runtime context
filtered out of the conversation (it is preserved in the record itself).
"""

from __future__ import annotations

import json
import ntpath
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import GENESIS_PREV_HASH
from ..canonical import canonicalize, sha256_hex
from ..codex_context import is_internal_user_text
from . import view_state
from .model import MessageView, ToolCallView, usage_dict

_DEFAULT_ROOT = "ProveLog Records"

                                                                             
                                                                             
                                                                               
_INJECTED_PREFIXES = ("Current runtime context", "<system-reminder>",
                      "Time sampled while preparing",
                      "This session is being continued from a previous conversation",
                      "# Files mentioned by the user:",
                      "Files mentioned by the user:",
                      "The following is the Codex agent history",
                      "[Request interrupted by user]",
                      "(not recorded - this part of the conversation happened before")

                                                          
                                                                             
                                                              
_MODEL_LABELS = {
    ("deepseek-account", "deepseek-flash"): ("DeepSeek-V4.1-Flash", "1017"),
    ("deepseek-official", "deepseek-flash"): ("DeepSeek-V4.1-Flash", "1017"),
}


def model_label(provider: str | None, model: str | None) -> tuple[str, str]:
    if (str(provider or ""), str(model or "")) in _MODEL_LABELS:
        return _MODEL_LABELS[(str(provider or ""), str(model or ""))]
    return (str(model or ""), "")


_USAGE_KEYS = {
    "inputTokens": "input", "outputTokens": "output",
    "cacheReadTokens": "cache_read", "cacheWriteTokens": "cache_write",
    "input": "input", "output": "output",
    "cache_read": "cache_read", "cache_write": "cache_write",
}


def store_root(home: Path) -> Path:
    """The store this home's recorder writes: PROVELOG_STORE, then the config
    file's ``store``, then ``~/ProveLog Records`` (the recorder's own order)."""
    from ..store import resolve_store_root_for_home
    return resolve_store_root_for_home(home)


def _store(home: Path):
    from ..store import open_store

    return open_store(str(store_root(home)), read_only=True)


                                                                               

def _dir_bytes(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return total


def _rev(folder: Path | None) -> str:
    """A cheap fingerprint of everything a conversation's page is built from.

    Five ``stat`` calls, no reads. The page polls the list; when a row's
    ``rev`` equals the open conversation's, nothing on disk changed and the
    conversation is not re-read (decompress + parse + chain check). A new
    record, a new timestamp receipt, a title or an attachment all rewrite one
    of these files and so change the value.
    """
    if folder is None:
        return ""
    parts = []
    for name in ("manifest.json", "trajectory.jsonl.zst", "trajectory.jsonl",
                 "transcript-preview.md", "attachments"):
        try:
            st = (folder / name).stat()
            parts.append(f"{st.st_mtime_ns}.{st.st_size}")
        except OSError:
            parts.append("-")
    return "/".join(parts)


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _bare(session_id: str) -> str:
    raw = str(session_id or "")
    for prefix in ("session-", "session_", "sess-", "sess_"):
        if raw.lower().startswith(prefix) and len(raw) > len(prefix) + 4:
            return raw[len(prefix):]
    return raw


def _scratch_manifest(manifest: dict[str, Any]) -> bool:
    """Test/scratch imports must not appear as user conversations."""
    values = [str(manifest.get("source_path") or ""),
              str(manifest.get("imported_from") or "")]
    for value in values:
        if "/.dsh/sessions/--private-tmp-" in value or "/.dsh/sessions/--tmp-" in value:
            return True
    return False


def _activity_at(manifest: dict[str, Any]) -> str:
    """The conversation's own last event time, not the importer's wall clock.

    Older builds wrote ``last_activity = now`` on every manifest rewrite. The
    signed stream's checkpoints still carry the real times, so prefer them when
    the stored value is implausibly newer (for example, a session from days ago
    marked "just now").
    """
    stored = str(manifest.get("last_activity") or "")
    latest = str(manifest.get("created_at") or "")
    for stream in (manifest.get("streams") or {}).values():
        if not isinstance(stream, dict):
            continue
        for mark in stream.get("checkpoints") or []:
            at = str((mark or {}).get("at") or "")
            if at > latest:
                latest = at
    if not latest or not stored:
        return stored or latest
    try:
        from datetime import datetime
        delta = (datetime.fromisoformat(stored.replace("Z", "+00:00"))
                 - datetime.fromisoformat(latest.replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return stored
                                                                               
                                                      
    if 0 <= delta <= 3600:
        return stored
    return latest


def _mtime_iso(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value), UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _manifest(folder: Path) -> dict[str, Any]:
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _delivered(a: dict[str, Any]) -> bool:
    """A stamp that has an actual receipt, not queued and not a lost claim."""
    return bool(a.get("receipt")) and not a.get("pending") and not a.get("failed")


def _stamp(manifest: dict[str, Any]) -> str:
    anchors = [a for a in (manifest.get("anchors") or []) if isinstance(a, dict)]
    if any(_delivered(a) for a in anchors):
        return "stamped"
    if any(a.get("pending") for a in anchors):
        return "queued"
    return "unstamped"


def _anchor_belongs(manifest: dict[str, Any], anchor: dict[str, Any],
                    stream: str) -> bool:
    owner = str(anchor.get("stream_id") or "")
    if owner:
        return owner == stream
                                                                        
                                                                     
    return len(manifest.get("streams") or {}) <= 1


def _stamp_for_stream(manifest: dict[str, Any], stream: str) -> str:
    anchors = [a for a in (manifest.get("anchors") or [])
               if isinstance(a, dict) and _anchor_belongs(manifest, a, stream)]
    if any(_delivered(a) for a in anchors):
        return "stamped"
    if any(a.get("pending") for a in anchors):
        return "queued"
    return "unstamped"


def _stamp_coverage(manifest: dict[str, Any], stream: str) -> tuple[int, bool]:
    anchors = [a for a in (manifest.get("anchors") or [])
               if isinstance(a, dict) and _anchor_belongs(manifest, a, stream)]
    stamped = [a for a in anchors if _delivered(a)]
    covered = max((int(a.get("covers_up_to") or 0) for a in stamped), default=0)
    total = int(((manifest.get("streams") or {}).get(stream) or {}).get("count") or 0)
    return covered, bool(stamped) and covered < total


_CHILD_CACHE: dict[str, dict[str, Any]] = {}


def _token_total(events: list[dict[str, Any]]) -> int:
    """Total tokens per model call, never double-counting total + components."""
    total = 0
    for event in events:
        data = event.get("data") or {}
        usage = data.get("usage")
        if not isinstance(usage, dict):
            usage = event.get("payload", {}).get("usage") if isinstance(event.get("payload"), dict) else None
        if not isinstance(usage, dict):
            usage = ((data.get("message") or {}).get("usage") or {})
        if not isinstance(usage, dict):
            continue
        value = usage.get("totalTokens") or usage.get("total_tokens")
        if value is None:
            value = sum(int(usage.get(key) or 0) for key in
                        ("inputTokens", "outputTokens",
                         "cacheReadTokens", "cacheWriteTokens"))
        try:
            total += int(value or 0)
        except (TypeError, ValueError):
            pass
    return total


def _child_meta(store, child_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    """DSH metadata for a child chain: title, kind, seed and direct parent."""
    source = str(entry.get("source_path") or "")
    if not source:
        try:
            source = str(store.session_capture(child_id).get("source_path") or "")
        except Exception:
            source = ""
    if not source:
        return {"is_fork": False, "seeded": False, "depth": 0,
                "title": "", "parent_session": "", "created_at": 0,
                "cwd": "", "tokens": 0, "duration_s": 0, "status": "unknown"}
    try:
        stat = Path(source).stat()
        key = f"{source}:{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        key = f"{source}:missing"
    cached = _CHILD_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from .. import capture, import_dsh
        header, events = import_dsh.read_log(Path(source))
        facts = capture.dsh_session_meta(events, header)
        created_ms = int(header.get("createdAt") or 0)
        last_ms = max((int(e.get("time") or 0) for e in events), default=0)
        source_mtime = float(entry.get("source_mtime")
                             or Path(source).stat().st_mtime)
        duration_s = max(0, int(((last_ms or int(source_mtime * 1000)) - created_ms) / 1000))
        value = {
            "is_fork": bool(facts.get("is_fork")),
            "seeded": bool(header.get("isSeeded")),
            "depth": int(header.get("delegationDepth") or 0),
            "title": str(facts.get("title") or ""),
            "parent_session": str(facts.get("parent_session") or ""),
            "created_at": created_ms,
            "cwd": str(header.get("cwd") or ""),
            "tokens": _token_total(events),
            "duration_s": duration_s,
                                                                             
            "status": "unknown",
        }
    except Exception:
        value = {"is_fork": False, "seeded": False, "depth": 0,
                 "title": "", "parent_session": "", "created_at": 0,
                 "cwd": "", "tokens": 0, "duration_s": 0, "status": "unknown"}
    if len(_CHILD_CACHE) >= 256:
        _CHILD_CACHE.clear()
    _CHILD_CACHE[key] = value
    return value


def _nested_fork(store, child_id: str, entry: dict[str, Any]) -> bool:
    return bool(_child_meta(store, child_id, entry).get("is_fork"))


def _child_row(store, folder: Path, manifest: dict[str, Any], child_id: str,
               entry: dict[str, Any]) -> dict[str, Any]:
    meta = _child_meta(store, child_id, entry)
    parent_id = str(manifest.get("session_id") or "")
    parent_title = str(manifest.get("display_title") or manifest.get("title")
                       or parent_id)
    stream = f"local-dsh-{_bare(child_id)}"
    count = int((entry or {}).get("records") or 0)
    stamp = _stamp_for_stream(manifest, stream)
    covered, partial = _stamp_coverage(manifest, stream)
    last = (_mtime_iso((entry or {}).get("source_mtime"))
            or manifest.get("last_activity") or "")
    kind = "fork" if meta["is_fork"] else "subagent"
    title = meta["title"] or ""
    return {
        "session_id": str(child_id),
        "agent": str(manifest.get("agent") or folder.parent.name),
        "title": title,
        "kind": kind,
        "mode": "seeded" if meta["is_fork"] else "subagent",
        "project": _normalize_project(str(meta.get("cwd") or "")),
        "seeded_prefix": bool(meta["seeded"]),
        "delegation_depth": int(meta["depth"]),
        "tokens": int(meta.get("tokens") or 0),
        "duration_s": int(meta.get("duration_s") or 0),
        "status": str(meta.get("status") or "unknown"),
        "records": count,
        "turns": 0,
        "tool_calls": 0,
        "started_at": (_mtime_iso(meta["created_at"] / 1000)
                       if meta.get("created_at") else ""),
        "created_at": (_mtime_iso(meta["created_at"] / 1000)
                       if meta["created_at"] else ""),
        "last_at": last,
        "stamp": stamp,
        "stamp_partial": partial,
        "stamp_covers": covered,
        "committed_to": _committed_to((manifest.get("streams") or {}).get(stream) or {}),
        "committed_at": str(manifest.get("last_checkpoint_at") or ""),
        "folder": str(folder),
        "rev": _rev(folder),
        "fork_of": parent_id if meta["is_fork"] else "",
        "parent_session": str(meta.get("parent_session") or ""),
        "parent_title": parent_title if meta.get("parent_session") == parent_id else "",
        "nested_child": True,
        "nested_fork": meta["is_fork"],
        "children": [],
    }


def _preview_title(folder: Path, session_id: str = "") -> str:
    """A display-only hint for a conversation whose host title has not arrived.

    The transcript preview is generated from the signed records and starts
    with the first user input. It is not written back into the manifest and
    is never treated as the host title; it only keeps the sidebar from showing
    a raw session id until the next watch pass supplies the real title.
    """
    try:
        lines = (folder / "transcript-preview.md").read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    heading = ""
    for line in lines:
        if line.startswith("# "):
            heading = line[2:].strip()
            break
                                                                           
                                                                            
    own_ids = {session_id, _bare(session_id)} - {""}
    if (heading
            and heading not in own_ids
            and not heading.startswith(("session-", "sessionid-", "sess-"))
            and not re.fullmatch(r"[0-9a-fA-F-]{32,36}", heading)
            and not is_internal_user_text(heading)):
        return heading[:100]
    for index, line in enumerate(lines):
        if line.strip() != "**Input**":
            continue
        block: list[str] = []
        for nxt in lines[index + 1:]:
            if nxt.startswith("**", 0):
                break
            if nxt.strip():
                block.append(nxt.strip())
        text = "\n".join(block).strip()
        if text and not is_internal_user_text(text):
            return text.splitlines()[0][:100]
    return ""


def _number_child_titles(rows: list[dict[str, Any]], parent_title: str) -> None:
    """Give children the same duplicate-title numbering as the sidebar."""
    seen = 0
    for row in rows:
        if not row.get("title"):
            continue
        if row.get("title") == parent_title:
            seen += 1
            row["title"] = f"{parent_title} ({seen})"
        _number_child_titles(row.get("children") or [], str(row.get("title") or ""))


def _children_tree(store, folder: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
                                                                             
                                                       
    if manifest.get("agent") != "dsh":
        return []
    rows = [_child_row(store, folder, manifest, str(cid), entry)
            for cid, entry in (manifest.get("child_sessions") or {}).items()]
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_parent.setdefault(str(row.get("parent_session") or ""), []).append(row)
    for row in rows:
        row["children"] = by_parent.get(str(row["session_id"]), [])
    parent_id = str(manifest.get("session_id") or "")
    return by_parent.get(parent_id, [])


_TSA_NAMES = (("sectigo", "Sectigo"), ("freetsa", "FreeTSA"),
              ("digicert", "DigiCert"), ("certum", "Certum"))


def _tsa_name(url: str | None) -> str:
    """A short, honest name for the authority that minted the stamp."""
    value = str(url or "")
    if not value:
        return ""
    low = value.lower()
    for needle, name in _TSA_NAMES:
        if needle in low:
            return name
    return value.split("//", 1)[-1].split("/", 1)[0]


def _one_stamp(anchor: dict[str, Any]) -> dict[str, Any]:
    receipt = anchor.get("receipt") or {}
                                                            
                                                                            
                                                    
    if isinstance(receipt.get("receipt"), dict):
        receipt = receipt["receipt"]
    tsa_url = receipt.get("tsa_url") or receipt.get("tsa")
                                                                                 
                                                  
    named = str(receipt.get("provider") or "")
    return {"provider": named or (_tsa_name(tsa_url) if isinstance(tsa_url, str) else ""),
            "tsa_url": tsa_url if isinstance(tsa_url, str) else "",
            "covers_up_to": int(anchor.get("covers_up_to") or 0),
            "created_at": anchor.get("created_at"),
            "gen_time": receipt.get("gen_time") or receipt.get("genTime"),
            "pending": bool(anchor.get("pending")),
            "failed": bool(anchor.get("failed"))}


def _stamps(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Every stamp this conversation has, oldest coverage first.

    Coverage is a prefix (``covers_up_to`` records), so a later stamp always
    covers at least as much as an earlier one - but the later one may have
    been minted by a DIFFERENT authority (Sectigo when it works, FreeTSA as
    the failover). The list keeps that history; the last entry is the current
    proof, and the drawer shows both when the authorities differ.
    """
    rows = [_one_stamp(a) for a in (manifest.get("anchors") or [])
            if isinstance(a, dict) and (a.get("receipt") or a.get("pending"))]
    rows.sort(key=lambda s: (int(s["covers_up_to"]), str(s.get("created_at") or "")))
    return rows


def _stamps_for_stream(manifest: dict[str, Any], stream: str) -> list[dict[str, Any]]:
    rows = [_one_stamp(a) for a in (manifest.get("anchors") or [])
            if isinstance(a, dict) and _anchor_belongs(manifest, a, stream)
            and (a.get("receipt") or a.get("pending"))]
    rows.sort(key=lambda s: (int(s["covers_up_to"]), str(s.get("created_at") or "")))
    return rows


def _latest_stamp_for_stream(manifest: dict[str, Any],
                             stream: str) -> dict[str, Any] | None:
    issued = [s for s in _stamps_for_stream(manifest, stream)
              if not s["pending"] and not s["failed"]]
    if not issued:
        return None
    return issued[-1]


def _committed_to(stream_row: dict[str, Any]) -> int:
    """How many records this stream's last signed commitment fixes (0 if none)."""
    marks = [m for m in (stream_row.get("checkpoints") or []) if isinstance(m, dict)]
    return int(marks[-1].get("covers_up_to") or 0) if marks else 0


def _latest_stamp(manifest: dict[str, Any]) -> dict[str, Any] | None:
    issued = [s for s in _stamps(manifest) if not s["pending"] and not s["failed"]]
    if not issued:
        return None
    return issued[-1]


_PROJECT_CACHE: dict[str, str] = {}
_PREVIEW_PATH = re.compile(r"(/(?:Users|home|Volumes|private|opt|srv)/[^\s\"'`;,)]+)")


def _project_from_preview(folder: Path) -> str:
    """Last-resort workspace for a host that did not record a cwd.

    The preview is store-local (the viewer already reads it for titles). Only
    absolute paths that still exist under the user's home are considered, and
    the evidence-store path itself is excluded. This is a display fallback;
    it never rewrites the manifest.
    """
    try:
        text = (folder / "transcript-preview.md").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return ""
    home = str(Path.home())
    counts: dict[str, int] = {}
    for match in _PREVIEW_PATH.findall(text):
        path = match.rstrip(".,:")
        if (not path.startswith(home) or "ProveLog Records" in path
                or _is_scratch_project(path)):
            continue
        try:
            if Path(path).is_dir():
                counts[path] = counts.get(path, 0) + 1
        except OSError:
            continue
    if not counts:
        return ""
    return sorted(counts, key=lambda p: (-counts[p], len(p), p))[0]


def _is_scratch_project(project: str) -> bool:
    """Host-generated "no folder" workspaces are not projects.

    Codex App puts folder-less chats under ``~/Documents/Codex/<date>/...``;
    Claude's scratch workspaces live under its Application Support tree. They
    are implementation details, not names a person chose, so grouping by them
    creates one random workspace per conversation. Keep the raw path in the
    manifest, but let the viewer group these as "no project".
    """
    path = str(project or "").replace("\\", "/").rstrip("/")
    if re.search(
            r"/(?:private/)?tmp/(?:dsh|codex|claude|qoder|codebuddy|kimi|mini?max|"
            r"workbuddy|antigravity)", path, re.I):
        return True
    if re.search(r"/Documents/(?:Codex|Qoder)/\d{4}-\d{2}-\d{2}/", path):
        return True
    if re.search(r"/CodeBuddy/\d{8,}", path):
        return True
    if path.endswith("/default-workspace") or "/default-workspace/" in path:
        return True
    if "/scratch-workspaces/" in path:
        return True
    if "/Application Support/" in path and re.search(
            r"/(?:scratch|workspace|sandbox)(?:[-_/]|$)", path, re.I):
        return True
                                                                            
                                                                           
                                                                            
    if re.search(r"/(?:\.vscode(?:-server|-insiders)?)/", path):
        return True
    if re.search(r"/Visual Studio Code(?: - Insiders)?\.app/", path):
        return True
    if re.search(r"/Application Support/Code/User/workspaceStorage/", path):
        return True
    return False


def _normalize_project(project: str) -> str:
    """One grouping key for one Windows workspace.

    Windows paths are case-insensitive and accept both separators. Hosts record
    the same folder in different spellings (for example ``C:\\...\\23`` and
    ``c:\\...\\23``); the viewer must not turn that into two workspaces that
    both render as "23".
    """
    value = str(project or "").strip()
    if not value:
        return ""
    if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", value):
        return ntpath.normcase(ntpath.normpath(value))
    return value


def _project_for(store, folder: Path, manifest: dict[str, Any]) -> str:
    project = _normalize_project(str(manifest.get("project") or manifest.get("cwd") or ""))
    if project:
        return "" if _is_scratch_project(project) else project
    sid = str(manifest.get("session_id") or "")
    try:
        source = str(store.session_capture(sid).get("source_path") or "")
    except Exception:
        source = ""
    if source:
        cached = _PROJECT_CACHE.get(source)
        if cached is not None:
            return cached
        try:
            from .. import import_dsh
            header, _events = import_dsh.read_log(Path(source))
            project = _normalize_project(str(header.get("cwd") or ""))
        except Exception:
            project = ""
        if project:
            if _is_scratch_project(project):
                return ""
            _PROJECT_CACHE[source] = project
            return project
    folder_key = str(folder)
    cached = _PROJECT_CACHE.get(folder_key)
    if cached is not None:
        return cached
    project = _normalize_project(_project_from_preview(folder))
    if project:
        _PROJECT_CACHE[folder_key] = project
        return project
    return _normalize_project(folder.parent.name)


def _visible_user_turns(store: Any, stream: str) -> int | None:
    """Count user messages the viewer actually shows, not host context rows.

    Existing Codex captures can contain selected-page/environment snapshots
    (role=user) that the current importer filters. The manifest's
    coverage.user_messages was written before that filter and is stale; read
    the (small) host stream to keep the sidebar honest without mutating the
    signed records.
    """
    try:
        events = store.get_records(stream)
    except (OSError, TypeError, ValueError):
        return None
    count = 0
    for env in events:
        record = env.get("record") if isinstance(env, dict) else None
        if not isinstance(record, dict) or record.get("action_type") != "user_message":
            continue
        payload = record.get("payload") or {}
        text = _text_of(payload.get("content"))
        if text and not _is_injected(text):
            count += 1
    return count


def sessions(home: Path) -> list[dict[str, Any]]:
    """Every recorded conversation, newest activity first.

    List-shaped facts only: the title, who recorded it, how much, when, and
    whether it is stamped. Reading message bodies is ``load``'s job.
    """
    root = store_root(home)
    if not root.is_dir():
        return []
    from ..session_store import capture_tier_for
    from ..session_store import own_stream as _own_stream
    from ..session_store import own_stream_id as _own_stream_id

    store = _store(home)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    manifests: list[tuple[Path, dict[str, Any]]] = []
    for folder in sorted(root.glob("*/*")):
        manifest = _manifest(folder)
        has_events = ((folder / "trajectory.jsonl.zst").is_file()
                      or (folder / "trajectory.jsonl").is_file())
        if not manifest.get("session_id") or not has_events:
            continue
        if _scratch_manifest(manifest):
            continue
        manifests.append((folder, manifest))
        agent = str(manifest.get("agent") or folder.parent.name)
        if manifest.get("session_role") in {"subagent", "internal"}:
            continue
        if agent == "codex":
            from ..session_kind import codex_kind, codex_meta
            source = store.session_capture(str(manifest.get("session_id") or "")).get("source_path")
            if source and codex_kind(codex_meta(Path(source)))[0] != "main":
                continue

        own = _own_stream(manifest)
        coverage = own.get("coverage") or manifest.get("coverage") or {}
        records = int(own.get("count") or manifest.get("records") or 0)
                                                                        
                                                                           
                                                            
        if (records <= 2 and int(coverage.get("user_messages") or 0) == 0):
            continue
        own_sid = _own_stream_id(manifest)
        stamp = _stamp_for_stream(manifest, own_sid)
        covered, partial = _stamp_coverage(manifest, own_sid)
        sid = str(manifest.get("session_id"))
        seen.add(sid)
                                                                        
                                                                           
        fork_of = ("" if agent in {"hermes", "cline", "opencode", "kilo", "openclaw"}
                   else str(manifest.get("fork_of") or ""))
        raw_title = str(manifest.get("display_title") or manifest.get("title")
                        or sid)
        base_title = raw_title
        if is_internal_user_text(base_title):
            base_title = _preview_title(folder) or ""
                                                                       
                                                                     
        if is_internal_user_text(raw_title) and not base_title:
            continue
                                                                         
                                                                           
                                                                        
        if (int(coverage.get("user_messages") or 0) == 0
                and base_title in ("", sid)
                and not _preview_title(folder)):
            continue
                                                                           
                                                                      
                                                                    
                                 
        visible_turns = None
        out.append({
            "session_id": sid,
            "agent": agent,
            "surface": str(manifest.get("surface") or ""),
            "project": _project_for(store, folder, manifest),
            "capture_tier": str(manifest.get("capture_tier")
                                or capture_tier_for(manifest.get("capture_point"))),
            "title": base_title,
            "records": records,
            "turns": visible_turns if visible_turns is not None
                     else int(coverage.get("user_messages") or 0),
            "tool_calls": int(coverage.get("tool_calls") or 0),
            "started_at": (manifest.get("first_ts")
                           or manifest.get("created_at") or ""),
            "created_at": manifest.get("created_at") or "",
            "last_at": _activity_at(manifest),
            "stamp": stamp,
            "stamp_partial": partial,
            "stamp_covers": covered,
            "committed_to": _committed_to(own),
            "committed_at": str(manifest.get("last_checkpoint_at") or ""),
            "folder": str(folder),
            "rev": _rev(folder),
            "fork_of": fork_of,
            "kind": "fork" if fork_of else "",
            "parent_session": fork_of,
            "parent_title": "",
            "seeded_prefix": bool(manifest.get("seeded_prefix")),
            "nested_child": False,
            "nested_fork": False,
            "children": [],
        })

                                                                              
                                                                          
    for folder, manifest in manifests:
        parent_id = str(manifest.get("session_id") or "")
        parent_title = str(manifest.get("display_title") or manifest.get("title")
                           or parent_id)
        for child_id, entry in (manifest.get("child_sessions") or {}).items():
            if child_id in seen or not _nested_fork(store, str(child_id), entry):
                continue
            stream = f"local-dsh-{_bare(child_id)}"
            count = int((entry or {}).get("records") or 0)
            meta = _child_meta(store, str(child_id), entry)
            stamp = _stamp_for_stream(manifest, stream)
            covered, partial = _stamp_coverage(manifest, stream)
            out.append({
                "session_id": str(child_id),
                "agent": str(manifest.get("agent") or folder.parent.name),
                "project": _project_for(store, folder, manifest),
                "title": parent_title,
                "records": count,
                "turns": 0,
                "tool_calls": 0,
                "started_at": (manifest.get("first_ts")
                               or manifest.get("created_at") or ""),
                "created_at": (_mtime_iso((entry or {}).get("source_mtime"))
                               or manifest.get("created_at") or ""),
                "last_at": (_mtime_iso((entry or {}).get("source_mtime"))
                            or manifest.get("last_activity") or ""),
                "stamp": stamp,
                "stamp_partial": partial,
                "stamp_covers": covered,
                "committed_to": _committed_to((manifest.get("streams") or {}).get(stream) or {}),
                "committed_at": str(manifest.get("last_checkpoint_at") or ""),
                "folder": str(folder),
                "rev": _rev(folder),
                "fork_of": parent_id,
                "kind": "fork",
                "mode": "seeded",
                "tokens": int(meta.get("tokens") or 0),
                "duration_s": int(meta.get("duration_s") or 0),
                "status": str(meta.get("status") or "unknown"),
                "parent_session": parent_id,
                "parent_title": parent_title,
                "seeded_prefix": True,
                "nested_child": True,
                "nested_fork": True,
                "children": [],
            })
    out.sort(key=lambda s: (s["last_at"], s["started_at"]), reverse=True)
                                                                            
                                                                              
                                                                             
                                                                          
                                                        
    by_id = {str(row["session_id"]): row for row in out}
    for row in out:
        sid = str(row["session_id"])
        parent_id = str(row.get("fork_of") or row.get("parent_session") or "")
        if not row.get("title") or row["title"] in {sid, parent_id}:
            row["title_hint"] = _preview_title(Path(str(row.get("folder") or "")), sid)
    for row in out:
        sid = str(row["session_id"])
        parent_id = str(row.get("fork_of") or row.get("parent_session") or "")
        title = str(row.get("title") or "").strip()
        parent = by_id.get(parent_id)
        parent_title = str((parent or {}).get("title") or "").strip()
        parent_hint = str((parent or {}).get("title_hint") or "").strip()
        if (not title or title in {sid, parent_id}) and (
                parent_title and parent_title not in {sid, parent_id}):
            title = parent_title
        elif (not title or title in {sid, parent_id}) and parent_hint:
            title = ""
            row["title_hint"] = str(row.get("title_hint") or parent_hint)
        if title in {sid, parent_id}:
            title = ""
        row["title"] = title
        row["untitled"] = not (title or row.get("title_hint"))
                                                                          
                                                                          
                                                                         
                                        
    parents = {row["session_id"]: row["title"] for row in out}
    for row in out:
        if not row.get("fork_of") or re.search(r" \(\d+\)$", row["title"]):
            continue
        parent_title = parents.get(str(row["fork_of"]), "")
        if parent_title and row["title"] != parent_title:
            continue
        row["title"] = row["title"] + " (1)"
    archived = view_state.archived(home)
    pinned = view_state.pinned(home)
    for row in out:
        sid = str(row["session_id"])
        entry = archived.get(sid) or {}
        pin = pinned.get(sid) or {}
        row["archived"] = bool(entry)
        row["archived_at"] = str(entry.get("archived_at") or "")
        row["pinned"] = bool(pin) and not entry
        row["pinned_at"] = str(pin.get("pinned_at") or "") if not entry else ""
    return out


def find(home: Path, session_id: str) -> dict[str, Any] | None:
    for row in sessions(home):
        if row["session_id"] == session_id:
            return row
                                                                             
                                                                      
    store = _store(home)
    root = store_root(home)
    for folder in sorted(root.glob("*/*")):
        manifest = _manifest(folder)
        for child_id, entry in (manifest.get("child_sessions") or {}).items():
            if _bare(child_id) != _bare(session_id):
                continue
            return _child_row(store, folder, manifest, str(child_id), entry)
    return None


                                                                               

def _blocks(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    if isinstance(value, list):
        return [b for b in value if isinstance(b, dict)]
    if isinstance(value, dict):
        inner = value.get("content")
        if isinstance(inner, list):
            return [b for b in inner if isinstance(b, dict)]
    return []


def _text_of(value: Any) -> str:
    parts: list[str] = []
    for block in _blocks(value):
        kind = block.get("type")
        if kind in ("text", "thinking", "reasoning"):
            parts.append(str(block.get("text") or ""))
        elif kind == "tool_result":
            parts.append(_text_of(block.get("content")))
    return "\n".join(p for p in parts if p).strip()


def _images_of(value: Any) -> list[dict[str, Any]]:
    """Attachment descriptors from content blocks (image blocks only)."""
    out: list[dict[str, Any]] = []
    for block in _blocks(value):
        if block.get("type") != "image":
            continue
        meta = block.get("attachment")
        if isinstance(meta, dict):
            digest = str(meta.get("attachmentId") or "").split(":", 1)[-1]
            media = str(meta.get("mediaType") or "")
            ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
                   "image/webp": ".webp", "image/gif": ".gif",
                   "image/avif": ".avif"}.get(media.lower(), "")
            if not digest:
                continue
            out.append({"file": digest + ext, "name": str(meta.get("name") or ""),
                        "width": meta.get("width"), "height": meta.get("height")})
            continue
                                                                           
                                                                             
                           
        url = str(block.get("url") or "")
        if url.startswith(("https://", "http://")):
            out.append({"url": url, "name": str(block.get("name") or "")})
    return out


def _files_of(value: Any) -> list[dict[str, Any]]:
    """Documents attached to a message: what was attached, never its content.

    A PDF, a slide deck or a video is not stored in the record (it can be
    large, and it is not conversation text). What the record keeps is the
    listing: name, path when the host gave one, type, size, and the content
    hash when the bytes were seen. The page shows exactly that.
    """
    out: list[dict[str, Any]] = []
    for block in _blocks(value):
        if block.get("type") not in ("document", "file"):
            continue
        meta = block.get("document")
        if not isinstance(meta, dict):
            meta = block.get("attachment")
        if not isinstance(meta, dict):
            continue
        media = str(meta.get("mediaType") or "")
        if media.lower().startswith("image/"):
            continue                                                
        name = str(meta.get("name") or "")
        path = str(meta.get("path") or "")
        if not name and not path:
            continue
        size = meta.get("bytes")
        out.append({
            "name": name or path.rsplit("/", 1)[-1],
            "path": path, "media": media,
            "bytes": size if isinstance(size, int) and size >= 0 else None,
            "sha256": str(meta.get("sha256") or meta.get("attachmentId") or "")
                      .split(":", 1)[-1]})
    return out


def _is_injected(text: str) -> bool:
    return (is_internal_user_text(text)
            or any(text.startswith(prefix) for prefix in _INJECTED_PREFIXES))


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


def _parse_args(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _tool_summary(name: str, args: Any) -> str:
    """One useful line for a collapsed tool row - never a wall of JSON."""
    if isinstance(args, dict):
        for key in ("command", "file_path", "path", "query", "url", "pattern",
                    "description", "prompt"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                line = value.strip().splitlines()[0]
                return line if len(line) <= 120 else line[:119] + "…"
    if isinstance(args, str) and args.strip():
        return args.strip().splitlines()[0][:120]
    return name or "tool"


def _usage(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    mapped = {}
    for key, value in raw.items():
        canonical = _USAGE_KEYS.get(key)
        if canonical and isinstance(value, (int, float)) and not isinstance(value, bool):
            mapped[canonical] = value
    return usage_dict(mapped)


def _merge_usage(total: dict[str, float], usage: dict[str, Any] | None) -> None:
    for key, value in (usage or {}).items():
        total[key] = total.get(key, 0) + value


def _record_msgs(records: list[dict[str, Any]]) -> tuple[list[MessageView], dict[str, Any]]:
    """Fold the event stream into messages. Returns (messages, stats)."""
    calls: dict[str, ToolCallView] = {}
    results: dict[str, dict[str, Any]] = {}
    for record in records:
        payload = record.get("payload") or {}
        action = record.get("action_type")
        if action == "tool_result":
            call_id = str(payload.get("call_id") or "")
            if call_id:
                results[call_id] = payload

    messages: list[MessageView] = []
    stats = {"injected": 0, "compactions": 0, "models": [], "user_at": []}
    usage_total: dict[str, float] = {}
    pending_assistant: MessageView | None = None
    seen_messages: set[str] = set()

    for position, record in enumerate(records):
        action = record.get("action_type") or ""
        payload = record.get("payload") or {}
        ts = record.get("ts_utc")
        if payload.get("ts_source") == "session":
            ts = None
        if action == "user_message":
            text = _text_of(payload.get("content"))
            images = _images_of(payload.get("content"))
            files = _files_of(payload.get("content"))
            if not text and not images and not files:
                continue
            if str(payload.get("origin") or "user") not in ("", "user") or _is_injected(text):
                stats["injected"] += 1
                continue
                                                                          
                                                                           
                                                        
            message_id = str(payload.get("id") or "")
            if message_id and message_id in seen_messages:
                continue
            if message_id:
                seen_messages.add(message_id)
            messages.append(MessageView(
                role="user", ts=ts,
                turn=payload.get("turn") if isinstance(payload.get("turn"), int) else None,
                text=text, images=images, files=files))
            stats["user_at"].append(position)
            pending_assistant = None
            continue
        if action == "compaction":
            stats["compactions"] += 1
            continue
        if action != "assistant_message":
            if action == "tool_call":
                call_id = str(payload.get("call_id") or "")
                if call_id and call_id in calls:
                    continue
                call = calls.get(call_id)
                if call is None:
                    name = str(payload.get("tool") or "")
                    args = (payload.get("args") or {}).get("content")
                    call = ToolCallView(name=name, summary=_tool_summary(name, args),
                                        input_text=_json(args))
                    result = results.get(call_id)
                    if result is not None:
                        call.output = _text_of(result.get("content"))
                        call.is_error = bool(result.get("is_error"))
                        call.images = _images_of(result.get("content"))
                    if pending_assistant is None:
                        pending_assistant = MessageView(role="assistant", ts=None, text="")
                        messages.append(pending_assistant)
                    pending_assistant.tool_calls.append(call)
                    calls[call_id] = call
            continue
        message_id = str(payload.get("id") or "")
        if message_id and message_id in seen_messages:
            continue
        if message_id:
            seen_messages.add(message_id)
        blocks = _blocks(payload.get("content"))
        texts: list[str] = []
        thinking: list[str] = []
        tool_calls: list[ToolCallView] = []
        for block in blocks:
            kind = block.get("type")
            if kind in ("thinking", "reasoning"):
                text = str(block.get("text") or "")
                if text:
                    thinking.append(text)
            elif kind == "text":
                text = str(block.get("text") or "")
                if text.strip():
                    texts.append(text)
            elif kind in ("tool-call", "tool_use"):
                call_id = str(block.get("id") or block.get("tool_use_id") or "")
                if call_id and call_id in calls:
                    continue
                name = str(block.get("name") or "")
                args = _parse_args(block.get("arguments")
                                   if "arguments" in block else block.get("input"))
                call = ToolCallView(name=name,
                                    summary=_tool_summary(name, args),
                                    input_text=_json(args))
                if call_id:
                    calls[call_id] = call
                    result = results.get(call_id)
                    if result is not None:
                        call.output = _text_of(result.get("content"))
                        call.is_error = bool(result.get("is_error"))
                        call.images = _images_of(result.get("content"))
                tool_calls.append(call)
        usage = _usage(payload.get("usage"))
        model = str(payload.get("model") or "") or None
        provider = str(payload.get("provider") or "") or None
        effort = str(payload.get("reasoning_effort") or "") or None
        if model and model not in stats["models"]:
            stats["models"].append(model)
        if model:
            name, badge = model_label(provider, model)
            label = (name + (" " + badge if badge else "")).strip()
            if label and label not in stats.setdefault("model_labels", []):
                stats["model_labels"].append(label)
            if effort and effort not in stats.setdefault("efforts", []):
                stats["efforts"].append(effort)
        message = MessageView(role="assistant", ts=ts,
                              turn=payload.get("turn") if isinstance(payload.get("turn"), int) else None,
                              text="\n\n".join(texts),
                              images=_images_of(payload.get("content")),
                              thinking="\n\n".join(thinking) or None,
                              tool_calls=tool_calls, model=model,
                              provider=provider, effort=effort, usage=usage)
        _merge_usage(usage_total, usage)
        messages.append(message)
        pending_assistant = message

    stats["usage_total"] = {k: int(v) if float(v).is_integer() else v
                            for k, v in usage_total.items()} or None
    return messages, stats


def _turn_ends(events: list[dict[str, Any]], user_at: list[int]) -> list[int] | None:
    """For each turn the page shows: how many leading records hold all of it.

    A timestamp covers a prefix of the stream (``covers_up_to`` records), so a
    turn is timestamped exactly when its end is within that prefix. A turn runs
    from its user message to the record before the next shown user message;
    the recorder's own bookkeeping at its tail (``lifecycle.*``: commitments,
    pause/resume) is not conversation content and does not hold a turn back.
    Records before the first user message belong to the first turn.

    Returns None when positions cannot be trusted to equal the stream's own
    numbering (``seq`` is not 0..n-1): the page then makes no per-turn claim.
    """
    if any(record.get("seq") != index for index, record in enumerate(events)):
        return None
    ends: list[int] = []
    for k, start in enumerate(user_at):
        end = user_at[k + 1] if k + 1 < len(user_at) else len(events)
        while end > start + 1 and str(
                events[end - 1].get("action_type") or "").startswith("lifecycle."):
            end -= 1
        ends.append(end)
    return ends


def _turns_within(ends: list[int] | None, covers: Any) -> int | None:
    """How many whole turns a prefix of ``covers`` records contains."""
    if ends is None:
        return None
    limit = int(covers or 0)
    return sum(1 for end in ends if end <= limit)


def _turn_marks(ends: list[int] | None,
                stamps: list[dict[str, Any]]) -> list[dict[str, Any] | None] | None:
    """For each turn: the first delivered timestamp that holds all of it.

    That is the stamp that first proved the turn existed, so its authority and
    time are what the turn's mark shows. None for a turn no delivered
    timestamp covers yet. The whole list is None when per-turn coverage cannot
    be stated (see ``_turn_ends``).
    """
    if ends is None:
        return None
    delivered = sorted(
        (s for s in stamps if not s.get("pending") and not s.get("failed")),
        key=lambda s: (int(s.get("covers_up_to") or 0), str(s.get("created_at") or "")))
    marks: list[dict[str, Any] | None] = []
    for end in ends:
        hit = next((s for s in delivered if int(s.get("covers_up_to") or 0) >= end), None)
        marks.append(None if hit is None else {
            "provider": hit.get("provider") or "",
            "gen_time": hit.get("gen_time") or hit.get("created_at") or "",
            "covers_up_to": int(hit.get("covers_up_to") or 0)})
    return marks


def conversation_stamped(home: Path, row: dict[str, Any]) -> bool:
    """Is every turn of this conversation inside a delivered timestamp?

    True at once when the timestamp covers every record. Otherwise the
    uncovered tail is read: if it is only the recorder's closing bookkeeping,
    the conversation itself is fully timestamped (same rule as ``load``).
    """
    total = int(row.get("records") or 0)
    if total <= 0 or row.get("stamp") != "stamped":
        return False
    if int(row.get("stamp_covers") or 0) >= total:
        return True
    try:
        view = load(home, str(row.get("session_id") or ""), verify_integrity=False)
    except Exception:
        return False
    return bool(view) and view.get("stamp") == "stamped" and not view.get("stamp_partial") \
        and int(view.get("turns") or 0) > 0


def _integrity(envelopes: list[dict[str, Any]],
               nested: bool = False) -> dict[str, Any]:
    """Recompute one conversation's chain locally, record by record.

    The viewer should be able to say "these bytes are the ones that were
    signed" without asking the user to run the CLI. This is that check: each
    record's hash against its own content, each ``prev_hash`` against the
    record before it, and the receipt chain's recv_seq/prev links.

    A legacy nested fork starts in the middle of its parent's receipt chain;
    the record chain still starts at GENESIS, so only the first receipt link is
    unavailable. ``nested`` skips that one link, not the record checks.
    """
    prev = GENESIS_PREV_HASH
    recv_prev = GENESIS_PREV_HASH
    checked = 0
    for index, env in enumerate(envelopes):
        record = env.get("record") or {}
        body = {k: v for k, v in record.items()
                if k not in ("record_hash", "record_sig")}
        try:
            digest = sha256_hex(canonicalize(body))
        except Exception:
            return {"ok": False, "records": checked, "reason": "unreadable"}
        if digest != record.get("record_hash"):
            return {"ok": False, "records": checked, "reason": "record_hash"}
        if str(record.get("prev_hash") or "") != prev:
            return {"ok": False, "records": checked, "reason": "prev_hash"}
        if env.get("server_prev_hash") != recv_prev:
            if not (nested and index == 0):
                return {"ok": False, "records": checked, "reason": "receipt_chain"}
        prev = str(record.get("record_hash") or "")
        recv_prev = str(env.get("server_record_hash") or "")
        checked += 1
    return {"ok": True, "records": checked, "reason": ""}


def _gaps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Declared recording gaps, read from the signed pause/resume records."""
    out: list[dict[str, Any]] = []
    paused_at = None
    for record in events:
        action = record.get("action_type")
        payload = record.get("payload") or {}
        if action == "lifecycle.pause":
            paused_at = payload.get("at")
            continue
        if action != "lifecycle.resume":
            continue
        gap = payload.get("gap")
        paused_at = None
        if not isinstance(gap, dict):
            continue
        out.append({
            "from_turn": gap.get("from_turn"), "to_turn": gap.get("to_turn"),
            "from_host_seq": gap.get("from_host_seq"),
            "to_host_seq": gap.get("to_host_seq"),
            "paused_at": gap.get("paused_at"), "resumed_at": gap.get("resumed_at"),
            "unavailable": bool(gap.get("unavailable")),
        })
    if paused_at:
        out.append({"open": True, "paused_at": paused_at})
    return out


def load(home: Path, session_id: str, verify_integrity: bool = True
         ) -> dict[str, Any] | None:
    """One session, ready for the page: metadata + messages + stamp facts."""
    row = find(home, session_id)
    if row is None:
        return None
                                                                          
                                                                         
    rev = _rev(Path(row["folder"])) if row.get("folder") else ""
    store = _store(home)
                                                                            
                                                              
    envelopes = store.session_envelopes(session_id)
    events = [e.get("record") or {} for e in envelopes]
    if not events:
        return None
    nested = bool(row.get("nested_child") or row.get("nested_fork"))
    folder = Path(row["folder"]) if nested else store.locate(session_id)
    manifest = _manifest(folder) if folder else {}
    if nested and row.get("agent") not in (None, "dsh"):
        return None
    if nested:
                                                                                
                                                                        
        child_chain = next(
            (str(chain) for chain, who in (manifest.get("chain_sessions") or {}).items()
             if _bare(str(who)) == _bare(session_id)), "")
        if not child_chain:
            return None                                               
        if child_chain:
            events = [r for r in events
                      if str(r.get("session_id") or "") == child_chain]
        stream = f"local-dsh-{_bare(session_id)}"
    else:
                                                                          
                                                                                
        parent_id = str(manifest.get("session_id") or "")
        child_chains = {str(chain) for chain, who
                        in (manifest.get("chain_sessions") or {}).items()
                        if who and _bare(str(who)) != _bare(parent_id)}
        if child_chains:
            events = [r for r in events
                      if str(r.get("session_id") or "") not in child_chains]
        from ..session_store import own_stream_id as _own_stream_id
        stream = _own_stream_id(manifest)
    if nested:
        if child_chain:
            envelopes = [e for e in envelopes
                         if str((e.get("record") or {}).get("session_id") or "")
                         == child_chain]
    elif child_chains:
        envelopes = [e for e in envelopes
                     if str((e.get("record") or {}).get("session_id") or "")
                     not in child_chains]
    integrity = (_integrity(envelopes, nested=nested) if verify_integrity
                 else {"ok": None, "deferred": True, "checked": 0})
    start_meta = {}
    for record in events:
        if record.get("action_type") == "lifecycle.session_start":
            start_meta = ((record.get("payload") or {}).get("meta") or {})
            break
    messages, stats = _record_msgs(events)
                                                                           
                                                                        
                       
    retracted_turns = sorted({
        int((record.get("payload") or {}).get("turn"))
        for record in events
        if record.get("action_type") == "retraction"
        and isinstance((record.get("payload") or {}).get("turn"), int)
    })
                                                                          
                                                                            
    turn_ends = (_turn_ends(events, stats["user_at"])
                 if len(events) == int(row.get("records") or 0) else None)
    attachments_dir = (folder / "attachments") if folder else None

    def _public(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for img in images:
            ok = bool(img.get("url")) or bool(
                attachments_dir and (attachments_dir / img.get("file", "")).is_file())
            rows.append({**img, "ok": ok})
        return rows
    stamp = _latest_stamp_for_stream(manifest, stream)
    turns_stamped = _turns_within(turn_ends, (stamp or {}).get("covers_up_to"))
                                                                            
                                                                            
                                                                              
                                                                             
                                                          
    stamp_partial = bool(row["stamp_partial"])
    if stamp_partial and turn_ends and turns_stamped == len(turn_ends):
        stamp_partial = False
    is_fork = (row.get("kind") == "fork"
               or (bool(row.get("fork_of")) and not row.get("kind")))
    all_tree = _children_tree(store, folder, manifest)
                                                                              
                                                                           
    tree = [item for item in all_tree if item.get("kind") != "fork"]
    siblings: list[dict[str, Any]] = []
    if nested:
        _number_child_titles(all_tree, str(row.get("parent_title") or row["title"]))

        def _locate(rows: list[dict[str, Any]], wanted: str):
            for item in rows:
                if item["session_id"] == wanted:
                    return item, rows
                found, level = _locate(item.get("children") or [], wanted)
                if found is not None:
                    return found, level
            return None, []

        current, level = _locate(all_tree, session_id)
        children = [x for x in ((current or {}).get("children") or [])
                    if x.get("kind") != "fork"]
        siblings = [x for x in level
                    if x["session_id"] != session_id and x.get("kind") != "fork"]
    else:
        _number_child_titles(tree, row["title"])
        children = tree
    return {
        "session_id": session_id,
        "agent": row["agent"],
        "surface": str(row.get("surface") or ""),
        "archived": bool(row.get("archived")),
        "archived_at": str(row.get("archived_at") or ""),
        "pinned": bool(row.get("pinned")),
        "pinned_at": str(row.get("pinned_at") or ""),
        "project": row.get("project") or "",
        "capture_tier": row.get("capture_tier") or "unknown",
        "title": row["title"],
        "title_hint": str(row.get("title_hint") or ""),
        "untitled": bool(row.get("untitled")),
        "kind": row.get("kind") or ("fork" if is_fork else ""),
        "status": row.get("status") or "",
        "tokens": int(row.get("tokens") or 0),
        "duration_s": int(row.get("duration_s") or 0),
        "parent_session": "" if is_fork else (row.get("parent_session") or ""),
        "parent_title": "" if is_fork else (row.get("parent_title") or ""),
        "children": children,
        "siblings": [] if is_fork else siblings,
        "messages": [{
            "role": m.role,
            "ts": m.ts,
            "turn": m.turn,
            "retracted": isinstance(m.turn, int) and m.turn in retracted_turns,
            "text": m.text,
            "thinking": m.thinking,
            "model": m.model,
            "images": _public(m.images),
            "files": m.files,
            "model_name": model_label(m.provider, m.model)[0],
            "model_badge": model_label(m.provider, m.model)[1],
            "effort": m.effort,
            "usage": m.usage,
            "tool_calls": [{
                "name": t.name,
                "summary": t.summary,
                "input": t.input_text,
                "output": t.output,
                "is_error": t.is_error,
                "images": _public(t.images),
            } for t in m.tool_calls],
        } for m in messages],
        "host_turns": manifest.get("host_turns") or {},
        "retracted_turns": retracted_turns,
        "records": row["records"],
        "turns": sum(1 for m in messages if m.role == "user") or row["turns"],
        "tool_calls": row["tool_calls"] or sum(len(m.tool_calls) for m in messages),
        "started_at": row["started_at"],
        "last_at": row["last_at"],
        "committed_to": row["committed_to"],
        "committed_at": row["committed_at"],
        "head_omitted": bool(start_meta.get("head_omitted")),
        "gaps": _gaps(events),
        "models": stats["models"],
        "model_labels": stats.get("model_labels") or [],
        "efforts": stats.get("efforts") or [],
        "usage_total": stats["usage_total"],
        "compactions": stats["compactions"],
        "injected_context": stats["injected"],
        "folder": str(folder) if folder else "",
        "rev": rev,
        "size": _human_size(_dir_bytes(folder)) if folder else "",
        "integrity": integrity,
        "stamp": row["stamp"],
        "stamp_partial": stamp_partial,
        "stamp_detail": stamp,
        "stamps": [{**item, "turns": _turns_within(turn_ends, item["covers_up_to"])}
                   for item in _stamps_for_stream(manifest, stream)],
                                                                             
                                                                     
        "turns_stamped": turns_stamped,
                                                                        
        "turn_marks": _turn_marks(turn_ends, _stamps_for_stream(manifest, stream)),
        "turns_committed": _turns_within(turn_ends, row["committed_to"]),
        "coverage": {"stamped": int((stamp or {}).get("covers_up_to") or 0),
                     "records": row["records"]},
        "fork_of": str(row.get("fork_of") or ""),
        "nested_child": nested,
        "nested_fork": bool(row.get("nested_fork")),
        "seeded_prefix": bool(row.get("seeded_prefix")),
    }


def delete(home: Path, session_id: str) -> bool:
    """Delete one conversation by deleting its folder - the product's rule.

    Guarded: only a folder that is exactly one level inside the store root
    and that carries a manifest with this session id is removed. A legacy
    nested fork shares its parent's folder and is intentionally read-only.
    """
    row = find(home, session_id)
    if row is not None and row.get("nested_fork"):
        return False
    root = store_root(home).resolve()
    for folder in root.glob("*/*"):
        manifest = _manifest(folder)
        if str(manifest.get("session_id")) != session_id:
            continue
        target = folder.resolve()
        if target.parent.parent != root or not (target / "manifest.json").is_file():
            return False
        shutil.rmtree(target)
        return True
    return False


def folder_for(home: Path, session_id: str) -> Path | None:
    for row in sessions(home):
        if row["session_id"] == session_id:
            return Path(row["folder"])
    return None


def _event_ms(value: Any) -> int:
    raw = str(value or "")
    if not raw:
        return 0
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def _trajectory_text(value: Any, limit: int = 8000) -> str:
    text = _text_of(value)
    return text if len(text) <= limit else text[:limit] + "\n…"


def _trajectory_json(value: Any, limit: int = 8000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "\n…"


def _retraction_text(payload: dict[str, Any], turn: Any) -> str:
    """One line for a retracted turn, using only what the host recorded."""
    count = payload.get("turns")
    parts: list[str] = []
    if isinstance(count, int) and count > 0:
        parts.append(f"{count} turn" + ("" if count == 1 else "s"))
    if isinstance(turn, int):
        parts.append(f"from turn {turn}")
    return "user retracted" + ((" (" + ", ".join(parts) + ")") if parts else "")


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()[:180]
    return ""


def _trajectory_events(home: Path, session_id: str):
    """The same chain filter as load(), for the raw-event trajectory view."""
    row = find(home, session_id)
    if row is None:
        return None
    store = _store(home)
    events = store.session_events(session_id)
    if not events:
        return None
    nested = bool(row.get("nested_child") or row.get("nested_fork"))
    folder = Path(row["folder"]) if nested else store.locate(session_id)
    manifest = _manifest(folder) if folder else {}
    if nested:
        child_chain = next(
            (str(chain) for chain, who in (manifest.get("chain_sessions") or {}).items()
             if _bare(str(who)) == _bare(session_id)), "")
        if child_chain:
            events = [r for r in events
                      if str(r.get("session_id") or "") == child_chain]
    else:
        parent_id = str(manifest.get("session_id") or "")
        child_chains = {str(chain) for chain, who
                        in (manifest.get("chain_sessions") or {}).items()
                        if who and _bare(str(who)) != _bare(parent_id)}
        if child_chains:
            events = [r for r in events
                      if str(r.get("session_id") or "") not in child_chains]
    return row, events


def trajectory(home: Path, session_id: str,
               limit: int | None = None) -> dict[str, Any] | None:
    """DSH-style turn/step ledger built from the signed event stream.

    ``limit`` keeps the newest N rows for transport. A 40k-record session is a
    76 MB JSON payload; the viewer opens the last page first and asks for more
    only when the user chooses to.
    """
    ctx = _trajectory_events(home, session_id)
    if ctx is None:
        return None
    row, events = ctx
    rows: list[dict[str, Any]] = []
    calls: dict[str, dict[str, Any]] = {}
    for event in events:
        action = str(event.get("action_type") or "")
        payload = event.get("payload") or {}
        ts = str(event.get("ts_utc") or "")
        ts_ms = _event_ms(ts)
        if payload.get("ts_source") == "session":
            ts, ts_ms = "", 0
        turn = payload.get("turn")
        step = payload.get("step")
        if action == "user_message":
            text = _trajectory_text(payload.get("content"))
            item = {"kind": "user", "turn": turn, "step": step,
                    "ts": ts, "ts_ms": ts_ms, "text": text,
                    "summary": _first_line(text), "tokens": 0,
                    "duration_ms": 0, "pending": False}
            rows.append(item)
        elif action == "assistant_message":
            text = _trajectory_text(payload.get("content"))
            item = {"kind": "assistant", "turn": turn, "step": step,
                    "ts": ts, "ts_ms": ts_ms, "text": text,
                    "summary": _first_line(text) or "Assistant",
                    "tokens": _token_total([event]),
                    "model": str(payload.get("model") or ""),
                    "provider": str(payload.get("provider") or ""),
                    "duration_ms": 0, "pending": False}
            rows.append(item)
        elif action == "tool_call":
            call_id = str(payload.get("call_id") or "")
            args = payload.get("args")
            item = {"kind": "tool", "turn": turn, "step": step,
                    "ts": ts, "ts_ms": ts_ms, "call_id": call_id,
                    "tool": str(payload.get("tool") or ""),
                    "args": _trajectory_json(args),
                    "summary": str(payload.get("tool") or "tool"),
                    "tokens": 0, "duration_ms": 0, "pending": True,
                    "is_error": False, "result": ""}
            rows.append(item)
            if call_id:
                calls[call_id] = item
        elif action == "tool_result":
            call_id = str(payload.get("call_id") or "")
            item = calls.get(call_id)
            if item is None:
                text = _trajectory_text(payload.get("content"))
                item = {"kind": "tool", "turn": turn, "step": step,
                        "ts": ts, "ts_ms": ts_ms, "call_id": call_id,
                        "tool": str(payload.get("tool") or "result"),
                        "args": "", "summary": _first_line(text) or "tool result",
                        "tokens": 0, "duration_ms": 0, "pending": False,
                        "is_error": bool(payload.get("is_error")), "result": text}
                rows.append(item)
            else:
                item["result"] = _trajectory_text(payload.get("content"))
                item["is_error"] = bool(payload.get("is_error"))
                item["pending"] = False
                if ts_ms and item.get("ts_ms"):
                    item["duration_ms"] = max(0, ts_ms - int(item["ts_ms"]))
                if not item.get("summary") or item.get("summary") == item.get("tool"):
                    item["summary"] = _first_line(item["result"]) or item.get("tool")
        elif action == "compaction":
            text = _trajectory_text(payload.get("summary") or payload.get("content")
                                    or payload.get("reason") or "")
            rows.append({"kind": "compaction", "turn": turn, "step": step,
                         "ts": ts, "ts_ms": ts_ms, "text": text,
                         "summary": _first_line(text) or "Compaction",
                         "tokens": 0, "duration_ms": 0, "pending": False})
        elif action == "retraction":
            text = _retraction_text(payload, turn)
            rows.append({"kind": "retraction", "turn": turn, "step": step,
                         "ts": ts, "ts_ms": ts_ms, "text": text,
                         "summary": _first_line(text) or "Retracted",
                         "tokens": 0, "duration_ms": 0, "pending": False})
                                                                         
                                                                   
    for index, item in enumerate(rows):
        if item["kind"] == "assistant" and not item.get("duration_ms"):
            nxt = next((r["ts_ms"] for r in rows[index + 1:] if r.get("ts_ms")), 0)
            if nxt and item.get("ts_ms"):
                item["duration_ms"] = max(0, min(nxt - int(item["ts_ms"]), 120000))
    turns = sorted({int(r["turn"]) for r in rows
                    if isinstance(r.get("turn"), int)})
    total_rows = len(rows)
    if isinstance(limit, int) and limit >= 0 and total_rows > limit:
        rows = rows[-limit:] if limit else []
    return {
        "session_id": session_id,
        "title": row.get("title") or "",
        "turns": turns,
        "turn_count": len(turns),
        "rows": rows,
        "total_rows": total_rows,
        "truncated": len(rows) < total_rows,
        "records": row.get("records") or total_rows,
    }
