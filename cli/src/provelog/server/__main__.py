"""Operator relay entry point: ``python -m provelog.server``.

This is deliberately a module, not a second installed executable. The public
CLI has exactly nine subcommands; the relay is operated separately.
"""

from __future__ import annotations

from .app import run

if __name__ == "__main__":
    run()
