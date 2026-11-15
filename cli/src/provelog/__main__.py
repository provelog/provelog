"""`python -m provelog`: the CLI through the interpreter, immune to PATH order.

Every subcommand works identically to the `provelog` console script:
    python -m provelog verify bundle.json
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
