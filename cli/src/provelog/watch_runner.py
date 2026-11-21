"""Internal collector entry point.

This is deliberately not a `provelog` subcommand: it is a long-running
mechanism installed by `provelog setup`, not a user action. It runs as
`python -m provelog.watch_runner`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _log_path(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    if sys.platform == "win32":
        from .onboarding import logs_dir
        return logs_dir() / "watch.log"
    return None


def _setup_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(path),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    if sys.platform == "win32":
                                                                 
        stream = open(path, "a", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream


def main(argv: list[str] | None = None) -> int:
    from .cli import _cmd_watch

    p = argparse.ArgumentParser(prog="provelog.watch_runner",
                                description="internal ProveLog collector loop")
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", default="10")
    p.add_argument("--dsh-home", help="DSH home (default: $DSH_HOME or ~/.dsh)")
    p.add_argument("--log-file", help="append collector output to this file")
    args = p.parse_args(argv)
    path = _log_path(getattr(args, "log_file", None))
    if path is not None:
        _setup_logging(path)
    try:
        return _cmd_watch(args)
    except BaseException:
        logging.exception("watch_runner exited")
        raise


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
