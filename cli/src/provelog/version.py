"""Version/update metadata shared by the CLI and the relay.

PyPI is the release source when the package is published there.  The check is
deliberately one HTTPS request, no identifiers, no background polling, and no
automatic installation.
"""

from __future__ import annotations

import os
from typing import Any

                                                                               
PYPI_URL = os.environ.get(
    "PROVELOG_PYPI_URL", "https://pypi.org/pypi/provelog/json")
PROJECT_URL = "https://pypi.org/project/provelog/"
DOWNLOAD_URL = PROJECT_URL


def version_tuple(value: str) -> tuple[int, ...] | None:
    """A numeric release tuple, or None when the value is not a release."""
    try:
        parts = tuple(int(p) for p in str(value).strip().split("."))
    except (TypeError, ValueError):
        return None
    return parts if parts and all(p >= 0 for p in parts) else None


def is_older(current: str, minimum: str) -> bool:
    left = version_tuple(current)
    right = version_tuple(minimum)
    if left is None or right is None:
        return False
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) < right + (0,) * (width - len(right))


def check(*, timeout: float = 2.0) -> dict[str, Any] | None:
    """Return the latest PyPI release; None on any network/shape failure.

    A pre-release or otherwise non-numeric version is not offered as an update
    here: the update prompt should point at a stable release.
    """
    from .netutil import client_for

    try:
        with client_for(PYPI_URL, timeout=timeout) as client:
            resp = client.get(PYPI_URL)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    info = data.get("info")
    if not isinstance(info, dict):
        return None
    latest = str(info.get("version") or "").strip()
    if version_tuple(latest) is None:
        return None
    return {
        "latest": latest,
        "download_url": PROJECT_URL,
        "project_url": PROJECT_URL,
    }
