"""External anchoring of the server receipt-chain head.

Anchoring bounds the tamper window: anything anchored cannot be rewritten without
the anchor failing to verify. We anchor a Merkle root over a batch of receipt hashes.

Two backends:
  - RFC3161Anchor: real trusted timestamp from a TSA. The server default is
    Sectigo primary with FreeTSA as the failure fallback. The TSA genTime is
    authoritative wall-clock; the verifier flags client ts_utc that trails it.
  - LocalAnchor: offline/dev anchor signed by a separate "anchor key". Carries NO
    third-party trust; used for tests and air-gapped runs. Clearly labelled in output.

Merkle construction follows RFC 6962 domain separation (0x00 leaf / 0x01 node) so the
inclusion proofs are interoperable with standard transparency tooling.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

_log = logging.getLogger("provelog.anchor")


def _leaf(h: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + h).digest()


def _node(l: bytes, r: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + l + r).digest()


def merkle_root(leaves_hex: list[str]) -> str:
    if not leaves_hex:
        return "0" * 64
    level = [_leaf(bytes.fromhex(h)) for h in leaves_hex]
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(_node(level[i], level[i + 1]))
            else:
                nxt.append(level[i])                               
        level = nxt
    return level[0].hex()


def _checked_root(root_hex: str) -> str:
    """A root arrives over the wire in the anchor-only service, so it is untrusted input. It must
    be exactly 32 hex-encoded bytes: anything else is a client bug, and timestamping it would mint
    a receipt that can never match any bundle."""
    root = (root_hex or "").strip().lower()
    if len(root) != 64:
        raise ValueError(f"a Merkle root is 32 bytes, so 64 hex characters, got {len(root)}")
    try:
        bytes.fromhex(root)
    except ValueError:
        raise ValueError("a Merkle root must be hex") from None
    return root


@dataclass
class AnchorReceipt:
    kind: str                                 
    merkle_root: str
    gen_time: str                                                       
    token_b64: str | None = None                                                
    signature: str | None = None                                              
    anchor_pubkey: str | None = None
    tsa_url: str | None = None


class Anchor(Protocol):
    def anchor(self, leaves_hex: list[str]) -> AnchorReceipt: ...
    def anchor_root(self, root_hex: str) -> AnchorReceipt: ...


class LocalAnchor:
    """Offline anchor signed by a dedicated key held outside the agent process."""

    def __init__(self, signing_key=None):
        from .keys import SigningKey                                                 
        self._key = signing_key or SigningKey.generate()

    def anchor(self, leaves_hex: list[str]) -> AnchorReceipt:
        return self.anchor_root(merkle_root(leaves_hex))

    def anchor_root(self, root_hex: str) -> AnchorReceipt:
        """Timestamp a root someone else computed. The anchor-only service never sees the
        leaves, so it cannot derive the root; it can only vouch for the one it was handed."""
        root = _checked_root(root_hex)
        gen_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        sig = self._key.sign((root + "|" + gen_time).encode("utf-8"))
        return AnchorReceipt(
            kind="local",
            merkle_root=root,
            gen_time=gen_time,
            signature=sig,
            anchor_pubkey=self._key.public_key_hex(),
        )


class RFC3161Anchor:
    """Trusted timestamp anchor. Defaults to Sectigo's public TSA over HTTPS
    (the HTTP endpoint remains valid); any RFC 3161
    TSA URL works. FreeTSA remains supported and in the failover list for
    operators who prefer it. Every round-trip is timed and its HTTP status
    counted in `stats` - the operator's health check shows which TSA is
    slow or rate-limiting."""

    def __init__(self, tsa_url: str = "https://timestamp.sectigo.com/rfc3161",
                 timeout: float = 10.0):
        self.tsa_url = tsa_url
        self.timeout = timeout
        self.stats = {"calls": 0, "ok": 0, "http_429": 0, "http_5xx": 0,
                      "http_other_error": 0, "total_latency_s": 0.0,
                      "last_latency_s": 0.0, "last_error": ""}

    def anchor(self, leaves_hex: list[str]) -> AnchorReceipt:
        return self.anchor_root(merkle_root(leaves_hex))

    def anchor_root(self, root_hex: str) -> AnchorReceipt:
        import base64
        import time as _time

        import httpx
        from rfc3161_client import TimestampRequestBuilder

        from .tsa_der import decode_timestamp_response

        root = _checked_root(root_hex)
        req = TimestampRequestBuilder().data(bytes.fromhex(root)).build()
        self.stats["calls"] += 1
        t0 = _time.monotonic()
        try:
            resp = httpx.post(
                self.tsa_url,
                content=req.as_bytes(),
                headers={"Content-Type": "application/timestamp-query"},
                timeout=self.timeout,
            )
            latency = _time.monotonic() - t0
            self.stats["total_latency_s"] += latency
            self.stats["last_latency_s"] = latency
            if resp.status_code == 429:
                self.stats["http_429"] += 1
            elif resp.status_code >= 500:
                self.stats["http_5xx"] += 1
            elif resp.status_code >= 400:
                self.stats["http_other_error"] += 1
            resp.raise_for_status()
            tst = decode_timestamp_response(resp.content)
            gen_time = tst.tst_info.gen_time.astimezone(UTC).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            self.stats["ok"] += 1
            self.stats["last_error"] = ""
            return AnchorReceipt(
                kind="rfc3161",
                merkle_root=root,
                gen_time=gen_time,
                token_b64=base64.b64encode(resp.content).decode("ascii"),
                tsa_url=self.tsa_url,
            )
        except Exception as e:
            if not self.stats["last_error"]:
                self.stats["last_error"] = f"{type(e).__name__}: {e}"[:300]
            raise


                                                                             
                                                                 
                           
SECTIGO_TSA_URL = "https://timestamp.sectigo.com/rfc3161"
FREETSA_TSA_URL = "https://freetsa.org/tsr"


class FailoverTSAAnchor:
    """Primary/fallback TSA chain, in a fixed order.

    It always tries the first TSA first; only a failure hands THIS anchoring
    call to the next. The default chain is Sectigo -> FreeTSA. Both are
    independent RFC 3161 TSAs and produce equally valid receipts; the order
    is preference/availability, not a trust level. FreeTSA permits low volume
    at 1 request/second/IP, so it is the fallback.
    If every member fails, the last error is raised and the caller keeps the
    batch pending; no local stamp is produced.
    """

    def __init__(self, tsa_urls: list[str] | None = None, timeout: float = 10.0,
                 _factory=None):
        urls = ([SECTIGO_TSA_URL, FREETSA_TSA_URL] if tsa_urls is None
                else list(tsa_urls))
        if not urls:
            raise ValueError("FailoverTSAAnchor needs at least one TSA URL")
        self._factory = _factory or (lambda u: RFC3161Anchor(u, timeout))
        self.members = [self._factory(u) for u in urls]

    @property
    def primary_url(self) -> str:
        return getattr(self.members[0], "tsa_url", "")

    def anchor(self, leaves_hex: list[str]) -> AnchorReceipt:
        return self.anchor_root(merkle_root(leaves_hex))

    def anchor_root(self, root_hex: str) -> AnchorReceipt:
        errors: list[str] = []
        for member in self.members:
            try:
                return member.anchor_root(root_hex)
            except Exception as e:                                              
                errors.append(f"{getattr(member, 'tsa_url', '?')}: {e}")
        raise RuntimeError(
            f"all {len(self.members)} TSAs failed to timestamp; "
            f"last errors: {'; '.join(errors[-2:])}")

    def stats(self) -> dict:
        return {"tsa": {m.tsa_url: dict(m.stats) for m in self.members}}


