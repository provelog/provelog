"""Export interaction over the existing bundle and collection formats.

All members are prepared and checked before writing; waiting only refreshes
receipts for frozen records, never re-reads conversation or attachment bytes.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import select
import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import export_names as names
from . import export_select as sel
from .verifier.verify import (
    _collection_verdict,
    _exit_code_for,
    _result_word,
    _rfc_anchor_cover,
    _tsa_label,
    verify_bundle,
)

WAIT_SECONDS = 90
POLL_SECONDS = 2


def say(message=""):
    print(message, file=sys.stderr, flush=True)


def cancel():
    say("Export cancelled.\nNo file was written.")
    return 1


def publish_new(tmp, out):
    """Give the finished temp file its final name, never replacing a file.

    A hard link is atomic and fails if `out` exists. exFAT/FAT drives and some
    network shares have no hard links; there the file is created exclusively
    and copied, which still cannot overwrite an existing file.
    """
    try:
        os.link(tmp, out)
        return
    except FileExistsError:
        raise
    except OSError:
        pass
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as target, open(tmp, "rb") as source:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        Path(out).unlink(missing_ok=True)
        raise


def atomic_write(out, payload, overwrite=False):
    """Publish a complete file, without a check/replace race for no-clobber."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".provelog-", suffix=".tmp", dir=out.parent)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        if overwrite:
            os.replace(tmp, out)
        else:
            publish_new(tmp, out)
    finally:
        Path(tmp).unlink(missing_ok=True)


def assess(item):
    rep = verify_bundle(item["bundle"], item["files"])
    item["rep"] = rep
    item["word"] = _result_word(rep)
    item["total"] = len(item["bundle"]["records"])
    cover = []
    if rep.axes.time == "pass":
        for anchor in item["bundle"].get("anchors") or []:
            probe = {**item["bundle"], "anchors": [anchor]}
            checked = verify_bundle(probe, item["files"])
            if checked.axes.time == "pass" and checked.axes.integrity == "pass":
                cover.extend(_rfc_anchor_cover(probe))
    item["covered"] = min(item["total"], max((c for c, _, _ in cover), default=0))
    item["tsas"] = sorted({_tsa_label(u) for _, _, u in cover})
    item["complete"] = rep.axes.time == "pass" and rep.axes.coverage == "complete"
    item["state"] = (
        "complete"
        if item["complete"]
        else "partial"
        if rep.axes.time == "pass" and rep.axes.coverage == "partial"
        else "unsupported"
        if rep.axes.time == "untrusted"
        else "none"
    )
    return item


def blocked(item):
    rep = item["rep"]
    return not rep.ok or rep.axes.integrity != "pass" or rep.axes.attribution == "fail"


def reason(item):
    findings = item["rep"].findings
    return next((str(f.detail) for f in findings if f.severity in ("error", "fail")), item["word"])


def tsa_suffix(items):
    labels = sorted({label for item in items for label in item["tsas"]})
    return " (" + " and ".join(labels) + ")" if labels else ""


def describe_status(items):
    if len(items) == 1:
        item = items[0]
        n, c = item["total"], item["covered"]
        if item["complete"]:
            say(f"All {n:,} records have verified timestamps{tsa_suffix(items)}.")
        elif item["state"] == "partial":
            say(f"{c:,} of {n:,} records have verified timestamps{tsa_suffix(items)}.")
            say(f"{n - c:,} records have no verified time proof.")
        elif item["state"] == "unsupported":
            say(
                "A timestamp is present, but its authority is not supported by this verifier.\nSupported authorities: Sectigo and FreeTSA."
            )
        else:
            say(f"None of the {n:,} records have a verified timestamp.")
        if not item["complete"]:
            if item["state"] == "partial":
                say(f"\nProveLog verification will report:\n  RESULT: {item['word']}")
            else:
                say(
                    f"\nProveLog verification will report RESULT: {item['word']} (exit {_exit_code_for(item['rep'], False)})."
                )
            if item["state"] == "none":
                say("The export can save your records, but cannot prove when they already existed.")
    else:
        say(f"{len(items)} conversations selected.\n")
        labels = {
            "complete": "fully timestamped",
            "partial": "partially timestamped",
            "none": "without verified timestamps",
            "unsupported": "with unsupported timestamp authorities",
        }
        for state, label in labels.items():
            n = sum(i["state"] == state for i in items)
            if n:
                say(f"  {n} {label}")
        invalid = sum(i["word"] == "INVALID" for i in items)
        partial = sum(i["state"] == "partial" for i in items)
        if invalid:
            if partial:
                say(f"{partial} conversations will verify as VERIFIED (partial timestamp coverage).")
            say(f"{invalid} conversations will verify as INVALID.")
        say(f"\nThe collection will verify as RESULT: {_collection_verdict([i['word'] for i in items])}.")
        if all(i["complete"] for i in items):
            say(f"All records have verified timestamps{tsa_suffix(items)}.")
    for item in items:
        if item["rep"].axes.attribution == "absent":
            say("Note: No device signature.")
            break


def waiting_state(store, items):
    """Unknown permits a bounded attempt; only explicit pending defaults to wait."""
    states = []
    for item in items:
        if item["complete"]:
            continue
        anchors = store.anchors_for_stream(item["row"]["stream_id"])
        pending = any(
            a.get("pending") and not a.get("failed") and int(a.get("covers_up_to") or 0) >= item["total"]
            for a in anchors
        )
        states.append(
            "pending"
            if pending
            else "blocked"
            if item["state"] == "unsupported" or any(a.get("failed") for a in anchors)
            else "unknown"
        )
    if states and all(s == "pending" for s in states):
        return "pending"
    if states and all(s == "blocked" for s in states):
        return "blocked"
    return "unknown"


def refresh(store, items):
    from .cli import _bundle_anchors

    for item in items:
        sid = item["row"]["stream_id"]
        receipts = _bundle_anchors(store, sid, item["bundle"]["records"])
        if receipts:
            candidate = copy.deepcopy(item)
            candidate["bundle"]["anchors"] = receipts
            assess(candidate)
            if blocked(candidate):
                raise ValueError("The refreshed timestamp failed verification: " + reason(candidate))
            if candidate["covered"] >= item["covered"]:
                item.update(candidate)
        elif not item["complete"]:
            if any(
                a.get("receipt")
                and not a.get("pending")
                and not a.get("failed")
                and int(a.get("covers_up_to") or 0) > item["total"]
                for a in store.anchors_for_stream(sid)
            ):
                raise ValueError(
                    'The latest timestamp cannot be used with this export snapshot.\n\nNo file was written.\nRun "provelog export" again to select the latest records.'
                )


def enter_pressed(timeout):
    if os.name == "nt":
        import msvcrt

        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if msvcrt.kbhit() and msvcrt.getwch() in ("\r", "\n"):
                return True
            time.sleep(0.05)
        return False
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            sys.stdin.readline()
            return True
    except (OSError, ValueError):
        return True
    return False


def wait_for_timestamps(store, items):
    end = time.monotonic() + WAIT_SECONDS
    say("Press Enter to stop waiting.")
    while True:
        refresh(store, items)
        if all(i["complete"] for i in items):
            return
        if waiting_state(store, items) == "blocked":
            say("Timestamping is currently unavailable.")
            return
        covered, total = sum(i["covered"] for i in items), sum(i["total"] for i in items)
        print(
            f"\rWaiting for timestamps… {covered:,} of {total:,} records covered.   ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        remaining = end - time.monotonic()
        if remaining <= 0:
            say("\nTimestamps are still pending.")
            return
        if enter_pressed(min(POLL_SECONDS, remaining)):
            say()
            return


def approve(store, items, args):
    interactive = sys.stdin.isatty()
    complete = all(i["complete"] for i in items)
    if args.require_stamp and not complete:
        say("Export requires a verified timestamp for every record.")
        say(
            f"\nTimestamped: {sum(i['covered'] for i in items):,} of {sum(i['total'] for i in items):,} records\nNo file was written.\n\nWait for timestamping to complete, or retry without --require-stamp."
        )
        return 3
    if args.dry_run:
        return 0
    if not interactive:
        if not complete and not args.allow_unstamped:
            say(
                "The selected records are not fully timestamped.\n\nNo file was written.\nUse --allow-unstamped to export without full timestamp coverage."
            )
            return 3
        return 0
    confirm = args.confirm_all
    if (complete or args.allow_unstamped) and not confirm:
        return 0
    waited = False
    while True:
        complete = all(i["complete"] for i in items)
        state = waiting_state(store, items) if not complete and not args.allow_unstamped else "blocked"
        can_wait = state != "blocked"
        if can_wait and state == "unknown":
            say(
                "The timestamping status could not be confirmed.\nWaiting may improve coverage, but may not resolve every invalid result."
            )
        default = "e" if waited and complete else "w" if state == "pending" else "q"
        action = f"export all {len(items)}" if confirm else "export"
        if not complete:
            action += " now"
        say("\n" + ("[w] wait and check · " if can_wait else "") + f"[e] {action} · [q] cancel\n")
        print(f"Choose [{default}]: ", end="", file=sys.stderr, flush=True)
        try:
            answer = input().strip().lower() or default
        except EOFError:
            answer = "q"
        if answer == "q":
            return cancel()
        if answer == "e":
            return 0
        if answer == "w" and can_wait:
            wait_for_timestamps(store, items)
            describe_status(items)
            waited = True
        else:
            say("Choose one of the options above.")


def summary(items, out, separate=False):
    print("Export complete.\n")
    if len(items) == 1:
        print(f"Conversation: {sel.describe(items[0]['row'])}")
    else:
        print(f"Conversations: {len(items)}")
    print(f"{'Directory:' if separate else 'File:':14}{Path(out).resolve()}")
    if separate:
        print(f"Index:        {(Path(out) / 'index.json').resolve()}")
    if len(items) == 1:
        item = items[0]
        state = item["state"]
        value = (
            f"All {item['total']:,} records covered{tsa_suffix(items)}"
            if item["complete"]
            else f"{item['covered']:,} of {item['total']:,} records covered{tsa_suffix(items)}"
            if state == "partial"
            else "Authority not supported by this verifier"
            if state == "unsupported"
            else "None"
        )
        print("Timestamps:   " + value)
        if state == "partial":
            print(f"              {item['total'] - item['covered']:,} records have no verified time proof")
        if not item["complete"]:
            note = (
                " — no verified timestamp"
                if state == "none"
                else " — timestamp authority not supported"
                if state == "unsupported"
                else ""
            )
            print("Verification: " + item["word"] + note)
    else:
        counts = [
            (s, sum(i["state"] == s for i in items)) for s in ("complete", "partial", "none", "unsupported")
        ]
        value = (
            f"All {len(items)} conversations fully covered{tsa_suffix(items)}"
            if all(i["complete"] for i in items)
            else " · ".join(f"{n} {s}" for s, n in counts if n) + " (conversations)"
        )
        print("Timestamps:   " + value)
        if not all(i["complete"] for i in items):
            word = _collection_verdict([i["word"] for i in items])
            invalid = sum(i["word"] == "INVALID" for i in items)
            note = f" — {invalid} conversations do not pass verification" if invalid else ""
            print("Verification: " + word + note)
            if invalid:
                for state, explanation in (
                    ("none", "have no verified timestamp"),
                    ("unsupported", "have timestamps only from unsupported authorities"),
                    ("partial", "have only partial timestamp coverage"),
                ):
                    count = sum(i["state"] == state for i in items)
                    if count:
                        print(f"{count} conversations {explanation}.")
    if any(i["rep"].axes.attribution == "absent" for i in items):
        print("Note: No device signature.")
    missing = sum(len(i["missing"]) for i in items)
    if missing:
        print(
            f"Note: {missing} attachment{'s were' if missing != 1 else ' was'} missing and {'were' if missing != 1 else 'was'} not included."
        )
    selected = {i["row"]["stream_id"] for i in items}
    omitted = {s for i in items for s in i["row"].get("subagent_streams", []) if s not in selected}
    if omitted:
        print(
            f"Note: {len(omitted)} subagent conversations are not included.\nExport them separately if needed."
        )


def write_items(items, args):
    from .collection import write_collection
    from .pack import build_pack

    if args.separate:
        root = Path(args.out_dir) if args.out_dir else Path("provelog-exports") / names.batch_dir_name()
        if root.exists() and any(root.iterdir()):
            say("Output directory is not empty. Use a new directory with --out-dir.\nNo file was written.")
            return 2
        root.mkdir(parents=True, exist_ok=True)
        written, index = [], []
        try:
            for item in items:
                out = names.next_free(root / names.pack_basename(item["row"]))
                atomic_write(out, build_pack(item["bundle"], extra_files=item["files"] or None))
                written.append(str(out.resolve()))
                index.append({**item["row"], "file": out.name, "status": "ok", "stamped": item["covered"]})
            atomic_write(
                root / "index.json",
                json.dumps(
                    {
                        "summary": {"requested": len(items), "ok": len(items), "failed": 0, "skipped": 0},
                        "sessions": index,
                    },
                    indent=2,
                ).encode(),
            )
        except BaseException:
            say(
                "Export did not complete. Files already written:\n" + "\n".join(written)
                if written
                else "No export file was created."
            )
            raise
        summary(items, root, True)
        return 0
    explicit = args.bundle or args.out
    default = names.pack_basename(items[0]["row"]) if len(items) == 1 else names.collection_name(len(items))
    out = Path(explicit) if explicit else names.next_free(Path(args.out_dir or ".") / default)
    overwrite = bool(explicit and args.overwrite)
    if out.exists() and not overwrite:
        say(
            f"Cannot export: {out} already exists.\n\nChoose another path with -o,\nor use --overwrite to replace this file.\n\nNo file was written."
        )
        return 2
    say(f"Exporting{(' ' + str(len(items)) + ' conversations') if len(items) > 1 else ''}…")
    if len(items) == 1:
        item = items[0]
        payload = (
            json.dumps(item["bundle"], indent=2).encode()
            if args.bundle
            else build_pack(item["bundle"], extra_files=item["files"] or None)
        )
        atomic_write(out, payload, overwrite)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".provelog-collection-", dir=out.parent)
        os.close(fd)
        used = set()
        members = []
        for item in items:
            base = names.pack_basename(item["row"])[:-4]
            name, number = base, 2
            while name in used:
                name = f"{base}-{number}"
                number += 1
            used.add(name)
            members.append(
                {
                    "name": name,
                    "bundle": item["bundle"],
                    "files": item["files"],
                    "meta": {
                        **{k: item["row"].get(k, "") for k in ("session_id", "stream_id", "agent", "title")},
                        "records": item["total"],
                        "stamped": item["covered"],
                    },
                }
            )
        try:
            write_collection(Path(temp), members, overwrite=True)
            if overwrite:
                os.replace(temp, out)
            else:
                publish_new(temp, out)
        finally:
            Path(temp).unlink(missing_ok=True)
    summary(items, out)
    return 0


def run(args):
    from . import cli
    from .easy import _load_config_file

    selector = args.selector
    if selector and ("/" in selector or "\\" in selector or selector.lower().endswith((".zip", ".json"))):
        say(f'Output paths now use -o.\n\nUse:\n  provelog export -o "{selector}"')
        return 2
    if selector and selector.casefold() == "all":
        args.all = True
    conflicts = [
        (args.stream and selector, "--stream cannot be combined with an agent selector or all."),
        (args.all and (args.stream or args.latest), "--all cannot be combined with --stream or --latest."),
        (
            args.allow_unstamped and args.require_stamp,
            "--allow-unstamped cannot be combined with --require-stamp.",
        ),
        (
            args.separate and (args.out or args.overwrite or args.bundle),
            "--output, --overwrite and --bundle cannot be combined with --separate.\nUse a new output directory with --out-dir.",
        ),
        (args.bundle and args.one_zip, "--bundle cannot be combined with --one-zip."),
        (args.bundle and args.out, "--bundle cannot be combined with --output."),
        (args.json and not args.list, "--json can only be used with --list."),
        (
            args.limit is not None and (args.limit < 1 or args.all),
            "--limit must be positive and cannot be combined with --all.",
        ),
    ]
    for condition, message in conflicts:
        if condition:
            say(message)
            return 2
    if args.out or args.bundle:
        target = Path(args.out or args.bundle)
        if target.exists() and not args.overwrite and not args.list and not args.dry_run:
            say(
                f"Cannot export: {target} already exists.\n\nChoose another path with -o,\nor use --overwrite to replace this file.\n\nNo file was written."
            )
            return 2
    store, _ = cli._open_local_store(read_only=True)
    rows = [
        r
        for r in cli._export_rows(store, str(_load_config_file().get("stream_id") or ""))
        if r["records"] > 0
    ]
    chosen_agent = None
    if selector and selector.casefold() != "all":
        chosen_agent = sel.agent_key(selector, rows)
        if chosen_agent is None:
            import difflib

            say(f"Unknown agent: {selector}\n")
            hints = difflib.get_close_matches(
                selector.lower(), sorted({str(r.get("agent") or "") for r in rows}), n=1
            )
            if not hints:
                hints = difflib.get_close_matches(selector.lower(), sel._AGENT_LABELS, n=1)
            if hints:
                say(f"Did you mean {hints[0]}?")
            say('Run "provelog export" to choose from all agents.')
            return 2
        if args.agent and any(sel.agent_key(a, rows) != chosen_agent for a in args.agent):
            say("An agent selector cannot be combined with a different --agent filter.")
            return 2
        args.agent = [chosen_agent]
    args.agent = [sel.agent_key(a, rows) or a for a in args.agent or []]
    rows = sel.sort_rows(sel.filter_rows(rows, **cli._export_filters(args)), args.sort)
    if args.list:
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        elif not rows:
            print("No conversations match these filters.")
        else:
            print(sel.render_list(rows, args.limit, with_stream=True, group_by=args.group_by))
            print(f"\n{sel.render_summary(rows)}\n\nExport one with:\n  provelog export --stream <id>")
        return 0
    args.confirm_all = bool(args.all)
    streams = args.stream or (
        []
        if selector or args.latest or args.all
        else [os.environ["PROVELOG_STREAM_ID"]]
        if os.environ.get("PROVELOG_STREAM_ID")
        else []
    )
    if streams:
        by_id = {r["stream_id"]: r for r in rows}
        targets = [by_id.get(s) or cli._export_legacy_row({"stream_id": s}) for s in dict.fromkeys(streams)]
    elif not rows:
        say(
            f"No {sel.agent_label(chosen_agent)} conversations available to export yet."
            if chosen_agent
            else "No conversations available to export yet."
        )
        say(
            '\nRun "provelog export" to choose from all agents.'
            if chosen_agent
            else '\nRun "provelog status" to check recording.'
        )
        return 1
    elif args.all:
        targets = rows
    elif args.latest:
        targets = [max(rows, key=lambda r: str(r.get("last_activity") or ""))]
    else:
        candidates = rows[: args.limit] if args.limit else rows
        targets, code = sel.choose(
            candidates,
            interactive=sys.stdin.isatty(),
            agent=chosen_agent,
            on_all=lambda: setattr(args, "confirm_all", True),
        )
        if code == -1:
            say("Opening your conversations in the browser.\nNo export file has been written.")
            from .chatview.cli import _cmd_chat

            return _cmd_chat(
                argparse.Namespace(
                    stop=False, port=None, home=None, no_browser=False, no_reuse=False, idle_timeout=0
                )
            )
        if code:
            return code
    if args.bundle and len(targets) > 1:
        say("--bundle writes one JSON file; it cannot hold several conversations.")
        return 2
    if args.confirm_all and selector and selector.casefold() == "all":
        say(f"Selected all {len(targets)} conversations across all agents and projects.\n")
    elif len(targets) == 1:
        say("Selected: " + sel.describe(targets[0]) + "\n")
    else:
        say(f"Selected {len(targets)} conversations.\n")
    items, failures, backend_failures = [], [], []
    for index, row in enumerate(targets, 1):
        if len(targets) == 1:
            say("Checking records, attachments and timestamps…")
        elif sys.stderr.isatty():
            print(f"\rChecking {index} of {len(targets)} conversations…", end="", file=sys.stderr, flush=True)
        elif index == 1:
            say(f"Checking {len(targets)} conversations…")
        try:
            prep = cli._export_prepare(store, row["stream_id"])
            if prep is None:
                raise ValueError("No records found.")
            bundle, files, missing, *_ = prep
            item = assess(
                {"row": dict(row), "bundle": copy.deepcopy(bundle), "files": files, "missing": missing}
            )
            if item["rep"].result == "cannot_verify":
                                                                            
                                                                           
                backend_failures.append(
                    f"  {sel._title(row)}: {reason(item)}")
                continue
            if blocked(item):
                raise ValueError(reason(item))
            items.append(item)
            if missing:
                n = len(missing)
                say(
                    f"Warning: {n} attachment{'s are' if n != 1 else ' is'} missing from disk and will not be included.\nThe recorded conversation will still be exported."
                )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            failures.append(f"  {sel._title(row)}: {exc}")
    if len(targets) > 1 and sys.stderr.isatty():
        say()
    if backend_failures:
        say(
            "Cannot export: verification backend unavailable.\n"
            + "\n".join(backend_failures)
            + "\n\nNo file was written. The conversations are intact; this machine "
              "cannot decode RFC 3161 tokens.\n"
              "Verify a pack with verify-offline.html in a browser, or export on a "
              "machine where the native extension is permitted (a WDAC/AV policy can "
              "block it)."
        )
        return 1
    if failures:
        say(
            "Cannot export the selected conversations.\n"
            + "\n".join(failures)
            + '\n\nNo file was written.\nRun "provelog export" to choose fewer conversations.'
        )
        return 1
    describe_status(items)
    code = approve(store, items, args)
    if code:
        return code
    if args.dry_run:
        print(f"Would export {len(items)} conversation(s). No file was written.")
        return 0
    return write_items(items, args)
