"""HTTP clients that behave the same on loopback as on the open internet.

macOS system proxies answer requests to ``127.0.0.1`` with 502 (observed on this
project twice: the operator relay and the anchor push). A local endpoint -
the dev TSA, the operator relay - must be reached
directly, never through whatever proxy the operator happens to have configured,
so proxy discovery is switched off for loopback hosts only.
"""

from __future__ import annotations

from urllib.parse import urlparse

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def is_loopback(url: str | None) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host in _LOOPBACK_HOSTS


def client_for(url: str | None, *, timeout: float):
    """An ``httpx.Client`` for this URL, with proxies disabled on loopback."""
    import httpx

    return httpx.Client(timeout=timeout, trust_env=not is_loopback(url))
