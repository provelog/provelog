"""Automatic anchoring: aggregate epochs, and the TTL sweep.

There is no record-driven tick anymore - records never arrive here. What this
module runs is:

  - AggregatingAnchorScheduler: the epoch thread. Every epoch, all queued
    stream claims become leaves of one Merkle tree and ONE TSA stamp covers
    the root; the per-stream receipts are dispatched into their
    deliver-then-delete rows.
  - RetentionScheduler: the external-anchor row TTL (7 days), which exists
    only to catch rows whose delivery-crash path left them behind.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from ..anchor import Anchor
from .risk import DELAYED, LANES, LIVE, RiskGuard

_log = logging.getLogger("provelog.tlog")

                                                                          
                                                                           
            
LIVE_RESERVE = 10_000


class LaneClosed(Exception):
    """A lower-priority lane is closed for this epoch; retry later."""


_LANE_RANK = {LIVE: 0, DELAYED: 1}


class AnchorScheduler:
    """The per-stream scheduler shell.

    Per-stream direct stamping happens synchronously in the POST /v1/anchors
    handler, one TSA round-trip per push. The base scheduler holds the anchor
    backend; start/stop are no-ops because there is no server-held record to
    anchor on a timer."""

    def __init__(self, store, anchor: Anchor):
        self.store = store
        self.anchor = anchor

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class RetentionScheduler:
    """Row-level TTL for the hosted anchor rows.

    Each external_anchors row dies the moment its receipt is delivered
    (deliver-then-delete, in the endpoint). This sweep is the backstop for
    the crash window between stamping and delivery: N days after ITS OWN
    creation, whole row, no tombstone - the receipt was handed to the user
    at stamp time and verification is 100% offline, so the hosted row is
    transit data, not evidence. 0 = keep forever."""

    def __init__(self, store, interval_s: float = 3600.0, external_anchor_days: int = 7):
        self.store = store
        self.interval = interval_s
        self.external_anchor_days = external_anchor_days
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sweep(self) -> int:
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return self._sweep_external_anchors(now)

    def _sweep_external_anchors(self, now: str) -> int:
        if self.external_anchor_days <= 0:
            return 0
        cutoff = self._cutoff(self.external_anchor_days, ref=now)
        n, oldest = self.store.delete_external_anchors_before(cutoff)
        if hasattr(self.store, "delete_tombstones_before"):
            self.store.delete_tombstones_before(cutoff)
        if n:
            _log.info("external anchor TTL: removed %s hosted row(s) created before %s "
                      "(oldest %s); delivered receipts verify offline, unaffected)",
                      n, cutoff, oldest or "-")
        return n

    def _cutoff(self, days: int, ref: str) -> str:
        t = datetime.strptime(ref, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        return (t - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def start(self) -> None:
        if self.interval <= 0 or self._thread is not None:
            return

        def loop():
            while not self._stop.wait(self.interval):
                try:
                    self.sweep()
                except Exception:  # pragma: no cover - defensive
                    _log.exception("retention sweep failed")

        self._thread = threading.Thread(target=loop, daemon=True, name="pr-retention")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None


                                                                                      
 
                                                                              
                                                                  
                                                                            
                                                                         
                                                                       
                                    
 
                                                                              
                                                                            
                                                                              
                                                    

                                                                            
                                                                           
AGG_WAL_MAX_AGE_S = 7 * 24 * 3600

                                                                          
                                                                        
                                                                            
                                                                        
                                                                 
AGG_BATCH_MAX_ENTRIES = 100_000
AGG_BATCH_MAX_WAL_BYTES = 64 * 1024 * 1024

                                                                             
                                                                               
                                                                               
                 
LANE_CAPS = {LIVE: AGG_BATCH_MAX_ENTRIES, DELAYED: 15_000}
LANE_CAPS_IDLE = {DELAYED: 25_000}
IDLE_AT = 0.10


class AggregateBatch:
    """The pending per-epoch batch of stream claims, with a write-ahead log.

    Anchor pushes feed claims in; the epoch consumes and clears them. The WAL
    survives a crash between queue and epoch: on restart the entries re-enter
    the next epoch. Pure disk failures are swallowed like every convenience
    layer here - the claim was answered 'queued', and losing it to a disk
    error costs the epoch's receipt, never any record."""

    _HEADER = {"t": "agg-batch", "v": 1}

    def __init__(self, wal_path: str | None = None):
        from pathlib import Path
        self._path = Path(wal_path) if wal_path else None
        self._lock = threading.Lock()
        self._entries: list[dict[str, Any]] = []
        self._next_epoch = 1
        self._lane_counts: dict[str, int] = {}
        self._load()

                                                                                     

    def _load(self) -> None:
        """Load the WAL. Two file shapes are accepted:

        - the current APPEND shape: header, appended entry lines, and
          {"t":"consumed","epoch":N} markers; entries after the last marker
          are pending;
        - the legacy FULL-REWRITE shape (written before the append change):
          header plus every then-pending entry, no consumed markers - every
          entry counts as pending, so a batch in flight across the upgrade
          is never lost.

        A torn final line (a crash mid-append) is skipped and the rest kept."""
        import json as _json
        import time as _time
        if self._path is None or not self._path.is_file():
            return
        try:
            lines = [ln for ln in self._path.read_text(encoding="utf-8").splitlines()
                     if ln.strip()]
        except OSError:
            return
        now = _time.time()
        consumed_at = -1                                             
        staged: list[dict[str, Any]] = []
        for i, ln in enumerate(lines):
            try:
                doc = _json.loads(ln)
            except ValueError:
                continue                                                     
            if not isinstance(doc, dict):
                continue
            if doc.get("t") == "agg-batch":
                self._next_epoch = max(self._next_epoch, int(doc.get("next_epoch", 1)))
                continue
            if doc.get("t") == "consumed":
                                                                             
                consumed_at = i
                staged = []
                self._next_epoch = max(self._next_epoch, int(doc.get("epoch", 1)) + 1)
                continue
            if doc.get("t") != "e":
                continue
            staged.append((i, doc))
        for i, doc in staged:
            if i < consumed_at:
                continue                                                         
                                                                          
                                                               
            if now - float(doc.get("queued_at", now)) > AGG_WAL_MAX_AGE_S:
                continue
            doc.pop("t", None)
            doc.pop("queued_at", None)
            self._entries.append(doc)
            lane = str(doc.get("lane") or LIVE)
            self._lane_counts[lane] = self._lane_counts.get(lane, 0) + 1

    def _append_wal(self, line: str) -> None:
        """Append ONE line (writing the header first on a fresh file). The
        per-push cost is O(1), not O(batch): the old full rewrite on every
        add() was O(n^2) over an epoch's pushes."""
        import json as _json
        import os as _os
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if not self._path.exists():
                fd = _os.open(self._path, _os.O_WRONLY | _os.O_CREAT, 0o600)
                try:
                    _os.write(fd, (_json.dumps(
                        {**self._HEADER, "next_epoch": self._next_epoch}) + "\n").encode("utf-8"))
                finally:
                    _os.close(fd)
            fd = _os.open(self._path, _os.O_WRONLY | _os.O_CREAT | _os.O_APPEND, 0o600)
            try:
                _os.write(fd, line.encode("utf-8"))
            finally:
                _os.close(fd)
        except OSError:
            pass                                                           

    def _rewrite_wal(self) -> None:
        """Header + pending entries + the fresh epoch counter: ONE full write
        per consumed epoch (and at construction of a fresh file). An epoch's
        worth of entries is bounded by the cap, so this stays O(epoch)."""
        import json as _json
        import time as _time
        if self._path is None:
            return
        import os as _os
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(_json.dumps({**self._HEADER, "next_epoch": self._next_epoch}) + "\n")
                for e in self._entries:
                    fh.write(_json.dumps({**e, "t": "e",
                                          "queued_at": _time.time()}) + "\n")
            _os.replace(tmp, self._path)
        except OSError:
            pass                                                           

                                                                                       

    def add(self, entry: dict[str, Any]) -> None:
        """Queue one claim, coalescing repeats of the same stream.

        The claim for a stream is a prefix commitment: a later one with greater
        coverage says strictly more than an earlier one, so keeping both would
        only make the epoch tree bigger for the same evidence. A flood of
        repeated claims for the same conversation therefore collapses to one
        leaf; the superseded anchor id answers 404 and the client retries (its
        newer claim covers the same records).
        """
        import json as _json
        import time as _time
        with self._lock:
            same = [e for e in self._entries if e.get("stream_id") == entry.get("stream_id")]
            if same:
                fresh = int(entry.get("covers_up_to") or 0)
                if all(int(e.get("covers_up_to") or 0) <= fresh for e in same):
                                                                                 
                                                                              
                                                             
                    lanes = [str(e.get("lane") or LIVE) for e in same]
                    lanes.append(str(entry.get("lane") or LIVE))
                    entry = {**entry, "lane": min(lanes, key=lambda n: _LANE_RANK.get(n, 0))}
                    removed = [e for e in self._entries
                               if e.get("stream_id") == entry.get("stream_id")]
                    for old in removed:
                        old_lane = str(old.get("lane") or LIVE)
                        self._lane_counts[old_lane] = max(
                            0, self._lane_counts.get(old_lane, 0) - 1)
                    self._entries = [e for e in self._entries
                                     if e.get("stream_id") != entry.get("stream_id")]
                else:
                    return                                                       
            self._entries.append(dict(entry))
            new_lane = str(entry.get("lane") or LIVE)
            self._lane_counts[new_lane] = self._lane_counts.get(new_lane, 0) + 1
            self._append_wal(_json.dumps({**entry, "t": "e",
                                          "queued_at": _time.time()}) + "\n")

    def lane_counts(self) -> dict[str, int]:
        with self._lock:
            return {lane: count for lane, count in self._lane_counts.items() if count}

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._entries]

    def clear(self, next_epoch: int) -> None:
        """Mark everything consumed and compact: the consumed marker lets a
        crash between marker and rewrite still load an empty batch, and the
        rewrite reclaims the appended lines so the file never grows unboundedly."""
        import json as _json
        with self._lock:
            self._entries = []
            self._lane_counts = {}
            self._next_epoch = max(self._next_epoch, next_epoch)
            self._append_wal(_json.dumps({"t": "consumed", "epoch": next_epoch - 1}) + "\n")
            self._rewrite_wal()

    def remove(self, anchor_ids: set[str], next_epoch: int) -> None:
        """Remove exactly the claims this epoch processed; keep everything that
        arrived while the TSA call or the dispatch loop was in flight.

        No consumed marker here: that marker swallows EVERYTHING appended
        before it, including the late arrivals this method exists to protect.
        The WAL is rewritten to the remaining entries instead; if the process
        crashes before the rewrite, the processed claims are re-stamped next
        epoch and the dispatch guards skip them idempotently.
        """
        with self._lock:
            kept = []
            for e in self._entries:
                if e.get("anchor_id") in anchor_ids:
                    lane = str(e.get("lane") or LIVE)
                    self._lane_counts[lane] = max(
                        0, self._lane_counts.get(lane, 0) - 1)
                else:
                    kept.append(e)
            self._entries = kept
            self._next_epoch = max(self._next_epoch, next_epoch)
            self._rewrite_wal()

    @property
    def next_epoch(self) -> int:
        return self._next_epoch

    def __len__(self) -> int:
        return len(self._entries)

    def wal_size(self) -> int:
        """Bytes of the WAL file (0 when in-memory). Feeds the cap below."""
        try:
            return self._path.stat().st_size if self._path is not None else 0
        except OSError:
            return 0


class BatchFull(Exception):
    """The epoch queue is at its ceiling. The caller answers 503, honestly.

    Running an early epoch instead would spend one TSA call per breach - the one
    shared resource every user depends on - and a sustained flood would drain the
    free TSA quota. Refusing is the cheaper, safer answer.
    """


class AggregatingAnchorScheduler(AnchorScheduler):
    """AnchorScheduler with per-push direct TSA DISABLED: every collector claim
    claim queues into the AggregateBatch, and one epoch thread stamps the
    whole batch with a single TSA request. The stamping math, the receipts,
    the WAL and the deliver-then-delete dispatch are exactly the aggregate
    design - only the (removed) server-held-record source is gone."""

    def __init__(self, store, anchor: Anchor,
                 aggregate_interval_s: float = 60.0, batch: AggregateBatch | None = None):
        super().__init__(store, anchor)
        self.aggregate_interval = aggregate_interval_s
        self.batch = batch if batch is not None else AggregateBatch()
        self.risk = RiskGuard(AGG_BATCH_MAX_ENTRIES)
        self._tsa_ok = True
        self._agg_stop = threading.Event()
        self._agg_thread: threading.Thread | None = None

                                                                                        

    def enqueue_push(self, *, anchor_id: str, stream_id: str,
                     covers_up_to: int, merkle_root: str,
                     lane: str = LIVE) -> None:
        if lane not in LANES:
            lane = LIVE
        self._refuse_if_capped(lane)
        self.batch.add({"source": "push", "stream_id": stream_id,
                        "anchor_id": anchor_id, "lane": lane,
                        "covers_up_to": covers_up_to, "merkle_root": merkle_root})

    def _lane_counts(self) -> dict[str, int]:
        counts = {lane: 0 for lane in LANES}
        try:
            raw = self.batch.lane_counts()
        except AttributeError:
            raw = {}
            try:
                for entry in self.batch.pending():
                    lane = str(entry.get("lane") or LIVE)
                    raw[lane] = raw.get(lane, 0) + 1
            except Exception:
                raw = {}
        except Exception:
            raw = {}
        for lane, count in raw.items():
            counts[str(lane)] = int(count)
        return counts

    def lane_snapshot(self) -> dict[str, Any]:
        counts = self._lane_counts()
        return {"lanes": counts, "risk": self.risk.snapshot(),
                "pending": len(self.batch),
                "capacity": AGG_BATCH_MAX_ENTRIES}

    def _lane_cap(self, lane: str, total: int) -> int:
        cap = LANE_CAPS.get(lane, AGG_BATCH_MAX_ENTRIES)
        if (lane in LANE_CAPS_IDLE
                and self.risk.state == "green"
                and self.risk.ratio(total) < IDLE_AT):
            cap = LANE_CAPS_IDLE[lane]
        return cap

    def _refuse_if_capped(self, lane: str = LIVE) -> None:
        """Refuse at the shared ceiling, and close lower lanes earlier.

        The batch is the shared TSA budget: one epoch = one stamp. Live is
        admitted until the hard ceiling; delayed catch-up may use the queue
        until ``LIVE_RESERVE`` is left.
        """
        total = len(self.batch)
        self.risk.observe(total, tsa_ok=self._tsa_ok)
        if total >= AGG_BATCH_MAX_ENTRIES \
                or self.batch.wal_size() >= AGG_BATCH_MAX_WAL_BYTES:
            _log.warning("aggregate batch at cap (%s entries, %s bytes WAL): "
                         "refusing new claims", total, self.batch.wal_size())
            raise BatchFull("stamping queue is full")
        if lane != LIVE and total >= AGG_BATCH_MAX_ENTRIES - LIVE_RESERVE:
            raise LaneClosed("live traffic is using the queue; retry later")
        counts = self._lane_counts()
        if counts.get(lane, 0) >= self._lane_cap(lane, total):
            raise LaneClosed(f"{lane} lane is full; retry later")
        if not self.risk.allows(lane):
            raise LaneClosed(f"{lane} lane is closed while the service is busy")

    def pending(self) -> int:
        """How many claims are waiting for the next epoch (cheap, best effort)."""
        try:
            return len(self.batch)
        except Exception:
            return 0

                                                                                         

    def epoch(self) -> dict[str, Any]:
        """Stamp the whole pending batch with ONE TSA request and dispatch the
        per-stream aggregate receipts. An empty batch is skipped entirely:
        nobody queued, nobody pays, no empty stamp."""
        from dataclasses import asdict as _asdict

        from .. import merkle
        from ..chain import _utc_now_iso

        entries = self.batch.pending()
        self.risk.observe(len(entries), tsa_ok=self._tsa_ok)
        if not entries:
            return {"epoch": 0, "streams": 0, "tsa_calls": 0, "kind": None}
                                                                         
                                                           
        entries.sort(key=lambda e: (_LANE_RANK.get(str(e.get("lane") or LIVE), 0),
                                    e["anchor_id"], e["stream_id"]))
        index = self.batch.next_epoch
        started_at = _utc_now_iso()
        size = len(entries)

        leaf_hashes: list[str] = []
        for e in entries:
            commit = merkle.compute_anchor_commit(
                e["stream_id"], index, e["covers_up_to"], e["merkle_root"],
                started_at, "aggregate", "")
            e["_commitment"] = commit.hex()
            leaf_hashes.append(merkle.compute_leaf_hash(commit, merkle.AGG_DOMAIN))
        levels = merkle.build_tree(leaf_hashes)
        agg_root = merkle.merkle_root_from_tree(levels)

                                                                           
                                                                      
                                                                          
        tsa_calls = 1
        agg_receipt = self.anchor.anchor_root(agg_root)
        self._tsa_ok = True
        kind = "aggregate" if agg_receipt.kind == "rfc3161" else "aggregate-local"
        receipt_core = _asdict(agg_receipt)

        from .storage import CoverageWentBackwards, DuplicateAnchor

        dispatched = 0
        failed: list[dict[str, Any]] = []
        for i, e in enumerate(entries):
            receipt = {
                "kind": kind,
                "stream_id": e["stream_id"],
                "covers_up_to": e["covers_up_to"],
                "commitment": e["_commitment"],
                "epoch": {"index": index, "started_at": started_at, "size": size},
                                                                              
                                                                             
                "leaf_index": i,
                "inclusion_proof": merkle.make_inclusion_proof_from_tree(i, levels),
                "agg_root": agg_root,
                "agg_receipt": receipt_core,
                "provider": receipt_core.get("tsa_url", ""),
                "primary_failed": bool(
                    getattr(self.anchor, "primary_url", None)
                    and receipt_core.get("tsa_url")
                    and receipt_core.get("tsa_url") != self.anchor.primary_url),
            }
            try:
                self.store.append_external_anchor(
                    anchor_id=e["anchor_id"], stream_id=e["stream_id"],
                    merkle_root=e["merkle_root"],
                    covers_up_to=e["covers_up_to"], receipt=receipt,
                    created_at=_utc_now_iso())
                dispatched += 1
            except (DuplicateAnchor, CoverageWentBackwards):
                                                                                
                                                                
                continue
            except Exception:
                                                                               
                                                                            
                _log.exception("could not persist anchor %s; re-queueing",
                               e["anchor_id"])
                failed.append(e)
                                                                            
                                                                           
                                                                             
                                                                          
                                                               
        processed = {e["anchor_id"] for e in entries
                     if all(e is not f for f in failed)}
        self.batch.remove(processed, index + 1)
        _log.info("aggregate epoch %s: %s stream(s) under one %s stamp "
                  "(agg_root=%s)", index, size, kind, agg_root[:16])
        return {"epoch": index, "streams": size, "tsa_calls": tsa_calls,
                "kind": kind, "agg_root": agg_root, "push": dispatched}

                                                                                           

    def start(self) -> None:
        if self.aggregate_interval <= 0 or self._agg_thread is not None:
            return

        def loop():
            while not self._agg_stop.wait(self.aggregate_interval):
                try:
                    self.epoch()
                except Exception:
                    self._tsa_ok = False
                    self.risk.observe(len(self.batch), tsa_ok=False)
                    _log.exception("aggregate epoch failed; the batch stays "
                                   "queued for the next epoch")

        self._agg_thread = threading.Thread(target=loop, daemon=True,
                                            name="pr-agg-anchor")
        self._agg_thread.start()

    def stop(self) -> None:
        self._agg_stop.set()
        self._agg_thread = None
