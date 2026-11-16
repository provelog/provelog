"""Unified `provelog` command line: record locally, stamp remotely, verify."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from pathlib import Path

from .netutil import client_for

                                                                                    
                                                                              
                                                                                     
                                                                         
warnings.filterwarnings("ignore", message=r".*starlette\.testclient.*")
warnings.filterwarnings("ignore", module=r"starlette\.testclient")


def _require_server_extra(command: str, modules: tuple[str, ...] = ("fastapi",
                                                                    "uvicorn")) -> None:
    """Fail with a one-line fix when a runtime module is missing.

    Core installs support recording, verification, export and anchoring; the
    hosted stamping service and the chat viewer need fastapi/uvicorn, which
    ship in the normal install. An ImportError
    from `import fastapi` would be a traceback naming a module the user never
    chose - this turns it into the install line that fixes it. `modules`
    names what THIS command actually imports."""
    missing = []
    for name in modules:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    if not missing:
        return
    from .strings import t
    print(t("meta.require_server_extra",
            command=command,
            missing_clause=(f" ({', '.join(missing)})" if missing else "")),
          file=sys.stderr)
    raise SystemExit(2) from None


def _cmd_verify(args) -> int:
    from .verifier.verify import main as verify_main
    argv = [args.bundle]
    for root in getattr(args, "tsa_root", None) or []:
        argv += ["--tsa-root", root]
    if args.json:
        argv += ["--json"]
    if getattr(args, "strict", False):
        argv += ["--strict"]
    return verify_main(argv)


















def _display_records_dir(path: str) -> str:
    """Prefer the friendly default spelling; otherwise show the real path."""
    from pathlib import Path as _Path
    try:
        return str(_Path(path).expanduser().resolve())
    except OSError:
        return str(path)


def _print_setup_complete(result: dict) -> None:
    from .strings import t

    supported = list(result.get("supported_agents") or [])
    unsupported = list(result.get("unsupported_agents") or [])
    print(t("setup.complete_title"))
    print()
    if supported:
        print(t("setup.complete_supported", names=", ".join(supported)))
    else:
        print(t("setup.complete_no_supported"))
    if unsupported:
        print(t("setup.complete_unsupported", names=", ".join(unsupported)))
    print(t("setup.complete_link"))
    print()
    print(t("setup.complete_auto") if supported else t("setup.complete_future"))
    print()
    _store, store_path = _open_local_store()
    print(t("setup.complete_records", path=_display_records_dir(str(store_path))))
    print()
    print(t("setup.complete_commands"))


def _print_setup_paused(result: dict) -> None:
    from .strings import t

    print("ProveLog setup complete. Recording is still paused.")
    print()
    _store, store_path = _open_local_store()
    print(t("setup.complete_records", path=_display_records_dir(str(store_path))))
    print()
    print("Run provelog resume to resume recording.")


def _cmd_setup(args) -> int:
    """One command after `pip install`: install the background collector."""
    from . import onboarding
    from . import pause as pause_state

    running = onboarding.watch_process_running()
    if onboarding.watch_agent_installed() and running is not False:
        print("ProveLog is already set up.")
        print()
        print("Run provelog status to check status.")
        return 0

    result = onboarding.setup()
    if result.get("error") == "unsupported_platform":
        print(result.get("message") or "unsupported platform", file=sys.stderr)
        return 1
    platform_state = (result.get("platform") or {})
    if platform_state.get("state") == "experimental":
        print("[warn] " + str(platform_state.get("message") or ""), file=sys.stderr)
    if not result.get("watch"):
        reason = str(result.get("message") or
                     "the background collector service could not be installed")
        print("Error: Could not set up the background collector.", file=sys.stderr)
        print(f"Reason: {reason}", file=sys.stderr)
        print(file=sys.stderr)
        print("Run provelog doctor to check the installation.", file=sys.stderr)
        return 1
    if pause_state.is_paused():
        _print_setup_paused(result)
        return 0
    _print_setup_complete(result)
    return 0

def _cmd_uninstall(args) -> int:
    """Remove the collector (and any legacy viewer service). Evidence is not touched."""
    from . import onboarding
    from . import pause as pause_state
    from .strings import t

    pause_state.clear()
    result = onboarding.uninstall()
    watch = result.get("watch")
    viewer = result.get("viewer")
    errors = result.get("errors") or []
    if watch:
        print(t("setup.uninstall_collector", path=watch))
    if viewer:
        print(t("setup.uninstall_viewer", path=viewer))
    if errors:
        print("Error: Could not remove all ProveLog service files.", file=sys.stderr)
        completed = [label for label, path in
                     (("collector service file", watch), ("viewer service file", viewer))
                     if path]
        if completed:
            print(f"Removed: {', '.join(completed)}", file=sys.stderr)
        print(f"Failed: {errors[0]['item']}", file=sys.stderr)
        print(f"Reason: {errors[0]['reason']}", file=sys.stderr)
        print(file=sys.stderr)
        print(t("setup.uninstall_evidence"), file=sys.stderr)
        return 1
    if not watch and not viewer:
        print("No ProveLog service files to remove.")
    print(t("setup.uninstall_evidence"))
    return 0


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return singular if n == 1 else (plural or singular + "s")


def _cmd_status(args) -> int:
    """One glance at recording, collector, timestamp service and records."""
    from . import pause as pause_state
    from .easy import _load_config_file
    from .store import open_store, resolve_store_path

    cfg = _load_config_file()
    store_path = resolve_store_path(cfg)
    store = open_store(store_path)
    sessions = store.sessions()
    total = sum(int(s.get("records") or 0) for s in sessions)
    paused = pause_state.read()

    from . import onboarding
    collector = onboarding.watch_agent_installed()
    if not collector:
        print("Recording:          Inactive")
        print("Collector:          Not installed")
        print()
        print("Run provelog setup to set up automatic recording.")
        return 0

    running = onboarding.watch_process_running()
    if paused.get("paused"):
        if paused.get("announced"):
            print(f"Recording:          Paused since {paused.get('paused_at') or paused.get('at')}")
        else:
            print(f"Recording:          Pause requested at {paused.get('at')}")
    else:
        print("Recording:          Not paused")
    if running is False:
        print("Collector:          Not running")
        print()
        print("Run provelog doctor to check the collector.")
        return 0
    from . import capture_state
    stalled = [] if paused.get("paused") else capture_state.stalled_sources()
    print("Collector:          " + (
        f"Installed (not advancing: {len(stalled)} source file(s))"
        if stalled else "Installed"))

    endpoint = str(cfg.get("anchor_endpoint") or "https://provelog.ai").rstrip("/")
    alive = False
    try:
        with client_for(endpoint, timeout=2.0) as client:
            alive = client.get(f"{endpoint}/healthz").status_code == 200
    except Exception:
        pass
    print(f"Timestamp service:  {'Reachable' if alive else 'Unavailable'}")
    if total:
        print(f"Records:            {len(sessions)} {_plural(len(sessions), 'session')}, "
              f"{total} {_plural(total, 'record')}")
    else:
        print("Records:            None yet")
    print(f"Records directory:  {store_path}")
    try:
        ie = json.loads((Path.home() / ".provelog" / "import-errors.json").read_text(encoding="utf-8"))
        ie_errors = ie.get("errors") if isinstance(ie, dict) else None
        if isinstance(ie_errors, list) and ie_errors:
            age_min = max(0, int((time.time() - float(ie.get("at") or 0)) / 60))
            print(f"Import errors:      {len(ie_errors)} ({age_min} min ago)")
        else:
            print("Import errors:      none")
    except (OSError, ValueError, TypeError):
        print("Import errors:      none")

    if not total:
        print()
        print("Start using a supported agent to create records.")
    if not alive:
        print()
        print("Existing records remain available. Timestamp requests will retry "
              "automatically while the collector is active."
              if not paused.get("paused") else
              "Existing records remain available. Timestamp requests will retry "
              "when recording resumes.")
    return 0

def _cmd_doctor(args) -> int:
    from . import doctor
    report = doctor.run(offline=bool(getattr(args, "offline", False)))
    print(doctor.render(report, as_json=bool(getattr(args, "json", False))))
    return doctor.exit_code(report)








def _cmd_stop(args) -> int:
    """Pause the local collector. There is no per-host hook state to pause."""
    from . import onboarding
    from . import pause as pause_state

    state = pause_state.read()
    if state.get("paused"):
        if state.get("announced"):
            print("Recording is already paused.")
        else:
            print("A pause request is already pending.")
        return 0
    pause_state.request_pause()
    running = onboarding.watch_process_running()
    if not onboarding.watch_agent_installed() or running is False:
        print("Pause requested. It will take effect when the collector starts.")
        return 0
    print("Pause requested. Recording will pause on the collector's next check.")
    print("The paused interval will be marked as a gap.")
    return 0


def _cmd_resume(args) -> int:
    """Resume the local collector."""
    from . import onboarding
    from . import pause as pause_state

    state = pause_state.read()
    if state.get("paused"):
        pause_state.request_resume()
        running = onboarding.watch_process_running()
        if not onboarding.watch_agent_installed() or running is False:
            print("Resume requested. Recording will resume when the collector starts.")
            print()
            print("Run provelog doctor to check the collector.")
            return 0
        print("Resume requested. Recording will resume on the collector's next check.")
        print("The paused interval will remain marked as a gap.")
        return 0
    if state.get("resumed_at") and not state.get("done"):
        print("A resume request is already pending.")
        return 0
    print("Recording is not paused.")
    return 0


def _bundle_anchors(store, stream_id: str, records: list[dict]) -> list[dict]:
    """The latest delivered stamp, in the shape `provelog verify` reads.

    A bundle without its receipt proves the records were not changed, not when
    they existed - the same gap the viewer closed by putting anchor.json in its
    zip. The CLI export is a full-stream bundle, so the receipt can travel
    inside it. Embed only when the stamp's own Merkle root recomputes from the
    records being exported; a receipt that does not match the bundle is worse
    than no receipt.
    """
    try:
        rows = store.anchors_for_stream(stream_id)
    except (AttributeError, OSError):
        return []
    usable = [a for a in rows
              if isinstance(a, dict) and a.get("receipt") and not a.get("pending")
              and not a.get("failed")]
    if not usable:
        return []

    def _covers(anchor: dict) -> int:
        try:
            return int(anchor.get("covers_up_to") or 0)
        except (TypeError, ValueError):
            return 0

    anchors = []
    for entry in sorted(usable, key=_covers):
        covers = _covers(entry)
        if covers > len(records):
            continue
        wrapper = entry.get("receipt") or {}
        token = wrapper.get("receipt") if isinstance(wrapper, dict) and isinstance(wrapper.get("receipt"), dict) else wrapper
        from .tsa_der import normalize_receipt_tokens
        token = normalize_receipt_tokens(token)
        anchors.append({"anchor_seq": len(anchors), "covers_up_to": covers, "receipt": token})
    return anchors


def _local_bundle(store, stream_id: str) -> dict:
    """The verifiable bundle for one local stream, built from this disk.

    The latest delivered receipt travels inside `anchors` (when it matches the
    exported records), so `provelog verify bundle.json` proves the same thing
    the viewer's evidence zip does: the records were not changed, and they
    existed no later than the TSA's time."""
    from .pack import with_format_headers
    from .session_store import capture_tier_for
    records = store.get_records(stream_id)
    tier = "unknown"
    root = getattr(store, "root", None)
    if root is not None:
        for mf in root.glob("*/*/manifest.json"):
            try:
                manifest = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if stream_id in (manifest.get("streams") or {}):
                tier = str(manifest.get("capture_tier")
                           or capture_tier_for(manifest.get("capture_point")))
                break
    return with_format_headers({"format": "provelog.bundle/1", "stream_id": stream_id,
                                "server_head": store.head(stream_id),
                                "records": records,
                                "anchors": _bundle_anchors(store, stream_id, records),
                                "capture_tier": tier})


def _open_local_store(read_only: bool = False) -> tuple:
    """(store, store_path) from PROVELOG_STORE / config / default."""
    from .easy import _load_config_file
    from .store import open_store, resolve_store_path
    cfg = _load_config_file()
    path = resolve_store_path(cfg)
    return open_store(path, read_only=read_only), path


def _date_arg(value: str):
    import datetime as _dt
    try:
        return _dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


def _export_filters(args) -> dict:
    return dict(
        agents=tuple(getattr(args, "agent", None) or ()),
        project=getattr(args, "project", None),
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        search=getattr(args, "search", None),
        stamped=(True if getattr(args, "stamped", False)
                 else False if getattr(args, "unstamped", False) else None),
    )


def _export_legacy_row(stream: dict) -> dict:
    return {"session_id": "", "stream_id": str(stream.get("stream_id") or ""),
            "agent": "", "title": str(stream.get("stream_id") or ""),
            "records": int(stream.get("records") or 0), "stamped": 0,
            "last_activity": str(stream.get("last_activity") or ""),
            "project": "", "subagents": 0, "fork_of": "", "folder": "",
            "legacy": True, "this_project": False, "current": False}


def _export_rows(store, pinned: str) -> list[dict]:
    from . import export_select as sel
    rows = sel.order_candidates(store.export_candidates(), os.getcwd(), pinned)
    if rows:
        return rows
    return [_export_legacy_row(s) for s in store.list_streams()]


def _export_prepare(store, stream_id: str):
    """(bundle, files, missing, total, covered, rep, complete), or None.

    Everything the writers need, built once. `missing` names attachments the
    records reference but the disk no longer has.
    """
    from .verifier.verify import _rfc_anchor_cover, verify_bundle
    bundle = _local_bundle(store, stream_id)
    if not bundle.get("records"):
        return None
    files: dict[str, bytes] = {}
    missing: list[str] = []
    declared: list[dict] = []
    for entry in store.attachments_for_stream(stream_id):
        path = str(entry.get("path") or entry.get("file") or "")
        if "\\" in path or path.startswith("/") or ".." in path.split("/"):
            raise ValueError("Unsafe attachment path: " + path)
        name = path.rsplit("/", 1)[-1]
        if not name:
            continue
        data = store.read_attachment(stream_id, name)
        if data is None:
            missing.append(name)
            declared.append(entry)
            continue
        files[f"attachments/{name}"] = data
        declared.append(entry)
    if declared:
        from .pack import ATTACHMENTS_VERIFIER_MIN
        bundle["attachments"] = declared
        bundle["verifier_min"] = ATTACHMENTS_VERIFIER_MIN
    rep = verify_bundle(bundle, files)
    total = len(bundle.get("records") or [])
    covered = 0
    if rep.axes.time == "pass":
        covered = min(total, max((c for c, _t, _u in _rfc_anchor_cover(bundle)),
                                 default=0))
    complete = total > 0 and covered >= total
    return bundle, files, missing, total, covered, rep, complete


def _export_write(bundle, files, out, *, overwrite, bundle_only):
    from .export_flow import atomic_write
    from .pack import build_pack
    payload = json.dumps(bundle, indent=2).encode("utf-8") if bundle_only else build_pack(bundle, extra_files=files or None)
    atomic_write(out, payload, overwrite)


def _cmd_export(args) -> int:
    from .export_flow import run, say
    try:
        return run(args)
    except KeyboardInterrupt:
        say("Export interrupted.")
        return 130
    except FileExistsError as exc:
        say(f"Cannot export: {exc.filename or exc} already exists.\nChoose another path with -o, or use --overwrite.")
        return 2
    except (OSError, ValueError) as exc:
        say(f"Export failed.\nReason: {exc}\nYour recorded conversations were not changed.")
        return 1


def _record_import_errors(rows) -> None:
    """Leave the last collector-pass errors where doctor can see them."""
    import time
    errors = [{"session_id": str(r.get("session_id") or ""),
               "error": str(r.get("error") or "")}
              for r in rows if r.get("error")]
    target = Path.home() / ".provelog" / "import-errors.json"
    if not errors:
                                                                            
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"at": time.time(), "errors": errors[:20]},
                                     ensure_ascii=False), encoding="utf-8")
        os.chmod(target, 0o600)
    except OSError:
        pass


def _watch_all_once(store, *, home, workbuddy_home, kimi_homes, baseline):
    """One collection pass over every v1-supported importer.

    Cursor and Antigravity are deliberately absent: setup detects them and
    says so, but v1 does not record them.
    """
    from . import (
        import_claude_code,
        import_cline,
        import_codebuddy,
        import_codebuddy_code,
        import_codex,
        import_copilot,
        import_doubao,
        import_doubao_app,
        import_dsh,
        import_gemini,
        import_hermes,
        import_kimi,
        import_minimax,
        import_omp,
        import_openclaw,
        import_opencode,
        import_pi,
        import_qoder,
        import_vscode_agent,
        import_workbuddy,
    )

    rows = import_dsh.watch_once(store, home, since_ms=baseline)
    rows += import_workbuddy.watch_once(store, workbuddy_home, since_ms=baseline)
    rows += import_kimi.watch_once(store, kimi_homes, since_ms=baseline)
    rows += import_qoder.watch_once(store, since_ms=baseline)
    rows += import_minimax.watch_once(store, since_ms=baseline)
    rows += import_doubao.watch_once(store, since_ms=baseline)
    rows += import_doubao_app.watch_once(store, since_ms=baseline)
    rows += import_codebuddy.watch_once(store, since_ms=baseline)
    rows += import_pi.watch_once(store, since_ms=baseline)
    rows += import_omp.watch_once(store, since_ms=baseline)
    rows += import_opencode.watch_once(store, since_ms=baseline)
    rows += import_cline.watch_once(store, since_ms=baseline)
    rows += import_hermes.watch_once(store, since_ms=baseline)
    rows += import_openclaw.watch_once(store, since_ms=baseline)
    rows += import_gemini.watch_once(store, since_ms=baseline)
    rows += import_copilot.watch_once(store, since_ms=baseline)
    rows += import_claude_code.watch_once(store, since_ms=baseline)
    rows += import_codex.watch_once(store, since_ms=baseline)
    rows += import_codebuddy_code.watch_once(store, since_ms=baseline)
    rows += import_vscode_agent.watch_once(store, since_ms=baseline)
    return rows


def _cmd_watch(args) -> int:
    """Near-real-time collection from the hosts' own logs (no network changes)."""
    import time as _time

    from . import (
        import_dsh,
        import_kimi,
        import_workbuddy,
        onboarding,
    )
    from . import pause as pause_state
    from .easy import _device_key

    if getattr(args, "remove_agent", False):
        removed = onboarding.remove_watch_agent()
        print(f"removed {removed}" if removed else "no watch agent installed")
        return 0
    if getattr(args, "install_agent", False):
        path = onboarding.install_watch_agent(int(args.interval))
        print(f"watch agent installed: {path}" if path
              else "could not install the background watch agent")
        return 0

    store, store_path = _open_local_store()
    home = import_dsh.dsh_home(getattr(args, "dsh_home", None))
    workbuddy_home = import_workbuddy.workbuddy_home()
    kimi_homes = import_kimi.kimi_homes()
    baseline = onboarding.baseline_ms()
    last_image_capture: dict[str, float] = {}
    while True:
        import_dsh.begin_anchor_pass()
        state = pause_state.read()
        if state.get("done"):
            pause_state.clear()
            state = {}
        if state.get("paused"):
            if not state.get("announced"):
                                                                               
                                                                               
                                                                    
                fresh = (_time.time() * 1000 - import_dsh._iso_ms(state.get("at", ""))
                         <= max(120_000, float(args.interval) * 2000))
                if fresh:
                    _watch_all_once(store, home=home,
                                    workbuddy_home=workbuddy_home,
                                    kimi_homes=kimi_homes, baseline=baseline)
                    import_dsh.flush_anchor_pass(store)
                info = import_dsh.announce_pause(store, _device_key(), state)
                state.update(info)
                pause_state.save(state)
                print("recording paused; the paused interval will be a declared gap")
                sys.stdout.flush()
            if args.once:
                return 0
            try:
                _time.sleep(float(args.interval))
            except KeyboardInterrupt:
                return 0
            continue
        if state.get("resumed_at") and not state.get("done"):
            info = import_dsh.process_resume(store, _device_key(), state)
            state.update(info)
            pause_state.save(state)
            pause_state.clear()
            print(f"recording resumed; gap closed at {info.get('resumed_at')}")
            sys.stdout.flush()
        rows = _watch_all_once(store, home=home, workbuddy_home=workbuddy_home,
                               kimi_homes=kimi_homes, baseline=baseline)
        import_dsh.flush_anchor_pass(store)
        _record_import_errors(rows)
        for row in rows:
            if row.get("added") and not row.get("error"):
                sid = str(row.get("session_id") or "")
                now = _time.time()
                if sid and now - last_image_capture.get(sid, 0) >= 60:
                    try:
                        from . import markdown_images
                        markdown_images.capture(store, sid)
                    except Exception:
                        pass
                    last_image_capture[sid] = now
        for row in rows:
            if row.get("error"):
                print(f"{row['session_id']}: error: {row['error']}", file=sys.stderr)
            elif row.get("skipped"):
                continue
            elif row.get("added"):
                print(f"{row['session_id']}: +{row['added']} events"
                      f"{' (committed)' if row.get('committed') else ''}")
        sys.stdout.flush()
        if args.once:
            return 0
        try:
            _time.sleep(float(args.interval))
        except KeyboardInterrupt:
            return 0




def _server_extra_present() -> bool:
    """Whether the local server commands can register: fastapi must be
    importable. A probe, not an import - checking must never cost a module load."""
    from importlib.util import find_spec
    try:
        return find_spec("fastapi") is not None
    except (ImportError, ValueError):
        return False


class _Parser(argparse.ArgumentParser):
    """Route an extra-gated command on a core install to the install line.

    `provelog chat` on a core install would otherwise die with argparse's
    "invalid choice" - accurate, and useless to someone following an old
    README. The commands still EXIST; they ship with provelog, and a
    reinstall is the fix the error names."""

    _EXTRA_COMMANDS = ("browse", "view", "chat")

    def error(self, message):
        from .strings import t
        for cmd in self._EXTRA_COMMANDS:
            if f"invalid choice: {cmd!r}" in message:
                print(t("meta.extra_command", command=cmd), file=sys.stderr)
                raise SystemExit(2) from None
        super().error(message)


def build_parser() -> argparse.ArgumentParser:
    from .strings import t as _t
    p = _Parser(prog="provelog", description=_t("meta.description"),
                epilog=_t("meta.help_groups"), add_help=False,
                formatter_class=argparse.RawDescriptionHelpFormatter)
                                                                                               
                                                                            
    from . import __version__
    from .strings import t
    p.add_argument("-h", "--help", action="help",
                   help="Show this help message and exit")
    p.add_argument("--version", action="version",
                   version=f"provelog {__version__}",
                   help="Show the version and exit")
                                                                                                        
    sub = p.add_subparsers(dest="cmd", required=False, metavar="<command>",
                           help=None)
                                                                            
                                                                       
    sub.required = False
    _orig_add = sub.add_parser

    def _grouped_add(name: str, **kw):
        kw["help"] = argparse.SUPPRESS
                                                                        
                                                                       
                                                             
        return _orig_add(name, **kw)

    sub.add_parser = _grouped_add  # type: ignore[method-assign]

    _orig_format = p.format_help

    def _format_help():
        import re as _re
        text = _orig_format()
                                                                           
                                             
        text = _re.sub(r"\npositional arguments:\n  <command>(\n    .*)+\n",
                       "", text, count=1)
        return text

    p.format_help = _format_help  # type: ignore[method-assign]

    v = sub.add_parser("verify", help=t("verify.help"), epilog=t("verify.exit_codes"),
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    v.add_argument("bundle")
    v.add_argument("--tsa-root", action="append", metavar="HOST=CERT.pem",
                   help=t("verify.help_tsa_root"))
    v.add_argument("--json", action="store_true")
    v.add_argument("--strict", action="store_true", help=t("verify.sp_strict"))
    v.set_defaults(func=_cmd_verify)

    ex = sub.add_parser("export", description="Export your recorded conversations.",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog="Examples:\n  provelog export           Choose conversations to export\n  provelog export codex     Choose Codex conversations\n  provelog export claude    Choose Claude Code conversations\n  provelog export all       Review and export all conversations",
                        help="export recorded conversations")
    ex.add_argument("selector", nargs="?", metavar="agent|all", help="choose an agent, or all conversations")
    ex.add_argument("-o", "--output", dest="out", metavar="PATH", help="save to a specific file")
    ex.add_argument("--allow-unstamped", action="store_true", help="allow incomplete timestamp coverage")
    ex.add_argument("--bundle", metavar="PATH",
                    help="write the raw bundle JSON instead of a pack (advanced/debugging)")
    ex.add_argument("--stream", action="append", metavar="ID",
                    help="stream id to export (repeatable; see --list); skips the question")
    ex.add_argument("--overwrite", action="store_true", help="replace an existing output file")
    ex.add_argument("--latest", action="store_true",
                    help="export the most recently active session without asking")
    ex.add_argument("--all", action="store_true",
                    help="export every session matching the filters")
    ex.add_argument("--list", action="store_true",
                    help="list the sessions that can be exported, then exit")
    ex.add_argument("--json", action="store_true",
                    help="with --list: machine-readable output")
    ex.add_argument("--agent", action="append", metavar="NAME",
                    help="only sessions from this agent (key or display name; repeatable)")
    ex.add_argument("--project", metavar="PATH",
                    help="only sessions recorded under this project directory")
    ex.add_argument("--since", metavar="YYYY-MM-DD", type=_date_arg,
                    help="only sessions active on or after this date")
    ex.add_argument("--until", metavar="YYYY-MM-DD", type=_date_arg,
                    help="only sessions active on or before this date")
    ex.add_argument("--search", metavar="TEXT",
                    help="only sessions whose title or id contains this text")
    stamped = ex.add_mutually_exclusive_group()
    stamped.add_argument("--stamped", action="store_true",
                         help="only sessions fully covered by a timestamp")
    stamped.add_argument("--unstamped", action="store_true",
                         help="only sessions not fully covered by a timestamp")
    ex.add_argument("--limit", type=int, metavar="N",
                    help="show at most N rows in --list and the picker")
    ex.add_argument("--sort", choices=("recent", "records", "agent", "project"),
                    default="recent", help="row order (default: recent)")
    ex.add_argument("--group-by", choices=("agent", "project"),
                    help="group the --list output by agent or project")
    layout = ex.add_mutually_exclusive_group()
    layout.add_argument("--one-zip", action="store_true",
                        help="several sessions: one collection ZIP (default)")
    layout.add_argument("--separate", action="store_true",
                        help="several sessions: one pack per session, plus index.json")
    ex.add_argument("--out-dir", metavar="DIR",
                    help="output directory (separate packs default to ./provelog-exports/<time>)")
    ex.add_argument("--dry-run", action="store_true",
                    help="with several sessions: show what would be exported, write nothing")
    ex.add_argument("--require-stamp", action="store_true",
                    help="refuse to write the pack unless every record carries a "
                         "third-party timestamp")
                                                                             
                                                                                
    groups = {
        "Output": {"out", "overwrite"},
        "Selection and queries (advanced)": {"stream", "latest", "all", "list", "json", "limit", "sort", "group_by"},
        "Filters (advanced)": {"agent", "project", "since", "until", "search", "stamped", "unstamped"},
        "Timestamp policy (advanced)": {"allow_unstamped", "require_stamp"},
        "Packaging (advanced)": {"bundle", "one_zip", "separate", "out_dir", "dry_run"},
    }
    for title, destinations in groups.items():
        group = ex.add_argument_group(title)
        for action in ex._actions:
            if action.dest in destinations:
                if action in ex._optionals._group_actions:
                    ex._optionals._group_actions.remove(action)
                group._group_actions.append(action)
    ex.set_defaults(func=_cmd_export)

    st = sub.add_parser("status",
                        help="where recording lands locally, and is the anchor endpoint up?")
    st.set_defaults(func=_cmd_status)

    doc = sub.add_parser("doctor", help=t("doctor.help"))
    doc.add_argument("--json", action="store_true")
    doc.add_argument("--offline", action="store_true",
                     help=t("doctor.offline"))
    doc.set_defaults(func=_cmd_doctor)

    su = sub.add_parser("setup",
                        help="install the local collector")
    su.set_defaults(func=_cmd_setup)

    un = sub.add_parser("uninstall",
                        help="remove the collector; evidence is kept")
    un.set_defaults(func=_cmd_uninstall)

    sp = sub.add_parser("stop", help="pause local recording; nothing is deleted")
    sp.set_defaults(func=_cmd_stop)

    rs = sub.add_parser("resume", help="resume local recording")
    rs.set_defaults(func=_cmd_resume)

                                                                             
                                                                          
                                             
                                                                      
                                                                       
    if _server_extra_present():
        from .chatview.cli import register_cli as register_chat
        register_chat(sub)
    return p


def _print_welcome() -> None:
    """A bare `provelog` is usually a new user looking around. Greet them with the one command worth
    running first and where to go next, instead of an argparse 'arguments are required' error."""
    from .strings import t
    print(t("welcome.text"))


def _cmd_default() -> int:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        _print_welcome()
        return 0
    store, _ = _open_local_store(read_only=True)
    if not any(int(row.get("records") or 0) for row in store.list_streams()):
        from .easy import _load_config_file
        _print_welcome()
        if not _load_config_file():
            print("No records yet. Run provelog setup to enable recording.")
        else:
            print("No records yet. Start a supported agent, then run provelog again.")
        return 0
    _require_server_extra("browse")
    from .chatview.cli import _cmd_chat
    args = argparse.Namespace(stop=False, port=None, home=None, no_browser=False,
                              no_reuse=False, idle_timeout=0)
    return _cmd_chat(args)


def main(argv: list[str] | None = None) -> int:
    from . import strings

    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    try:
        if not hasattr(args, "func"):
            return _cmd_default()
        return args.func(args)
    except FileNotFoundError as e:
                                                                                                    
        print(strings.t("errors.file_not_found", name=e.filename), file=sys.stderr)
        print(strings.t("errors.file_not_found_hint"), file=sys.stderr)
        return 2
    except IsADirectoryError as e:
                                                                                       
        print(strings.t("errors.is_a_directory", name=e.filename), file=sys.stderr)
        print(strings.t("errors.is_a_directory_hint",
                        name=e.filename or "."), file=sys.stderr)
        return 2
    except PermissionError as e:
        print(strings.t("errors.permission_denied", name=e.filename), file=sys.stderr)
        print(strings.t("errors.permission_denied_hint"), file=sys.stderr)
        return 2
    except OSError as e:
                                                                                          
                                                                                        
        print(strings.t("errors.os_error",
                        name=getattr(e, "filename", None) or "the file",
                        reason=e.strerror or e), file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
                                                                                            
                                                                                            
                                                               
        from .verifier.verify import say_not_json
        say_not_json(e, "the file")
        return 2
    except UnicodeDecodeError:
                                                                                               
                                                                                  
        from .verifier.verify import say_not_utf8
        say_not_utf8("the file")
        return 2
    except RuntimeError as e:
                                                                                              
                                                                                             
                                                                                           
                                                 
        print(str(e), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
