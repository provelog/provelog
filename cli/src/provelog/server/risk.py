"""Priority lanes and the operator's load state.

The epoch queue is shared by two classes of claims:

  live      a conversation that just recorded something; the user expects a
            stamp within the epoch.
  delayed   the content was captured live, but its stamp was missed (TSA down,
            429, power loss). It is automatic catch-up, never an import.

The guard is deliberately about the QUEUE, not about identities: a quiet
service looks the same for one user and for ten thousand behind a VPN.
Leaving a state needs time (hysteresis), so a burst does not flap the lanes.
"""

from __future__ import annotations

import time

GREEN = "green"
YELLOW = "yellow"
ORANGE = "orange"
RED = "red"

LIVE = "live"
DELAYED = "delayed"
LANES = (LIVE, DELAYED)

_RANK = {GREEN: 0, YELLOW: 1, ORANGE: 2, RED: 3}


class RiskGuard:
    """Queue-pressure state machine for lane admission.

    green    < 40%   all lanes
    yellow   40-65%  live + delayed
    orange   65-80%  live only (delayed closed)
    red      >= 80%  live only
    """

    def __init__(self, capacity: int, *, yellow_at: float = 0.40,
                 orange_at: float = 0.65, red_at: float = 0.80,
                 hold_s: float = 60.0):
        self.capacity = max(1, int(capacity))
        self.yellow_at = yellow_at
        self.orange_at = orange_at
        self.red_at = red_at
        self.hold_s = hold_s
        self._state = GREEN
        self._hot_since = 0.0

    @property
    def state(self) -> str:
        return self._state

    def ratio(self, pending: int) -> float:
        return max(0.0, float(pending)) / self.capacity

    def observe(self, pending: int, *, tsa_ok: bool = True,
                now: float | None = None) -> str:
        """Advance the state. Returns the new state.

        Upgrades happen immediately (protect live traffic); downgrades need
        ``hold_s`` below the lower band, so a one-off dip does not reopen the
        delayed lane.
        """
        stamp = time.monotonic() if now is None else float(now)
        ratio = self.ratio(pending)
        if not tsa_ok or ratio >= self.red_at:
            target = RED
        elif ratio >= self.orange_at:
            target = ORANGE
        elif ratio >= self.yellow_at:
            target = YELLOW
        else:
            target = GREEN

        if _RANK[target] >= _RANK[self._state]:
            self._state = target
            if target != GREEN:
                self._hot_since = stamp
            return self._state
                                                                      
        lower = {RED: self.orange_at, ORANGE: self.yellow_at,
                 YELLOW: self.yellow_at / 2}.get(self._state, 0.0)
        if ratio <= lower and stamp - self._hot_since >= self.hold_s:
            self._state = target
        return self._state

    def allows(self, lane: str) -> bool:
        if lane == LIVE:
            return True
        if lane == DELAYED:
            return self._state in (GREEN, YELLOW)
        return False

    def snapshot(self) -> dict:
        return {"state": self._state, "allows": {
            lane: self.allows(lane) for lane in LANES}}
