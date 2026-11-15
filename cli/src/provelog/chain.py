"""Client-side hash chain over agent activity records.

Each record links to the previous via ``prev_hash`` (the previous record's
``record_hash``), carries a monotonic ``seq``, and is signed with the device key.
A session opens with a genesis record (prev_hash = GENESIS). Along the way it may
carry any number of ``lifecycle.commitment`` records: each states "everything up to
seq N is final" by carrying the Merkle root of those record hashes. A commitment is
a checkpoint, never an ending - a conversation has no end we could observe, so the
chain stays appendable and a later commitment simply covers more.

This chain detects in-flight tampering and reordering *by the client*. A second,
locally-computed receipt chain (``recv_hash`` / ``server_record_hash``) is laid
down by the store beside it, so anchoring leaves and the verifier can detect
deletion or reordering even without a hosted server receiving the records.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import GENESIS_PREV_HASH
from .canonical import canonicalize, sha256_hex
from .keys import SigningKey, verify_signature

SCHEMA_VERSION = "1"

              
GENESIS = "lifecycle.session_start"
COMMIT = "lifecycle.commitment"
HEARTBEAT = "lifecycle.heartbeat"
MODEL_CALL = "model_call"
TOOL_CALL = "tool_call"
MCP_CALL = "mcp_call"
DECISION = "decision"
DATA_ACCESS = "data_access"
HUMAN_OVERSIGHT = "human_oversight"
POLICY_DECISION = "policy.decision"


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def record_content(rec: dict[str, Any]) -> dict[str, Any]:
    """The signed/hashed subset: everything except the derived hash and signature."""
    return {k: v for k, v in rec.items() if k not in ("record_hash", "record_sig")}


def compute_record_hash(rec: dict[str, Any]) -> str:
    return sha256_hex(canonicalize(record_content(rec)))


@dataclass
class Chain:
    stream_id: str
    key: SigningKey
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _seq: int = 0
    _last_hash: str = GENESIS_PREV_HASH
    _hashes: list[str] = field(default_factory=list)
    _open: bool = False
    _clock: Callable[[], str] = _utc_now_iso
    _idgen: Callable[[], str] = lambda: str(uuid.uuid4())

    def _emit(self, action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        content = {
            "v": SCHEMA_VERSION,
            "stream_id": self.stream_id,
            "session_id": self.session_id,
            "record_id": self._idgen(),
            "seq": self._seq,
            "prev_hash": self._last_hash,
            "ts_utc": self._clock(),
            "pubkey": self.key.public_key_hex(),
            "action_type": action_type,
            "payload": payload,
        }
        canon = canonicalize(content)
        rec = dict(content)
        rec["record_hash"] = sha256_hex(canon)
        rec["record_sig"] = self.key.sign(canon)
        self._seq += 1
        self._last_hash = rec["record_hash"]
        self._hashes.append(rec["record_hash"])
        return rec

    @classmethod
    def resume(cls, stream_id: str, key: SigningKey, session_id: str,
               seq: int, prev_hash: str, hashes: list[str],
               clock: Callable[[], str] | None = None) -> Chain:
        """Reopen an existing chain at its last record.

        Used by incremental ingestion: the session stays open between polls, and a
        later commitment() must still cover every record ever written to it.
        """
        chain = cls(stream_id=stream_id, key=key, session_id=session_id)
        chain._seq = int(seq)
        chain._last_hash = prev_hash
        chain._hashes = list(hashes)
        chain._open = True
        if clock is not None:
            chain._clock = clock
        return chain

    def start(self, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._open or self._seq != 0:
            raise RuntimeError("session already started")
        self._open = True
        return self._emit(GENESIS, {"meta": meta or {}})

    def append(self, action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._open:
            raise RuntimeError("session not started")
        if action_type in (GENESIS, COMMIT):
            raise ValueError(f"{action_type} is emitted by start()/commit(), not append()")
        return self._emit(action_type, payload)

    def heartbeat(self) -> dict[str, Any]:
        if not self._open:
            raise RuntimeError("session not started")
        return self._emit(HEARTBEAT, {})

    def commit(self, reason: str = "auto") -> dict[str, Any]:
        """Checkpoint the chain: "everything up to ``covers_up_to`` is final".

        The root is the Merkle root over the record hashes emitted so far, which is
        exactly what the collector sends to the relay, so a receipt for this prefix also
        names this record. Committing does not end anything: the caller may keep
        appending in the same chain, and the next commitment covers the whole prefix.
        """
        if not self._open:
            raise RuntimeError("session not started")
        from .anchor import merkle_root                                                
        covers = self._seq
        root = merkle_root(list(self._hashes))
        rec = self._emit(
            COMMIT,
            {"covers_up_to": covers, "root": root, "reason": reason},
        )
                                                                               
                                                                     
        return rec


                                                                  


@dataclass
class ChainIssue:
    seq: int | None
    record_id: str | None
    code: str
    detail: str


def verify_client_chain(records: list[dict[str, Any]]) -> list[ChainIssue]:
    """Verify a full ordered list of client records. Returns a list of issues (empty == valid)."""
    issues: list[ChainIssue] = []
    if not records:
        return [ChainIssue(None, None, "empty", "no records")]

    expected_prev = GENESIS_PREV_HASH
    expected_seq = 0
    pubkey0 = records[0].get("pubkey")

    for rec in records:
        seq = rec.get("seq")
        rid = rec.get("record_id")

                        
        recomputed = compute_record_hash(rec)
        if recomputed != rec.get("record_hash"):
            issues.append(ChainIssue(seq, rid, "hash_mismatch",
                                     "record_hash does not match content (record altered)"))
                   
        if not verify_signature(rec.get("pubkey", ""), canonicalize(record_content(rec)),
                                rec.get("record_sig", "")):
            issues.append(ChainIssue(seq, rid, "bad_signature", "signature invalid"))
                                                                        
        if rec.get("pubkey") != pubkey0:
            issues.append(ChainIssue(seq, rid, "key_changed", "device pubkey changed mid-stream"))
                                             
        if seq != expected_seq:
            issues.append(ChainIssue(seq, rid, "seq_gap",
                                     f"expected seq {expected_seq}, got {seq} (missing/reordered records)"))
                         
        if rec.get("prev_hash") != expected_prev:
            issues.append(ChainIssue(seq, rid, "broken_link",
                                     "prev_hash does not match previous record_hash (deletion/insertion)"))

        expected_prev = rec.get("record_hash")
        expected_seq = (seq if isinstance(seq, int) else expected_seq) + 1

                      
    if records[0].get("action_type") != GENESIS:
        issues.append(ChainIssue(0, records[0].get("record_id"), "no_genesis",
                                 "first record is not a session_start"))
                                                                                   
    commits = [r for r in records if r.get("action_type") == COMMIT]
    if commits:
        last = commits[-1]
        last_seq = last.get("seq")
        if isinstance(last_seq, int):
            covered = [r.get("record_hash") for r in records if isinstance(r.get("seq"), int)
                       and r.get("seq") < last_seq]
            from .anchor import merkle_root
            expect = merkle_root(covered)
            if last.get("payload", {}).get("root") != expect:
                issues.append(ChainIssue(last_seq, last.get("record_id"), "bad_commitment",
                                         "commitment root does not cover the record set (truncation)"))
    return issues
