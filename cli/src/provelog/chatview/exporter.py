"""Evidence-pack export for the viewer: a session's recorded proof, zipped.

The viewer stays read-only over the hosts' own files; the one thing it
writes is the export a user asked for, under ~/provelog-exports/ and
nowhere else. What lands in the zip is evidence the recorder already
keeps on this disk, assembled - never re-derived, never re-signed:

  bundle.json            the session's sink rows, ORIGINAL bytes (recv_seq
                         and server hashes untouched, records verbatim -
                         the same assembly `provelog export` uses, scoped
                         to the session's rows)
  <sid>.provelog.jsonl   the session's receipt chain, when one exists on
                         disk (the file itself, byte for byte)
  <sid>.anchor.json      the stamp credential beside it, when one exists
  VERIFY.txt             what this pack proves, per stamp state, and how to
                         check it offline; MANIFEST.json carries the SHA-256
                         of every member

Why the stamp rides as a FILE and bundle["anchors"] may also carry it: the
pack's bundle is a session's rows, which can be a slice of a stream its host's
hooks share. A receipt is copied from the session manifest for the recipient, and
it is embedded in bundle["anchors"] only when its Merkle root recomputes from
exactly these rows (full stream, or the covered prefix). A session sub-pack never
pretends a sliced root is the whole-stream root; the full-stream receipt is
verified by `provelog verify` on the full bundle.

The stamped-scope cut is the stamp's own claim, not a
guess: the latest anchor receipt's covers_up_to counts receipt links,
each link carries the record_hash of the client session a hook event
opened, and those client sessions' rows are exactly what the cut keeps.
"""
from __future__ import annotations

import io
import json
import os
import secrets
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..pack import _DISCLAIMER, build_pack, with_format_headers
from . import stamps
from .safe import _SAFE_COMPONENT, safe_component

                                                                          
                                                              
EXPORT_DIR_NAME = "provelog-exports"

                                                                          
                                                             
_STATUS_COPY = {
    stamps.STAMPED: (
        "Stamped - the pack carries a timestamp issued by an independent TSA "
        "and can be verified offline."
    ),
    stamps.QUEUED: (
        "Stamping - the request was submitted but the receipt is not available "
        "yet; this pack currently carries local signatures only."
    ),
    stamps.UNSTAMPED: (
        "Unstamped - local signatures only: they show the records have not "
        "changed, not when they existed."
    ),
}


                                                                               


def _store_path(home: Path) -> Path:
    """The store the recorder itself uses: PROVELOG_STORE, then the store key
    in <home>/.provelog.json, then ~/ProveLog. Mirrors
    cli._open_local_store's order minus the cwd config search."""
    env = os.environ.get("PROVELOG_STORE")
    if env:
        return Path(env)
    cfg_file = home / ".provelog.json"
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        if isinstance(cfg, dict) and cfg.get("store"):
            return Path(str(cfg["store"]))
    except (OSError, ValueError):
        pass
    return home / "ProveLog Records"


def _start_meta(record: dict) -> tuple[str, str]:
    """(host, host_session_id) a session-start record names - the hook
    path's own binding of a signed client session to its host session."""
    meta = ((record.get("payload") or {}).get("meta") or {})
    return meta.get("host") or "", meta.get("host_session_id") or ""


def scan_store(home: Path) -> dict[tuple[str, str], list[tuple[str, dict]]]:
    """(host, host_session_id) -> that session's sink rows, each paired with
    its stream id. One pass over every stream: a hook event opens its own
    short client session whose start record's meta carries the host
    binding, and every later record of that client session belongs to the
    same host session - the readers' grouping rule, applied to the sink."""
    from ..store import open_store

    path = _store_path(home)
    if not path.exists():
        return {}
    store = open_store(str(path), read_only=True)
    groups: dict[tuple[str, str], list[tuple[str, dict]]] = {}
    try:
        for row in store.list_streams():
            stream_id = row["stream_id"]
            rows = store.get_records(stream_id)
            owner: dict[Any, tuple[str, str]] = {}
            for r in rows:
                rec = r.get("record") or {}
                if rec.get("action_type") == "lifecycle.session_start":
                    host, hsid = _start_meta(rec)
                    if host and hsid:
                        owner[rec.get("session_id")] = (host, hsid)
            for r in rows:
                key = owner.get((r.get("record") or {}).get("session_id"))
                if key:
                    groups.setdefault(key, []).append((stream_id, r))
    finally:
        store.close()
    return groups


                                                                                


                                                                                


@dataclass
class Evidence:
    """Everything the pack for one session is assembled from."""

    host: str
    session_id: str
    stream_id: str
    rows: list[dict]                                                            
    anchored_rows: list[dict]                                           
    badge: str                                                             
    receipt_file: Path | None
    anchor_receipt: dict | None
    covers: int                                                             
    multi_stream: bool                                                          
    title: str | None = None                                               
    folder: Path | None = None                                                
    capture_tier: str = "unknown"                                    

    @property
    def exportable(self) -> bool:
        return bool(self.rows)

    @property
    def partial(self) -> bool:
        """A stamp exists but does not reach the session's last records."""
        return bool(self.anchored_rows) and len(self.anchored_rows) < len(self.rows)

    def to_preview(self) -> dict:
        return {
            "host": self.host, "session_id": self.session_id,
            "stream_id": self.stream_id,
            "stamp": self.badge,
            "records": len(self.rows),
            "anchored_records": len(self.anchored_rows),
            "receipt": self.receipt_file is not None,
            "anchor": self.anchor_receipt is not None,
            "covers": self.covers,
            "exportable": self.exportable,
            "partial": self.partial,
            "capture_tier": self.capture_tier,
        }


def session_evidence(home: Path, host: str, session_id: str,
                     source_path: str | None = None,
                     project: str | None = None,
                     title: str | None = None, *,
                     groups: dict[tuple[str, str], list[tuple[str, dict]]]
                     | None = None) -> Evidence:
    """Assemble one session's evidence. groups is scan_store's result when
    the caller already has it (the everything-export scans once). title is
    the host's own display title for the session, when a reader found one -
    it only names directories, never evidence."""
    if groups is None:
        groups = scan_store(home)
    pairs = groups.get((host, session_id), [])
    rows = [r for _s, r in pairs]
    streams = {s for s, _r in pairs}

    anchor = stamps.anchor_receipt(home, host, session_id)
                                                                                  
                                                                         
    stamped_rows = [a for a in stamps.anchors_for(home, host, session_id)
                    if a.get("receipt") and not a.get("pending")
                    and not a.get("failed")]
    try:
        covers = max((int(a.get("covers_up_to") or 0) for a in stamped_rows), default=0)
    except (TypeError, ValueError):
        covers = 0
                                                                          
                                                                             
                                                             
    covered = min(len(rows), covers) if anchor else 0
    anchored_rows = [r for r in rows
                     if int(r.get("recv_seq", 0)) < covered] if covered else []
    tier = "unknown"
    folder = Path(source_path) if source_path else None
    if folder is not None:
        try:
            from ..session_store import capture_tier_for
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            tier = str(manifest.get("capture_tier")
                       or capture_tier_for(manifest.get("capture_point")))
        except (OSError, ValueError):
            pass
    return Evidence(
        host=host, session_id=session_id,
        stream_id=next(iter(streams), ""),
        rows=rows, anchored_rows=anchored_rows,
        badge=stamps.badge_for(host, session_id, source_path, project, home),
        receipt_file=None, anchor_receipt=anchor,
        covers=covers, multi_stream=len(streams) > 1, title=title,
        folder=folder, capture_tier=tier)


                                                                                


def _embedded_anchors(ev: Evidence, rows: list[dict]) -> list[dict]:
    """The session's receipt as a verifier anchor entry, only when it binds
    exactly these rows.

    A session can be a slice of a stream shared by its host's hooks. In that
    case the receipt's root covers the whole stream and cannot be recomputed
    from the slice, so it stays a separate anchor.json (full-stream
    the full-stream `provelog verify` checks it). When the rows are the stream - the DSH case -
    embedding the receipt makes `provelog verify bundle.json` prove the time
    even after the zip is unpacked.
    """
    if ev.anchor_receipt is None or not rows:
        return []
    if int(rows[0].get("recv_seq", -1)) != 0:
        return []                                                               
    covers = min(int(ev.covers or 0), len(rows))
    if covers <= 0:
        return []
    wrapper = ev.anchor_receipt
    token = wrapper.get("receipt") if isinstance(wrapper.get("receipt"), dict) else wrapper
    if not isinstance(token, dict) or not token.get("kind"):
        return []
    leaves = [r["server_record_hash"] for r in rows
              if int(r.get("recv_seq", -1)) < covers]
    if len(leaves) != covers:
        return []
    if token.get("kind") != "aggregate":
        from ..anchor import merkle_root
        root = wrapper.get("merkle_root") or token.get("merkle_root")
        if not root or merkle_root(leaves) != root:
            return []
    return [{"anchor_seq": 0, "covers_up_to": covers, "receipt": token}]


def build_bundle(ev: Evidence, scope: str = "all") -> tuple[dict, list[dict]]:
    """(bundle, rows-exported) - the session's (or its stamped part's) sink
    rows in the shape cli._local_bundle assembles, bytes untouched."""
    rows = ev.rows if scope == "all" else ev.anchored_rows
    if not rows:
        raise ValueError("no records to export for this scope")
    last = rows[-1]
    bundle = with_format_headers({
        "format": "provelog.bundle/1", "stream_id": ev.stream_id,
        "server_head": {"recv_seq": last.get("recv_seq"),
                        "server_record_hash": last.get("server_record_hash")},
        "records": rows, "anchors": _embedded_anchors(ev, rows),
        "capture_tier": ev.capture_tier})
                                                                               
                                                                              
                                
    if ev.folder is not None:
        try:
            manifest = json.loads((ev.folder / "manifest.json").read_text(encoding="utf-8"))
            attachments = [a for a in (manifest.get("attachments") or [])
                           if isinstance(a, dict)]
            if attachments:
                from ..pack import ATTACHMENTS_VERIFIER_MIN
                bundle["attachments"] = attachments
                bundle["verifier_min"] = ATTACHMENTS_VERIFIER_MIN
        except (OSError, ValueError):
            pass
    return bundle, rows


def _stamp_status(ev: Evidence, rows: list[dict]) -> str:
    """What the pack's own stamp line should say about THESE rows.

    A conversation can grow after its last stamp. Calling that whole pack
    "stamped" would repeat the CLI's old overclaim in the one file a person
    reads first, so a partial stamp is named as such: the prefix is
    timestamped, the tail is only locally signed.
    """
    if ev.badge == stamps.QUEUED:
        return _STATUS_COPY[stamps.QUEUED]
    if ev.anchor_receipt is None or ev.badge == stamps.UNSTAMPED:
        return _STATUS_COPY[stamps.UNSTAMPED]
    covered = min(int(ev.covers or 0), len(rows))
    if covered <= 0:
        return _STATUS_COPY[stamps.UNSTAMPED]
    if covered < len(rows):
        return (f"Partially stamped - the first {covered} records carry a timestamp "
                f"issued by an independent TSA; the remaining {len(rows) - covered} "
                f"carry local signatures only.")
    return _STATUS_COPY[stamps.STAMPED]


def _gap_lines(rows: list[dict]) -> list[str]:
    """One line per declared pause gap, read from the signed resume records."""
    out: list[str] = []
    for row in rows:
        record = row.get("record") or {}
        if record.get("action_type") != "lifecycle.resume":
            continue
        gap = (record.get("payload") or {}).get("gap")
        if not isinstance(gap, dict):
            continue
        if gap.get("unavailable"):
            out.append("Content captured while recording was paused could not be read from the host logs")
            continue
        if gap.get("from_turn") is not None and gap.get("to_turn") is not None:
            span = f"Turn {gap['from_turn']}-{gap['to_turn']}"
        else:
            span = f"host seq {gap.get('from_host_seq', '?')}-{gap.get('to_host_seq', '?')}"
        out.append(f"{span} was not recorded (paused from {gap.get('paused_at') or '?'} "
                   f"to {gap.get('resumed_at') or '?'})")
    return out


def _verify_txt(ev: Evidence, rows: list[dict], scope: str,
                extra: dict[str, bytes] | None = None) -> str:
    """The one file a person opens first: what this is, how to check it, what it proves.

    Deliberately short. The file list and the hashes live in MANIFEST.json, and the
    verdict lives in the verifier's own output - a guide that repeats them drifts
    away from the bytes it is describing.
    """
    anchor = "anchor.json" if ev.anchor_receipt else None
    extra = extra or {}
    status = _stamp_status(ev, rows)
    covered = min(int(ev.covers or 0), len(rows)) if anchor else 0
    partial = bool(anchor) and 0 < covered < len(rows)
    gaps = _gap_lines(rows)
    inside = ["bundle.json             the conversation's records (readable text)"]
    if "trajectory.jsonl.zst" in extra:
        inside.append("trajectory.jsonl.zst    the original bytes the recorder wrote (same records)")
    if anchor:
        inside.append(f"{anchor:<23} third-party timestamp (issued by an independent TSA)")
    if any(name.startswith("attachments/") for name in extra):
        count = sum(1 for name in extra if name.startswith("attachments/"))
        inside.append(
            f"attachments/            {count} files: images the records "
            "reference by hash (the filename is the SHA-256)")
    inside.append("verify-offline.html     open it and verify this pack offline (no network, no CLI)")
    inside.append("trust/                  public Sectigo / FreeTSA roots pinned by the offline verifier")
    inside.append("README.txt              this guide")
    inside.append("MANIFEST.json           SHA-256 and byte length of every file")

    scope_line = (
        ["", f"Scope:            stamped portion only ({len(rows)} records)"]
        if scope == "anchored" else []
    )
    lines = [
        "Open verify-offline.html first and drop bundle.json or the whole zip "
        "into it; it works fully offline, with nothing to install.",
        "",
        "Evidence pack - one conversation's records",
        "=" * 52,
        "",
        f"Host:             {ev.host}",
        f"Session:          {ev.session_id}",
        f"Records:          {len(rows)}",
        f"Stamp:            {status}",
        f"Capture:          {ev.capture_tier}",
    ] + ([f"Gap:              {'; '.join(gaps)}"] if gaps else []) + scope_line + [
        "",
        "Inside:",
    ] + ["  " + x for x in inside] + [
        "",
        "How to verify (offline; nothing to trust us for):",
        "",
        "  1. Open verify-offline.html and drop bundle.json or the whole zip into it (fully offline)",
        "  2. https://provelog.ai/verify    verify in a browser on another machine; nothing is uploaded",
        "  3. provelog verify bundle.json   when the command-line verifier is installed",
        "  MANIFEST.json                 check every SHA-256 with any tool",
        "",
        "Verifiable permanently:",
        "  The verifier stays open source, free and offline; the evidence format "
        "is public (docs/SPEC.md + vectors/v1).",
        "  Even if ProveLog stops being maintained, verify-offline.html in this pack can still verify it.",
        "",
        "What this proves / does not prove:",
        "",
        "  Proven: the records have not changed since they reached the local store (signatures + hash chain)"
        + (": the time is issued by an independent TSA." if anchor and not partial else
           (f": the first {covered} records are time-stamped by an independent "
            f"TSA; the remaining {len(rows) - covered} do not prove when they "
            "existed."
            if partial else ": this pack carries no timestamp and does not prove when anything existed.")),
        "  Not proven: completeness - no record can prove that nothing was withheld before recording.",
        "",
        "Legal notice",
        "------------",
        _DISCLAIMER,
        "",
    ]
    return "\n".join(lines)


def _record_files(ev: Evidence, scope: str) -> dict[str, bytes]:
    """The files that belong to the record itself, as they sit on disk.

    ``trajectory.jsonl.zst`` is the append-only stream the recorder wrote: the
    original bytes, not a re-serialisation. It only travels with a full export -
    a stamped-scope cut is a slice of rows, not a prefix of the file.

    ``attachments/`` are the bytes the records point at by hash (images the user
    sent, screenshots the agent read). Without them the pack is not self-contained:
    the hashes verify, the pictures do not travel.
    """
    out: dict[str, bytes] = {}
    folder = ev.folder
    if folder is None or not folder.is_dir():
        return out
    if scope == "all":
        record = folder / "trajectory.jsonl.zst"
        if record.is_file():
            out["trajectory.jsonl.zst"] = record.read_bytes()
    attachments = folder / "attachments"
    if attachments.is_dir():
        for path in sorted(attachments.iterdir()):
            if path.is_file() and path.name != "index.md":
                out["attachments/" + path.name] = path.read_bytes()
    return out


def session_pack_bytes(home: Path, ev: Evidence, scope: str = "all"
                       ) -> tuple[bytes, int]:
    """(zip bytes, records included) for one session's evidence pack."""
    bundle, rows = build_bundle(ev, scope)
    extra: dict[str, bytes] = {}
    if ev.anchor_receipt is not None:
        extra["anchor.json"] = json.dumps(
            ev.anchor_receipt, indent=2, ensure_ascii=False).encode("utf-8")
    extra.update(_record_files(ev, scope))
    return build_pack(bundle, extra_files=extra or None,
                      guide=_verify_txt(ev, rows, scope, extra)), len(rows)


def export_root(home: Path) -> Path:
    """The viewer's one writable directory, under the home it reads."""
    return home / EXPORT_DIR_NAME


                                                                        
                                                            
_DIR_REASONS = {
    "empty": ("The export directory cannot be empty: give a path inside "
              "your home directory (leave the field as-is to use the "
              "default; an explicitly blank value is always refused)"),
    "unresolvable": "Could not resolve the export directory: {e}",
    "outside": ('The export directory must be inside your home ({home}): '
                '"{raw}" resolves to {resolved}, outside it'),
    "occupied": "A file with the same name is in the way: {resolved}",
}


def _dir_reason(key: str, lang: str = "en", **fmt) -> str:
    return _DIR_REASONS[key].format(**fmt)


def export_dir_for(home: Path, raw: str | None, lang: str = "en") -> Path:
    """The user-chosen export directory, resolved and bounded. The red
    line: this viewer writes only inside the home it reads, so the
    check runs AFTER `~` expansion, `..` folding and symlink resolution -
    nothing the input names, however creatively, lands outside home.

    lang ("en"/"zh") picks the language of the refusal reasons; the app
    passes the request's Accept-Language verdict, direct callers default
    to English.

    Accepted: None (no dir sent - the default ~/provelog-exports/), `~`,
    `~/…`, a relative path (resolved against home), or an absolute path
    already under home. Refused: an explicitly empty/blank value, `/`,
    anything resolving outside home, and a path occupied by a file.
    `~user` forms are NOT expanded (only the bare `~`/`~/` prefix is);
    they resolve as relative names under home, which is safe.
    """
    if raw is None:
        return export_root(home)
    s = raw.strip()
    if not s:
        raise ValueError(_dir_reason("empty", lang))
    if s == "~" or s.startswith("~/"):
        candidate = home / s[1:].lstrip("/")
    elif s.startswith("/"):
        candidate = Path(s)
    else:
        candidate = home / s
    home_r = home.resolve()
    try:
        cand_r = candidate.resolve()
    except OSError as e:                                                       
        raise ValueError(_dir_reason("unresolvable", lang, e=e)) from e
    if cand_r != home_r and home_r not in cand_r.parents:
        raise ValueError(_dir_reason("outside", lang, home=home_r, raw=s,
                                     resolved=cand_r))
    if cand_r.exists() and not cand_r.is_dir():
        raise ValueError(_dir_reason("occupied", lang, resolved=cand_r))
    return cand_r


def _contained_write(root: Path, target: Path, data: bytes) -> Path:
    """Write under root only: every path component came from UNTRUSTED ids
    (host, session id), so each is safe_component'd upstream and the final
    resolved path must still sit under root - belt and braces."""
    root_r = root.resolve()
    target_r = target.resolve()
    if target_r != root_r and root_r not in target_r.parents:
        raise ValueError("export path escapes the export directory")
    target_r.parent.mkdir(parents=True, exist_ok=True)
    target_r.write_bytes(data)
    return target_r


def _next_free(path: Path) -> Path:
    """The first free name at path: a repeat export lands beside the
    existing file as -2, -3, … - nothing is ever silently overwritten."""
    if not path.exists():
        return path
    n = 2
    while path.with_name(f"{path.stem}-{n}{path.suffix}").exists():
        n += 1
    return path.with_name(f"{path.stem}-{n}{path.suffix}")


def _session_zip_name(ev: Evidence, scope: str) -> str:
    """The pack's file name: <session>-<date>.zip, -anchored for the
    stamped-only cut (so both scopes of one session coexist)."""
    name = safe_component(ev.session_id) + "-" + time.strftime("%Y%m%d")
    if scope != "all":
        name += "-" + safe_component(scope)
    return name + ".zip"


def session_zip_bytes(home: Path, ev: Evidence, scope: str = "all"
                      ) -> tuple[bytes, str, int]:
    """(zip bytes, relative path, records) for BROWSER-side delivery: the
    page fetches the bytes and writes them through the folder handle the
    user picked (File System Access). No disk write, no path leaves this
    method - the relative path is safe components only and is resolved
    inside the folder the user chose, never against this process's cwd.
    The zip itself is byte-identical to the disk mode's."""
    data, records = session_pack_bytes(home, ev, scope)
    rel = f"{safe_component(ev.host)}/{_session_zip_name(ev, scope)}"
    return data, rel, records


def export_session_zip(home: Path, ev: Evidence, scope: str = "all",
                       out_dir: Path | None = None) -> tuple[Path, int]:
    """Assemble the pack and write it to <dir>/<host>/<session>-<date>.zip
    (out_dir defaults to ~/provelog-exports/, already validated by
    export_dir_for when it came from a request; the stamped-only cut gets
    an -anchored suffix, and a name already taken gets -2, -3, … so a
    repeat export never silently overwrites); returns (path, records)."""
    data, records = session_pack_bytes(home, ev, scope)
    root = out_dir if out_dir is not None else export_root(home)
    target = _next_free(root / safe_component(ev.host)
                        / _session_zip_name(ev, scope))
    return _contained_write(root, target, data), records


                                                                              


def _title_slug(title: str | None) -> str:
    """A readable, safe prefix from the session's display title: keep the
    safe filename charset, fold runs of spaces to '-', drop everything
    else. A title nothing readable survives (Chinese, say) yields "" -
    the directory then stays the bare session id, unique and honest."""
    if not title:
        return ""
    out: list[str] = []
    for ch in title.strip():
        if ch in _SAFE_COMPONENT:
            out.append(ch)
        elif ch == " " and out and out[-1] != "-":
            out.append("-")
    return "".join(out)[:40].strip("-._")


def _session_dir(ev: Evidence) -> str:
    """The everything-export's per-session directory: the FULL session id
    (the recorded host_session_id is all the sink knows - what looked
    truncated in "hermes/s/" was that test machine's actual id), prefixed
    with the title slug when one survives sanitizing. Unique either way:
    the id is; the slug only buys readability."""
    slug = _title_slug(ev.title)
    if slug:
        return safe_component(slug + "-" + ev.session_id)
    return safe_component(ev.session_id)


def _all_zip_buffer(home: Path, evidence_list: list[Evidence],
                    progress=None) -> io.BytesIO:
    """The everything-zip in memory - shared by both delivery modes (the
    bytes the browser writes and the file the disk mode writes are
    byte-identical). Progress (if given) is called as (sessions done,
    evidence) after each pack, so the page can show n/m."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, ev in enumerate(evidence_list):
            data, _records = session_pack_bytes(home, ev, "all")
            prefix = safe_component(ev.host) + "/" + _session_dir(ev)
            with zipfile.ZipFile(io.BytesIO(data)) as inner:
                for info in inner.infolist():
                    z.writestr(prefix + "/" + info.filename,
                               inner.read(info.filename))
            if progress:
                progress(i + 1, ev)
    return buf


def export_all_zip(home: Path, evidence_list: list[Evidence],
                   progress=None, out_dir: Path | None = None) -> Path:
    """One zip, one directory per host/session: <host>/<[slug-]<full session
    id>>/ holding exactly the members a single-session export writes -
    written to the (validated) chosen directory, -2/-3 on a taken name."""
    buf = _all_zip_buffer(home, evidence_list, progress)
    root = out_dir if out_dir is not None else export_root(home)
    target = _next_free(
        root / ("provelog-exports-" + time.strftime("%Y%m%d") + ".zip"))
    return _contained_write(root, target, buf.getvalue())


def export_all_bytes(home: Path, evidence_list: list[Evidence],
                     progress=None) -> tuple[bytes, str]:
    """(zip bytes, suggested base name) for browser-side delivery: no disk
    write, no path - the client writes through the folder handle it picked
    and applies the -2/-3 no-overwrite rule there (writeToDir)."""
    return (_all_zip_buffer(home, evidence_list, progress).getvalue(),
            "provelog-exports-" + time.strftime("%Y%m%d") + ".zip")


                                                                         
                                                                        
                                                                      
                                                                    
                                                                
_JOBS: dict[str, dict] = {}
_JOB_LOCK = threading.Lock()


def start_export_all(home: Path, evidence_list: list[Evidence],
                     out_dir: Path | None = None, *,
                     in_memory: bool = False) -> str:
    """Run the everything-export on a daemon thread; returns the job id.
    in_memory=True delivers the zip as bytes (export_all_bytes) instead of
    writing it - the browser-side folder the user picked receives them."""
    job_id = secrets.token_hex(8)
    with _JOB_LOCK:
        _JOBS[job_id] = {"total": len(evidence_list), "done": 0,
                         "current": "", "path": None, "error": None,
                         "name": None, "data": None}

    def _progress(n: int, ev: Evidence) -> None:
        with _JOB_LOCK:
            _JOBS[job_id]["done"] = n
            _JOBS[job_id]["current"] = ev.session_id

    def _run() -> None:
        try:
            if in_memory:
                data, name = export_all_bytes(home, evidence_list, _progress)
                with _JOB_LOCK:
                    _JOBS[job_id]["data"] = data
                    _JOBS[job_id]["name"] = name
            else:
                path = export_all_zip(home, evidence_list, _progress, out_dir)
                with _JOB_LOCK:
                    _JOBS[job_id]["path"] = str(path)
        except Exception as e:  # noqa: BLE001 - surfaced to the page as error
            with _JOB_LOCK:
                _JOBS[job_id]["error"] = f"{type(e).__name__}: {e}"

    threading.Thread(target=_run, daemon=True,
                     name="provelog-export-all").start()
    return job_id


def job_state(job_id: str) -> dict | None:
    """A snapshot of one job's progress (None when the id is unknown). The
    zip BYTES never ride in here - job_bytes() hands them to the download
    endpoint; "ready" is true once either delivery has landed."""
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        out = {k: v for k, v in job.items() if k != "data"}
        out["ready"] = bool(job.get("data")) or bool(job.get("path"))
        return out


def job_bytes(job_id: str) -> bytes | None:
    """The finished job's zip bytes (browser delivery; None for disk jobs
    and unfinished ones)."""
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        return job.get("data") if job else None
