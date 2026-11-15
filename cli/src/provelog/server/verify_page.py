"""Public verifier page, served from the repository's static asset.

The browser verifier must work with no server-side verification and, when
hosted on a static site, with no API at all.  The canonical page therefore
lives at ``web/verify.html`` and this module only loads it for the optional
FastAPI convenience route.  Builds copy ``web/`` into the wheel (setup.py),
so an installed package serves the same bytes as a static deployment.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path


def _load_page() -> str:
    try:
        resource = importlib.resources.files("provelog").joinpath(
            "web", "verify.html")
        if resource.is_file():
            return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        pass
    repo = Path(__file__).resolve().parents[3] / "web" / "verify.html"
    if repo.is_file():
        return repo.read_text(encoding="utf-8")
    raise RuntimeError("web/verify.html is missing from this installation")


VERIFY_HTML = _load_page()
