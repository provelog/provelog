"""Internal collector entry point.

This is deliberately not a `provelog` subcommand: it is a long-running
mechanism installed by `provelog setup`, not a user action. It runs as
`python -m provelog.watch_runner`.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    from .cli import _cmd_watch

    p = argparse.ArgumentParser(prog="provelog.watch_runner",
                                description="internal ProveLog collector loop")
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", default="10")
    p.add_argument("--dsh-home", help="DSH home (default: $DSH_HOME or ~/.dsh)")
    args = p.parse_args(argv)
    return _cmd_watch(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
