                                                                    
# Copyright (c) DeepSeek and contributors, MIT.
# See THIRD_PARTY_NOTICES.md for the full attribution.

"""The local history viewer: `provelog chat`.

A read-only browser over the conversation transcripts the device's AI
coding CLIs already keep on disk (Claude Code, Codex, ...). It binds
127.0.0.1 only, serves a single self-contained page with zero external
requests, and never writes, uploads or deletes anything - the viewer is a
pair of eyes, not a recorder.

Structure (all lazy from the CLI's point of view: nothing here is imported
until `provelog chat` runs, so a plain `provelog --help` stays as fast as
ever):

  model.py          the unified internal format every reader normalizes to
  readers/          one module per host - discover() + load(), testable solo
  app.py            the FastAPI factory (token-gated, localhost-only)
  dsh_page.py       the page (self-contained HTML/CSS/JS, dsh look)
  cli.py            subcommand registration + the run loop
"""

from __future__ import annotations
