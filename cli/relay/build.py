#!/usr/bin/env python3
"""Build the operator-only ``provelog-relay`` distribution.

The server source stays in ``cli/src/provelog/server`` so the client and relay
share one implementation. Setuptools cannot package a directory outside its
project root reliably, so this script stages a comment-stripped copy, builds
wheel + sdist, and removes the staging tree.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLI = HERE.parent
SERVER = CLI / "src" / "provelog" / "server"
STAGE = HERE / "src" / "provelog" / "server"

sys.path.insert(0, str(CLI / "scripts"))
import publish_clean as P  # noqa: E402


def stage() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for src in sorted(SERVER.glob("*")):
        if not src.is_file() or src.name == "__pycache__":
            continue
        data = src.read_bytes()
        if src.suffix == ".py":
            text = P.clean_text("py", data.decode("utf-8"), str(src))
            (STAGE / src.name).write_text(text, encoding="utf-8")
        else:
            (STAGE / src.name).write_bytes(data)


def main() -> int:
    try:
        stage()
        dist = HERE / "dist"
        dist.mkdir(exist_ok=True)
        from setuptools import build_meta
        print(build_meta.build_wheel(str(dist)))
        print(build_meta.build_sdist(str(dist)))
        return 0
    finally:
        for path in (STAGE, HERE / "build"):
            if path.exists():
                shutil.rmtree(path)
        src_root = HERE / "src"
        if src_root.exists() and not any(src_root.rglob("*")):
            shutil.rmtree(src_root)


if __name__ == "__main__":
    raise SystemExit(main())
