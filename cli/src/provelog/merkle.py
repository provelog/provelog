"""RFC 6962 Merkle helpers shared by aggregate anchoring and the verifier.

This is the only part of the old transparency-log module the product still
needs: anchor commitments, leaf hashes, roots and inclusion proofs. The
checkpoint/witness/consistency machinery was an audit-era surface and is gone.
"""

from __future__ import annotations

import base64
import hashlib

from .anchor import _node
from .canonical import canonicalize

                                                 
AGG_DOMAIN = b"provelog.ai/v1/agg-leaf:"


def compute_anchor_commit(stream_id: str, anchor_seq: int, covers_up_to: int,
                          merkle_root: str, gen_time: str, kind: str,
                          token_b64_or_sig: str) -> bytes:
    """Commit to the full anchor receipt, not just its Merkle root."""
    return hashlib.sha256(canonicalize({
        "stream_id": stream_id,
        "anchor_seq": anchor_seq,
        "covers_up_to": covers_up_to,
        "merkle_root": merkle_root,
        "gen_time": gen_time,
        "kind": kind,
        "token_b64_or_sig": token_b64_or_sig,
    })).digest()


def compute_leaf_hash(anchor_commit: bytes, domain: bytes = AGG_DOMAIN) -> str:
    """RFC 6962 leaf hash with a domain label."""
    return hashlib.sha256(b"\x00" + domain + anchor_commit).hexdigest()


def _largest_pow2_below(n: int) -> int:
    k = 1
    while k << 1 < n:
        k <<= 1
    return k


def _mth(leaves: list[bytes]) -> bytes:
    n = len(leaves)
    if n == 0:
        return hashlib.sha256(b"").digest()
    if n == 1:
        return leaves[0]
    k = _largest_pow2_below(n)
    return _node(_mth(leaves[:k]), _mth(leaves[k:]))


def merkle_root_from_leaf_hashes(leaf_hashes_hex: list[str]) -> str:
    if not leaf_hashes_hex:
        return hashlib.sha256(b"").hexdigest()
    return _mth([bytes.fromhex(h) for h in leaf_hashes_hex]).hex()


def _path(m: int, leaves: list[bytes]) -> list[bytes]:
    n = len(leaves)
    if n <= 1:
        return []
    k = _largest_pow2_below(n)
    if m < k:
        return _path(m, leaves[:k]) + [_mth(leaves[k:])]
    return _path(m - k, leaves[k:]) + [_mth(leaves[:k])]


def make_inclusion_proof(leaf_index: int, leaf_hashes_hex: list[str]) -> list[str]:
    proof = _path(leaf_index, [bytes.fromhex(h) for h in leaf_hashes_hex])
    return [base64.b64encode(p).decode("ascii") for p in proof]


def build_tree(leaf_hashes_hex: list[str]) -> list[list[bytes]]:
    """The full Merkle levels, leaf level first, each node 32 raw bytes.

    Building every level once and reading proofs off the levels is O(n) total;
    calling ``make_inclusion_proof`` once per leaf is O(n^2) because it rebuilds
    each sibling subtree from scratch.
    """
    level = [bytes.fromhex(h) for h in leaf_hashes_hex]
    if not level:
        return []
    levels = [level]
    while len(level) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(_node(level[i], level[i + 1]))
            else:
                nxt.append(level[i])                                        
        levels.append(nxt)
        level = nxt
    return levels


def merkle_root_from_tree(levels: list[list[bytes]]) -> str:
    if not levels:
        return hashlib.sha256(b"").hexdigest()
    return levels[-1][0].hex()


def make_inclusion_proof_from_tree(leaf_index: int,
                                   levels: list[list[bytes]]) -> list[str]:
    """An inclusion proof from a pre-built tree; O(log n) per leaf."""
    proof: list[bytes] = []
    idx = leaf_index
    for level in levels[:-1]:
        sibling = idx ^ 1
        if sibling < len(level):
            proof.append(level[sibling])
        idx //= 2
    return [base64.b64encode(p).decode("ascii") for p in proof]
