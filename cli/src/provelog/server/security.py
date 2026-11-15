"""The stamping service's small in-process helpers.

What is left after the account layer went: a rate limiter (the per-stream
and per-IP TSA-spend gates) and the anchor id mint. There are no API keys
to hash and no roles to check - a push authenticates with the device
signature over the claim, verified and discarded in the endpoint.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque


def new_anchor_id() -> str:
    """The public handle a receipt is fetched by: unguessable, not derived
    from anything about the stream or the customer."""
    return f"anc_{uuid.uuid4().hex}"


class RateLimiter:
    """Fixed-window token bucket keyed by an arbitrary string (stream, IP)."""

    def __init__(self, limit: int, window_s: float):
        self.limit = limit
        self.window = window_s
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic() if now is None else now
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                dq = self._hits[key] = deque()
            while dq and dq[0] <= now - self.window:
                dq.popleft()
            if len(dq) >= self.limit:
                return False
            dq.append(now)
                                                                                         
                                                                                     
            if len(self._hits) > 4096:
                cutoff = now - self.window
                for k in [k for k, d in self._hits.items() if not d or d[-1] <= cutoff]:
                    if k != key:
                        del self._hits[k]
            return True
