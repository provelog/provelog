"""One serialized SQLite wrapper, shared by every embedded sink.

This wrapper was born on the server, where concurrent ingest threads on one
connection made `fetchone()` return None for rows that plainly existed. The
recording pipeline it guarded there moved INTO the client (local-first
recording), and it brought this requirement with it: the local sink's sqlite
file is shared by every hook process a coding agent spawns - two hosts, two
terminals, a replay - all appending to the same receipt chain. Statements run
under the lock and rows are materialised before it is released, so a caller
can never fetch from a cursor another thread has moved on.
"""

from __future__ import annotations

import sqlite3
import threading


class _Rows:
    """A cursor whose rows are already read, so nothing is fetched outside the lock."""

    __slots__ = ("_rows", "_i", "lastrowid", "rowcount")

    def __init__(self, rows, lastrowid=None, rowcount=-1):
        self._rows, self._i = rows, 0
        self.lastrowid, self.rowcount = lastrowid, rowcount

    def fetchone(self):
        if self._i >= len(self._rows):
            return None
        self._i += 1
        return self._rows[self._i - 1]

    def fetchall(self):
        rest, self._i = self._rows[self._i:], len(self._rows)
        return rest

    def __iter__(self):
        return iter(self.fetchall())


class _SerializedDB:
    """Serialises every statement on one sqlite connection, reads included.

    `sqlite3.Connection` is not safe for concurrent cursor use across threads even with
    `check_same_thread=False`: two interleaved execute/fetch pairs on the same connection make
    `fetchone()` return None for a row that plainly exists. Reads are therefore serialised with
    writes.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn, self._lock = conn, lock

    def execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
            return _Rows(rows, cur.lastrowid, cur.rowcount)

    def executemany(self, sql, seq):
        with self._lock:
            cur = self._conn.executemany(sql, seq)
            return _Rows([], cur.lastrowid, cur.rowcount)

    def executescript(self, script):
        with self._lock:
            self._conn.executescript(script)
            return _Rows([])

    def commit(self):
        with self._lock:
            self._conn.commit()

    def rollback(self):
        with self._lock:
            self._conn.rollback()

    def close(self):
        with self._lock:
            self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)
