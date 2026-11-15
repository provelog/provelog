"""The last rung of the ladder: block the loudest addresses, briefly.

Order of defence, and why this one is last:

  1. per-address share (always available, soft: over the share is refused)
  2. proof-of-work (reserved; costs every claimant CPU, so it is used before this)
  3. this: refuse an address outright for a while

A blocked address is collateral damage for everyone behind it, which is why it
only happens when the queue is already critical, only for addresses that account
for a large share of the flood, only for a bounded number of them, and only for
a few minutes. A distributed flood (many addresses, each small) is not what this
is for - PoW is - and pretending otherwise would just block ordinary users.

Counts live in memory only: no address is written to disk or logged beyond the
operator's own console line.
"""

from __future__ import annotations

import time


class FloodGuard:
    """Per-address claim counts in a sliding window, with a short block list."""

    def __init__(self, *, window_s: float = 60.0, block_s: float = 600.0,
                 share: float = 0.25, min_claims: int = 500,
                 max_blocked: int = 10, max_ips: int = 4096,
                 consider_every_s: float = 5.0):
        self.window_s = window_s
        self.block_s = block_s
        self.share = share
        self.min_claims = min_claims
        self.max_blocked = max_blocked
        self.max_ips = max_ips
        self.consider_every_s = consider_every_s
        self._counts: dict[str, list[float]] = {}
        self._blocked: dict[str, float] = {}
        self._considered_at = float("-inf")

                                                                                  

    def record(self, address: str, now: float | None = None) -> None:
        stamp = time.monotonic() if now is None else now
        hits = self._counts.get(address)
        if hits is None:
            hits = self._counts[address] = []
            if len(self._counts) > self.max_ips:
                self._evict(stamp)
        hits.append(stamp)

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_s
        for address in [a for a, h in self._counts.items() if not h or h[-1] <= cutoff]:
            del self._counts[address]

    def count(self, address: str, now: float | None = None) -> int:
        stamp = time.monotonic() if now is None else now
        cutoff = stamp - self.window_s
        return sum(1 for t in self._counts.get(address, []) if t > cutoff)

                                                                                  

    def is_blocked(self, address: str, now: float | None = None) -> bool:
        stamp = time.monotonic() if now is None else now
        until = self._blocked.get(address)
        if until is None:
            return False
        if until <= stamp:
            del self._blocked[address]
            return False
        return True

    def blocked_count(self) -> int:
        return len(self._blocked)

    def consider(self, now: float | None = None) -> list[str]:
        """Block the addresses that dominate the current window, if any do.

        Returns the addresses newly blocked so the caller can log one line.
        """
        stamp = time.monotonic() if now is None else now
        if stamp - self._considered_at < self.consider_every_s:
            return []
        self._considered_at = stamp
        cutoff = stamp - self.window_s
        totals = {a: sum(1 for t in hits if t > cutoff)
                  for a, hits in self._counts.items()}
        totals = {a: n for a, n in totals.items() if n > 0}
        window_total = sum(totals.values())
        if window_total == 0:
            return []
        fresh = []
        ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
        for address, n in ranked:
            if len(self._blocked) >= self.max_blocked:
                break
            if n < self.min_claims or n < self.share * window_total:
                break
            if self.is_blocked(address, stamp):
                continue
            self._blocked[address] = stamp + self.block_s
            fresh.append(address)
        return fresh
