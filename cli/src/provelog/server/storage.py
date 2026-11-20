"""The stamping service's own state - which is almost nothing.

The cloud no longer receives or stores records; recording is local-first and
the customer holds every byte of evidence. What this store holds is almost nothing:

  - external_anchors: stamp claims in their deliver-then-delete window -
                      each row lives exactly until its receipt is handed
                      over, then dies (the 7-day TTL sweep catches crashes).

There is no table here a record - or an account, a member, an audit trail,
an agent registration - could land in, by construction: the service has no
account layer (a push authenticates with the device signature over the
claim itself, verified and discarded), and `Storage` is where that
minimalism is enforced. There is no migration from the audit era: this
service has no legacy tables to carry.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any

from ..dbutil import _Rows, _SerializedDB  # noqa: F401  (shared serialized-sqlite core)

_SCHEMA = """
-- The anchor-only trust service. A customer keeps every record themselves and
-- sends nothing but a Merkle root over their record hashes. This table holds
-- no personal data and no content by construction: there is no column that
-- could carry any. That is the whole point, and it is why this service can be
-- operated without becoming a processor.
CREATE TABLE IF NOT EXISTS external_anchors (
    anchor_id     TEXT PRIMARY KEY,
    stream_id     TEXT NOT NULL,     -- the customer's own label; opaque to us
    merkle_root   TEXT NOT NULL,     -- 64 hex chars, over THEIR record hashes
    covers_up_to  INTEGER NOT NULL,  -- how many records the root spans
    receipt       TEXT NOT NULL,     -- AnchorReceipt JSON
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS external_anchors_by_stream
    ON external_anchors(stream_id, covers_up_to);
-- Deliver-then-delete leaves no row, and the client cannot tell "not minted
-- yet" (404, retry) from "already delivered, gone" (410, stop). A tombstone
-- keeps only the id and when it died, for the same retention window.
CREATE TABLE IF NOT EXISTS anchor_tombstones (
    anchor_id  TEXT PRIMARY KEY,
    deleted_at TEXT NOT NULL
);
"""

def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def coverage_went_backwards_message(have: int, offered: int) -> str:
    return (f"this stream is already anchored to {have} records; an anchor covering only "
            f"{offered} would drop the tail. Anchor the full chain, or open a new stream if "
            f"you meant to start over.")


def two_histories_message(covers: int) -> str:
    return (f"this stream is already anchored at {covers} records with a different root. The "
            f"same prefix cannot have two histories.")


class DuplicateAnchor(Exception):
    """An anchor identical to one already recorded. Carries the existing anchor_id so the caller
    can answer the retry with the row that already exists."""

    def __init__(self, anchor_id: str):
        super().__init__(anchor_id)
        self.anchor_id = anchor_id


class CoverageWentBackwards(ValueError):
    """Raised when an anchor would cover less of a stream than that stream's newest anchor does.

    Rejecting this is what makes the anchor history evidence rather than a log. If coverage could
    shrink, a customer could anchor 1000 records, have something go wrong at record 400, and
    re-anchor the shorter chain as though the tail never existed, carrying our signature. The
    refusal is the service."""


class Storage:
    def __init__(self, path: str = ":memory:"):
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
                                                                                             
                                                        
        self._lock = threading.RLock()
                                                                         
        conn.execute("PRAGMA busy_timeout=5000")
        if path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        self._db = _SerializedDB(conn, self._lock)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def append_external_anchor(self, *, anchor_id: str, stream_id: str,
                               merkle_root: str, covers_up_to: int,
                               receipt: dict[str, Any], created_at: str) -> dict[str, Any]:
        with self._lock:
            prev = self._db.execute(
                "SELECT anchor_id, covers_up_to, merkle_root FROM external_anchors "
                "WHERE stream_id=? ORDER BY covers_up_to DESC LIMIT 1",
                (stream_id,),
            ).fetchone()
            if prev is not None and covers_up_to < prev["covers_up_to"]:
                raise CoverageWentBackwards(
                    coverage_went_backwards_message(prev["covers_up_to"], covers_up_to))
            if prev is not None and covers_up_to == prev["covers_up_to"]:
                if merkle_root != prev["merkle_root"]:
                    raise CoverageWentBackwards(two_histories_message(covers_up_to))
                                                                                                
                                                                                                
                raise DuplicateAnchor(prev["anchor_id"])
            self._db.execute(
                "INSERT INTO external_anchors(anchor_id, stream_id, merkle_root, "
                "covers_up_to, receipt, created_at) VALUES (?,?,?,?,?,?)",
                (anchor_id, stream_id, merkle_root, covers_up_to,
                 json.dumps(receipt, ensure_ascii=False), created_at),
            )
            self._db.commit()
        return {"anchor_id": anchor_id, "stream_id": stream_id, "merkle_root": merkle_root,
                "covers_up_to": covers_up_to, "receipt": receipt, "created_at": created_at}

    def delete_external_anchor(self, anchor_id: str) -> bool:
        """Delete ONE hosted anchor row by its primary key (the receipt was just
        delivered in the response body - the hosted copy is garbage). Returns
        whether a row was removed.

        The id itself is remembered as a tombstone so a client that kept
        polling (crash between delivery and journal clearing) gets a definitive
        410 instead of an endless 404.
        """
        with self._lock:
            cur = self._db.execute("DELETE FROM external_anchors WHERE anchor_id=?",
                                   (anchor_id,))
            if cur.rowcount > 0:
                self._db.execute(
                    "INSERT OR REPLACE INTO anchor_tombstones(anchor_id, deleted_at) "
                    "VALUES (?,?)", (anchor_id, _now()))
            self._db.commit()
        return cur.rowcount > 0

    def anchor_tombstone(self, anchor_id: str) -> str | None:
        """When this anchor id was deleted, if it ever existed and died here."""
        with self._lock:
            row = self._db.execute(
                "SELECT deleted_at FROM anchor_tombstones WHERE anchor_id=?",
                (anchor_id,)).fetchone()
        return None if row is None else str(row["deleted_at"])

    def delete_tombstones_before(self, cutoff: str) -> int:
        """Row-level TTL for tombstones; they are diagnostic, not evidence."""
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM anchor_tombstones WHERE deleted_at < ?", (cutoff,))
            self._db.commit()
        return cur.rowcount

    def delete_external_anchors_before(self, cutoff: str) -> tuple[int, str]:
        """Row-level TTL: remove hosted anchor rows created before `cutoff`.
        Returns (rows removed, oldest removed created_at). The receipt was
        delivered to the user at stamp time and verifies offline, so these
        hosted copies are transit data, not evidence.

        Expired ids also become tombstones: a late poll then gets 410 ("this
        existed and is gone") instead of 404 ("never heard of it").
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT anchor_id, created_at FROM external_anchors "
                "WHERE created_at < ? ORDER BY created_at ASC", (cutoff,)).fetchall()
            if not rows:
                return 0, ""
            self._db.execute("DELETE FROM external_anchors WHERE created_at < ?",
                             (cutoff,))
            now = _now()
            self._db.executemany(
                "INSERT OR REPLACE INTO anchor_tombstones(anchor_id, deleted_at) "
                "VALUES (?,?)", [(row["anchor_id"], now) for row in rows])
            self._db.commit()
        return len(rows), rows[0]["created_at"]

    def newest_external_anchor(self, stream_id: str) -> dict[str, Any] | None:
        """The furthest-reaching anchor for a stream, or None. Lets the endpoint settle a
        conflict before spending a TSA round-trip on a request it is going to refuse."""
        row = self._db.execute(
            "SELECT anchor_id, stream_id, merkle_root, covers_up_to, receipt, created_at "
            "FROM external_anchors WHERE stream_id=? "
            "ORDER BY covers_up_to DESC LIMIT 1", (stream_id,),
        ).fetchone()
        if row is None:
            return None
        return {"anchor_id": row["anchor_id"], "stream_id": row["stream_id"],
                "merkle_root": row["merkle_root"], "covers_up_to": row["covers_up_to"],
                "receipt": json.loads(row["receipt"]), "created_at": row["created_at"]}

    def get_external_anchor(self, anchor_id: str) -> dict[str, Any] | None:
        """Read one signed assertion by id - the public auditor lookup, where
        the reader is a stranger holding only a receipt."""
        row = self._db.execute(
            "SELECT anchor_id, stream_id, merkle_root, covers_up_to, receipt, created_at "
            "FROM external_anchors WHERE anchor_id=?", (anchor_id,),
        ).fetchone()
        if row is None:
            return None
        return {"anchor_id": row["anchor_id"], "stream_id": row["stream_id"],
                "merkle_root": row["merkle_root"], "covers_up_to": row["covers_up_to"],
                "receipt": json.loads(row["receipt"]), "created_at": row["created_at"]}
