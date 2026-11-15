"""Load states for the anchor service: throttle only when the queue says so.

A quiet service looks identical for one user and for ten thousand users behind a
VPN. Throttling by identity (device key, address) was tried and removed: it cost
every honest user something and cost an attacker nothing. This guard is the
identity-blind alternative: the state is a function of how full the epoch queue
is, and the queue is the resource every user shares.

    NORMAL        queue < 20%   no challenge, no address limit
    ELEVATED      queue >= 20%  reserved: no action today (proof-of-work was
                                considered and rejected - see the decision record)
    UNDER_ATTACK  queue >= 60%, or 80% at any instant
                                proof-of-work + a per-address share of the queue

Leaving a state takes time, not one quiet sample: 60 s below the attack line
before returning to ELEVATED, 300 s below the elevated line before NORMAL. That
is the hysteresis that keeps a bursty-but-honest service from flapping between
"everything is fine" and "under attack" every other minute.
"""

from __future__ import annotations

import time

NORMAL = "normal"
ELEVATED = "elevated"
UNDER_ATTACK = "under_attack"


class PressureGuard:
    """Queue-pressure state machine with time-based hysteresis."""

    def __init__(self, capacity: int, *, elevated_at: float = 0.20,
                 attack_at: float = 0.60, panic_at: float = 0.80,
                 attack_hold_s: float = 60.0, elevated_hold_s: float = 300.0):
        self.capacity = max(1, int(capacity))
        self.elevated_at = elevated_at
        self.attack_at = attack_at
        self.panic_at = panic_at
        self.attack_hold_s = attack_hold_s
        self.elevated_hold_s = elevated_hold_s
        self._hot_at = float("-inf")
        self._very_hot_at = float("-inf")
        self._state = NORMAL

    @property
    def state(self) -> str:
        return self._state

    def ratio(self, pending: int) -> float:
        return max(0.0, float(pending)) / self.capacity

    def observe(self, pending: int, now: float | None = None) -> str:
        """Advance the state from the current queue depth and return it."""
        stamp = time.monotonic() if now is None else now
        ratio = self.ratio(pending)
        if ratio >= self.attack_at:
            self._very_hot_at = stamp
        if ratio >= self.elevated_at:
            self._hot_at = stamp
        if ratio >= self.panic_at:
            self._state = UNDER_ATTACK
        elif stamp - self._very_hot_at < self.attack_hold_s:
            self._state = UNDER_ATTACK
        elif stamp - self._hot_at < self.elevated_hold_s:
            self._state = ELEVATED
        else:
            self._state = NORMAL
        return self._state
