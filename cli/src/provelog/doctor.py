"""`provelog doctor`: one local self-check with actionable next steps.

This is deliberately an orchestrator, not a second verifier. Chain checks call
the same verifier the evidence pack uses; stamp freshness reads the same
manifests through the local store; the key path is the same path recording uses.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class Check:
    name: str
    status: str                                          
    detail: str
    fix: str = ""


def _store_path(home: Path) -> Path:
    from .easy import _load_config_file
    from .store import resolve_store_path

    return Path(resolve_store_path(_load_config_file()))


def _latest_stamp(root: Path) -> str | None:
    latest = None
    for mf in root.glob("*/*/manifest.json"):
        try:
            manifest = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for anchor in manifest.get("anchors") or []:
                                                                      
                                                                         
                                                                           
                                                                
            token: Any = anchor
            value = ""
            for _ in range(6):
                if not isinstance(token, dict):
                    break
                value = str(token.get("gen_time") or "")
                if value:
                    break
                nested = token.get("receipt")
                if not isinstance(nested, dict):
                    nested = token.get("agg_receipt")
                token = nested
            if value and (latest is None or value > latest):
                latest = value
    return latest


def _stamp_age_hours(value: str | None) -> float | None:
    if not value:
        return None
    from datetime import UTC, datetime
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - dt).total_seconds() / 3600)


def _backend_check() -> Check:
    """Can this machine decode RFC 3161 tokens at all?

    Separate from `chains` because a blocked/absent native extension says
    nothing about the records: it says this reader cannot judge the time here.
    The pure-Python fallback means a blocked native extension is no longer a
    hard failure.
    """
    hint = ("verify a pack with verify-offline.html in a browser, or run "
            "provelog on a machine where the RFC 3161 extension is permitted; "
            "`python -m provelog` works when the provelog.exe launcher is blocked")
    from .tsa_backend import probe
    kind, detail = probe()
    if kind == "unavailable":
        return Check("backend", "fail",
                     f"no RFC 3161 verifier can run: {detail[:300]}", hint)
    if kind == "pure":
        return Check("backend", "ok",
                     "RFC 3161 verifier: pure-Python fallback "
                     f"(native unavailable: {detail[:160]})")
    return Check("backend", "ok", "RFC 3161 verifier: native")


def _viewer_check() -> Check:
    """The viewer is optional; its native web stack may be blocked.

    export/verify must remain usable when fastapi/pydantic/uvicorn cannot
    load, so this is a warning, not a health failure.
    """
    try:
        import fastapi
        import pydantic
        import uvicorn
    except (ImportError, OSError) as exc:
        return Check(
            "viewer", "warn",
            f"viewer web stack cannot load: {exc}",
            "export and verify are unaffected; use `provelog export` and open "
            "verify-offline.html from the pack")
    pv = str(getattr(pydantic, "VERSION", ""))
    if not pv.startswith("2."):
        return Check(
            "viewer", "warn",
            f"pydantic {pv or 'unknown'} is not v2; the viewer may not load",
            "reinstall with `pip install -U 'provelog'` to get the pinned "
            "pydantic 2.x web stack")
    fv = str(getattr(fastapi, "__version__", "?"))
    uv = str(getattr(uvicorn, "__version__", "?"))
    return Check("viewer", "ok",
                 f"viewer dependencies load (fastapi {fv}, pydantic {pv}, "
                 f"uvicorn {uv})")


def _anchor_error_check() -> Check | None:
    """Surface the relay's last 409; a reinstall may have opened a new stream."""
    from . import anchoring
    err = anchoring.last_anchor_error()
    if not err:
        return None
    age = max(0, int((time.time() - float(err.get("at") or 0)) / 60))
    detail = str(err.get("detail") or "").replace("\n", " ")[:240]
    return Check(
        "anchor", "warn",
        f"anchor refused ({err.get('status') or 409}) {age} min ago: {detail}",
        "a reinstall starts a new local chain; ProveLog opens a new relay stream "
        "automatically. If this persists, export and verify offline.")


def _chain_check(home: Path, store_path: Path) -> Check:
    from .cli import _local_bundle
    from .store import open_store
    from .verifier.verify import verify_bundle

    if not store_path.is_dir():
        return Check("chains", "fail", "record store does not exist",
                     "run `provelog setup`")
    try:
        store = open_store(str(store_path), read_only=True)
        try:
            streams = [row.get("stream_id") for row in store.list_streams()
                       if row.get("stream_id")]
            checked = 0
            broken: list[str] = []
            unjudged = 0
            for stream in streams:
                bundle = _local_bundle(store, stream)
                if not bundle.get("records"):
                    continue
                checked += 1
                rep = verify_bundle(bundle)
                if rep.result == "cannot_verify":
                                                                             
                    unjudged += 1
                    continue
                if rep.axes.integrity != "pass":
                    detail = next((f.detail for f in rep.findings
                                   if f.severity == "fail"), rep.result)
                    broken.append(f"{stream}: {detail}")
        finally:
            store.close()
    except Exception as exc:                                       
        return Check("chains", "fail", f"could not check chains: {exc}",
                     "run `provelog status` and inspect the store")
    if broken:
        return Check("chains", "fail", f"{len(broken)} stream failures: {broken[0]}",
                     "export the affected session and run `provelog verify`")
    if unjudged:
        return Check("chains", "skip",
                     f"{checked - unjudged} stream(s) verified; {unjudged} could not be "
                     f"judged because the verification backend is unavailable",
                     "fix the backend first (see the backend check); the records "
                     "themselves were not reported as broken")
    return Check("chains", "ok", f"{checked} streams verified")


def _disk_check(root: Path) -> Check:
    try:
        used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        free = __import__("shutil").disk_usage(root).free
    except OSError as exc:
        return Check("disk", "warn", f"could not measure the store: {exc}")
    def size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
            n /= 1024
        return str(n)
    if free < 1_000_000_000:
        return Check("disk", "warn",
                     f"Records use {size(used)}; only {size(free)} available",
                     "archive old sessions and free disk space")
    return Check("disk", "ok", f"Records use {size(used)}; {size(free)} available")


def _update_info(offline: bool) -> dict[str, Any]:
    """The update notice is separate from health checks."""
    from . import __version__, version

    if offline:
        return {"status": "skipped", "latest": None, "reason": "offline"}
    info = version.check()
    if info is None:
        return {"status": "unavailable", "latest": None, "reason": "check_failed"}
    latest = info["latest"]
    if version.is_older(__version__, latest):
        return {"status": "available", "latest": latest, "reason": None}
    return {"status": "current", "latest": latest, "reason": None}


@dataclass
class Report:
    version: str
    update: dict[str, Any]
    checks: list[Check]
    reminders: list[dict[str, Any]]

    @property
    def ok(self) -> bool:
        return not any(c.status == "fail" for c in self.checks)


def _import_error_check() -> Check | None:
    """Recent collector import failures are not silently healthy."""
    import time

    target = Path.home() / ".provelog" / "import-errors.json"
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    errors = data.get("errors") if isinstance(data, dict) else None
    if not isinstance(errors, list) or not errors:
        return None
    try:
        age = time.time() - float(data.get("at") or 0)
    except (TypeError, ValueError):
        age = 1e9
    if age > 24 * 3600:
        return None
    age_min = max(0, int(age // 60))
    when = ("in the last collector pass" if age < 300
            else f"last seen {age_min} min ago")
    return Check("imports", "warn",
                 f"{len(errors)} import error(s) {when}",
                 "see ~/.provelog/import-errors.json and `provelog status`")


def run(home: Path | None = None, *, offline: bool = False) -> Report:
    from . import __version__, onboarding
    from .easy import _load_config_file, device_key_path

    home = home or Path.home()
    root = _store_path(home)
    checks: list[Check] = []
    reminders: list[dict[str, Any]] = []

    key = device_key_path()
    if not key.is_file():
        checks.append(Check("key", "fail", f"Device key not found: {key}",
                            "run `provelog setup`; old records cannot verify "
                            "attribution without it"))
    else:
        mode = stat.S_IMODE(key.stat().st_mode)
                                                                              
                                                                              
        if sys.platform != "win32" and mode & 0o077:
            checks.append(Check("key", "warn",
                                f"Device key permissions are too open: {key}",
                                "chmod 600 the key; old records still verify, "
                                "but the key is exposed"))
        else:
            checks.append(Check("key", "ok", f"Device key found: {key}"))
        reminders.append({
            "code": "key_backup",
            "detail": "Back up your device key separately from your records.",
            "path": str(key),
        })

    if not root.is_dir():
        checks.append(Check("store", "fail",
                            f"Records directory was not found: {root}",
                            "run `provelog setup`"))
    elif not os.access(root, os.R_OK):
        checks.append(Check("store", "fail",
                            f"Records directory is not readable: {root}",
                            "check the directory permissions"))
    else:
        checks.append(Check("store", "ok",
                            f"Records directory is readable: {root}"))
        backend = _backend_check()
        checks.append(backend)
        checks.append(_viewer_check())
        anchor = _anchor_error_check()
        if anchor is not None:
            checks.append(anchor)
        checks.append(_chain_check(home, root))

    latest = _latest_stamp(root)
    age = _stamp_age_hours(latest)
    if latest is None:
        checks.append(Check("timestamp", "warn", "No timestamp found",
                            "timestamping is automatic; check `provelog status` "
                            "and the anchor endpoint"))
    elif age is not None and age > 24:
        checks.append(Check("timestamp", "warn",
                            f"Latest timestamp is {age:.1f} hours old ({latest})",
                            "check TSA reachability and the anchor endpoint"))
    else:
        checks.append(Check("timestamp", "ok", f"Latest timestamp: {latest}"))

    cfg = _load_config_file()
    endpoint = str(cfg.get("anchor_endpoint") or "https://provelog.ai").rstrip("/")
    if offline:
        checks.append(Check("tsa", "skip", "Timestamp authority check skipped (--offline)"))
    else:
        ok = False
        error = ""
        try:
            from .netutil import client_for
            with client_for(endpoint, timeout=3.0) as client:
                ok = client.get(f"{endpoint}/healthz").status_code == 200
        except Exception as exc:
            error = str(exc)
        checks.append(Check("tsa", "ok" if ok else "warn",
                            (f"Timestamp authority is reachable: {endpoint}"
                             if ok else
                             f"Timestamp authority is unreachable: {endpoint}"),
                            "" if ok else ("records will wait for a stamp; check "
                                           f"network/TSA ({error[:100]})")))

    if onboarding.watch_agent_installed():
        checks.append(Check("collector", "ok", "Background collector is installed"))
        installed_version = onboarding.collector_version()
        if installed_version and installed_version != __version__:
            checks.append(Check(
                "collector-version", "warn",
                f"collector was installed by {installed_version}; this CLI is {__version__}",
                "run `provelog setup` to restart it with the installed version"))
        from . import capture_state
        stalled = capture_state.stalled_sources()
        if stalled:
            checks.append(Check(
                "collector", "warn",
                f"{len(stalled)} source file(s) grew but capture has not advanced",
                "restart the collector with `provelog setup` and check "
                "see ~/.provelog/import-errors.json and `provelog status`; "
                "otherwise new turns are not recorded"))
    else:
        checks.append(Check("collector", "warn", "Background collector is not installed",
                            "run `provelog setup`; new conversations will not be recorded"))

    err = _import_error_check()
    if err is not None:
        checks.append(err)

    checks.append(_disk_check(root) if root.is_dir()
                  else Check("disk", "skip", "Records directory does not exist"))
    return Report(__version__, _update_info(offline), checks, reminders)


def exit_code(report: Report) -> int:
    return 0 if report.ok else 1


def render(report: Report, *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps({
            "version": report.version,
            "update": report.update,
            "checks": [asdict(c) for c in report.checks],
            "reminders": report.reminders,
            "ok": report.ok,
        }, indent=2, ensure_ascii=False)
    marks = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[fail]", "skip": "[skip]"}
    lines = ["ProveLog health check", f"Version: {report.version}", ""]
    for c in report.checks:
        lines.append(f"{marks.get(c.status, '[?]   ')} {c.name:<12} {c.detail}")
        if c.fix:
            lines.append(f"       Next step: {c.fix}")
    fails = sum(c.status == "fail" for c in report.checks)
    warns = sum(c.status == "warn" for c in report.checks)
    def count(n: int, word: str) -> str:
        return f"{n} {word}" if n == 1 else f"{n} {word}s"
    lines.append("")
    lines.append(f"Result: {count(fails, 'failure')}, {count(warns, 'warning')}")
    if report.reminders:
        for reminder in report.reminders:
            lines.append("")
            lines.append(f"Reminder: Back up {reminder['path']} separately "
                         "from your records.")
    update = report.update
    if update.get("status") == "available":
        lines.append("")
        lines.append(f"Update available: {update['latest']} (installed: {report.version})")
        lines.append("Run python -m pip install --upgrade provelog to update.")
    elif update.get("status") == "skipped":
        lines.append("")
        lines.append("Update check skipped (--offline).")
    elif update.get("status") == "unavailable":
        lines.append("")
        lines.append("Update check unavailable.")
    return "\n".join(lines)
