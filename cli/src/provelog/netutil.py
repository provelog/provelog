"""HTTP clients that behave the same on loopback as on the open internet.

macOS system proxies answer requests to ``127.0.0.1`` with 502 (observed on this
project twice: the operator relay and the anchor push). A local endpoint -
the dev TSA, the operator relay - must be reached
directly, never through whatever proxy the operator happens to have configured,
so proxy discovery is switched off for loopback hosts only.

Trust: by default ``httpx`` uses certifi, which does NOT contain an enterprise
TLS-inspection root. That made every relay/TSA request fail on managed networks
unless the user manually set ``SSL_CERT_FILE``. We now prefer the OS trust store
(``truststore``), then ``SSL_CERT_FILE``, then certifi. No user configuration.
"""

from __future__ import annotations

import os
import ssl
from urllib.parse import urlparse

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def is_loopback(url: str | None) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host in _LOOPBACK_HOSTS


def ssl_context():
    """The best available TLS trust configuration, without user setup.

    Order: OS trust store (truststore) -> SSL_CERT_FILE -> certifi/default.
    Returns True when the caller should leave httpx's default (certifi).
    """
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    cafile = os.environ.get("SSL_CERT_FILE")
    if cafile:
        try:
            return ssl.create_default_context(cafile=cafile)
        except Exception:
            pass
    return True


def client_for(url: str | None, *, timeout: float):
    """An ``httpx.Client`` for this URL, with proxies disabled on loopback."""
    import httpx

    return httpx.Client(
        timeout=timeout,
        trust_env=not is_loopback(url),
        verify=ssl_context(),
    )
