"""Standalone verifier. Trusts neither the agent nor the Provelog server.

Given an exported bundle it independently recomputes every hash, signature, chain link,
Merkle anchor, and timestamp relationship, and reports exactly what it can and cannot
confirm. It depends only on the public Ed25519 key embedded in the records and on public
anchor receipts, so an auditor can run it offline.

What a clean result proves:
  - the recorded client chain is internally consistent and signed by one device key
  - the local receipt chain is consistent, with no deletion, insertion or reordering
  - records covered by an anchor existed no later than the anchor's trusted time
  - attachments declared in the bundle hash to the bytes the records reference
What it can NEVER prove: completeness (that nothing was withheld before being recorded).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import GENESIS_PREV_HASH, __version__
from ..anchor import merkle_root
from ..canonical import CanonicalError, canonicalize, sha256_hex
from ..chain import (
    COMMIT,
    HEARTBEAT,
    verify_client_chain,
)
from ..keys import verify_signature
from ..version import DOWNLOAD_URL

                                                    
_DIGEST_OIDS = {
    "1.3.14.3.2.26": "sha1",
    "2.16.840.1.101.3.4.2.1": "sha256",
    "2.16.840.1.101.3.4.2.2": "sha384",
    "2.16.840.1.101.3.4.2.3": "sha512",
    "sha1": "sha1", "sha256": "sha256", "sha384": "sha384", "sha512": "sha512",
}

                                                                                      
                                                             
ANCHOR_SKEW_TOLERANCE_S = 5.0


def _parse_ts(s: str):
    from datetime import datetime
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None


def _seconds_after(a: str, b: str) -> float:
    """How many seconds a is after b. Negative/0 if a <= b. inf if unparseable."""
    ta, tb = _parse_ts(a), _parse_ts(b)
    if ta is None or tb is None:
        return float("inf")
    return (ta - tb).total_seconds()


                                                                                         
                                                                                                
                                                                                            
FORMAT_ERROR_CODES = frozenset({
    "not_a_bundle",                                                                     
    "bad_format",                                                             
    "malformed_bundle",                                                                  
})

                                                                                
                                                                            
                                                                               
                                                             
BACKEND_ERROR_CODES = frozenset({
    "verification_backend_unavailable",
    "rfc3161_support_missing",
})


@dataclass
class Axes:
    """Four independent answers, so one headline cannot hide a missing axis."""
    integrity: str = "unknown"                            
    time: str = "absent"                                             
    attribution: str = "absent"                          
    coverage: str = "none"                                    


@dataclass
class Finding:
    severity: str                            
    code: str
    detail: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    axes: Axes = field(default_factory=Axes)

    def add(self, severity: str, code: str, detail: str) -> None:
        self.findings.append(Finding(severity, code, detail))

    @property
    def ok(self) -> bool:
        return not any(f.severity == "fail" for f in self.findings)

    @property
    def result(self) -> str:
        """Three-way headline verdict, finer than `ok`:
        - "verified": no failures.
        - "tampered": at least one failure indicates the recorded data itself does not verify.
        """
        fails = [f.code for f in self.findings if f.severity == "fail"]
        if not fails:
            return "verified"
        if any(code in FORMAT_ERROR_CODES for code in fails):
            return "malformed"
                                                                                               
                                                                                             
                                                                                
        if fails == ["empty"]:
            return "empty"
        if all(code in BACKEND_ERROR_CODES for code in fails):
                                                                                   
            return "cannot_verify"
        return "tampered"

    def to_dict(self, strict: bool = False) -> dict[str, Any]:
        backend = [f.detail for f in self.findings
                   if f.code in BACKEND_ERROR_CODES]
        out = {
            "ok": self.ok,
            "result": self.result,
            "verdict": _result_word(self, strict),
            "fail": sum(f.severity == "fail" for f in self.findings),
            "warn": sum(f.severity == "warn" for f in self.findings),
            "findings": [f.__dict__ for f in self.findings],
            "axes": asdict(self.axes),
        }
        if backend:
                                                                            
                                                                         
            out["backend_error"] = " ".join(backend)
        return out


def _by_arrival_sid(server_records: list[dict[str, Any]]) -> dict[Any, list[Any]]:
    """Client seq values per session, in arrival order. Sessions may interleave (two
    writers on one stream); emission order is only meaningful within a session."""
    out: dict[Any, list[Any]] = {}
    for sr in server_records:
        rec = sr.get("record", {})
        out.setdefault(rec.get("session_id"), []).append(rec.get("seq"))
    return out


def _version_tuple(value: str) -> tuple[int, ...] | None:
    """Numeric dotted version, or None for an unparseable requirement."""
    try:
        parts = [int(part) for part in value.strip().split(".")]
    except (TypeError, ValueError):
        return None
    return tuple(parts) if parts else None


def upgrade_required(bundle: dict[str, Any]) -> str | None:
    """The bundle's verifier_min when this verifier is older than required.

    Missing verifier_min is format 1 and always accepted. A future requirement
    must produce an explicit "upgrade" answer, never VERIFIED and never
    TAMPERING: the file may be perfectly valid; this build just cannot judge it.
    """
    if not isinstance(bundle, dict):
        return None
    raw = str(bundle.get("verifier_min") or "").strip()
    required = _version_tuple(raw)
    current = _version_tuple(__version__)
    if required is None or current is None:
        return None
    width = max(len(required), len(current))
    required = required + (0,) * (width - len(required))
    current = current + (0,) * (width - len(current))
    return raw if required > current else None


def _axes_for(bundle: dict[str, Any], findings: list[Finding]) -> Axes:
    """Derive the four axes from findings and the bundle shape.

    This adds no new verification: it only classifies what the existing checks
    already reported. Defaults are deliberately conservative.
    """
    codes = {f.code for f in findings}
    fails = [f.code for f in findings if f.severity == "fail"]
    source = bundle if isinstance(bundle, dict) else {}
    records = source.get("records")
    records = records if isinstance(records, list) else []
    anchors = source.get("anchors")
    anchors = anchors if isinstance(anchors, list) else []
    n = len(records)

                                                                       
                                                                        
                                                                             
                                                                        
    backend_only = bool(fails) and all(code in BACKEND_ERROR_CODES for code in fails)
    if n == 0:
        integrity = "unknown"
    elif fails and not backend_only:
        integrity = "fail"
    elif backend_only:
        integrity = "unknown"
    else:
        integrity = "pass"

    bad_time = {
        "anchor_sig_invalid", "anchor_decode_error", "anchor_time_mismatch",
        "anchor_imprint_mismatch", "anchor_bad_alg", "anchor_verify_error",
        "anchor_unknown_kind", "anchor_malformed",
    }
    has_rfc3161 = any(
        isinstance(a, dict) and _anchor_kind(a) == "rfc3161"
        for a in anchors)
    if codes & bad_time:
        time = "fail"
    elif codes & BACKEND_ERROR_CODES:
                                                                        
        time = "unknown"
    elif codes & {"no_anchor", "local_anchor_only", "anchor_local_only"}:
        time = "absent"
    elif "anchor_unverified_tsa" in codes:
        time = "untrusted"
    elif "anchor_rfc3161" in codes:
                                                                            
                                                                        
                                                                          
                                                                            
        time = "pass"
    elif has_rfc3161:
        time = "untrusted"
    else:
        time = "absent"
    if integrity == "fail" and time == "pass":
                                                                            
                                                 
        time = "untrusted"

    signatures = [r for r in records if isinstance(r, dict)
                  and isinstance(r.get("record"), dict)
                  and (r.get("record") or {}).get("pubkey")]
    if any("signature" in code for code in fails):
        attribution = "fail"
    elif n == 0 or not signatures:
        attribution = "absent"
    else:
        attribution = "pass"

    covers = [_as_int(a.get("covers_up_to")) for a in anchors if isinstance(a, dict)]
    covered = max(covers, default=0)
    after_anchor = bool(codes & {"recv_after_anchor", "ts_after_anchor"})
    if backend_only:
                                                                               
        coverage = "unknown"
    elif n and covered >= n and not after_anchor:
        coverage = "complete"
    elif covered > 0 or after_anchor:
        coverage = "partial"
    else:
        coverage = "none"
    return Axes(integrity, time, attribution, coverage)


def _axes_text(rep: Report, bundle: dict[str, Any]) -> str:
    """The four lines and conclusion, shared by the CLI and browser."""
    a = rep.axes
    source = bundle if isinstance(bundle, dict) else {}
    n = len(source.get("records") or [])
    covered = max((int(x.get("covers_up_to") or 0)
                   for x in (source.get("anchors") or [])
                   if isinstance(x, dict)), default=0)
    backend = any(f.code in BACKEND_ERROR_CODES for f in rep.findings)
    if a.integrity == "pass":
        integrity = (f"{n} records, hash chain and signatures verified"
                     if a.attribution == "pass" else
                     f"{n} records, hash chain verified")
    elif a.integrity == "fail":
        integrity = "integrity failure; do not rely on this file"
    elif backend:
        integrity = "verification backend unavailable; integrity was not checked"
    else:
        integrity = "not enough data to judge integrity"
    time = {
        "pass": "RFC 3161 timestamp verified",
        "absent": "no timestamp in this bundle",
        "untrusted": "timestamp present, TSA not in the recognized list (Sectigo, FreeTSA)",
        "fail": "timestamp invalid",
        "unknown": "timestamp not checked (verification backend unavailable)",
    }.get(a.time, "no timestamp in this bundle")
    attribution = {
        "pass": "device signature verified",
        "absent": "no device signature",
        "fail": "device signature invalid",
    }.get(a.attribution, "no device signature")
    if a.integrity == "fail":
                                                                           
                                                                        
        coverage_value = "n/a"
        coverage = "not applicable (integrity failed)"
    elif a.integrity == "unknown" and backend:
        coverage_value = "n/a"
        coverage = "not applicable (verification backend unavailable)"
    else:
        coverage_value = a.coverage
        coverage = {
            "complete": f"{covered} / {n} records covered by a timestamp",
            "partial": f"{covered} / {n} records covered by a timestamp",
            "none": f"0 / {n} records covered by a timestamp",
            "unknown": "not applicable (verification backend unavailable)",
        }.get(a.coverage, f"0 / {n} records covered by a timestamp")
        if covered > 0 and a.time != "pass":
            coverage += {
                "untrusted": " (unrecognized TSA)",
                "absent": " (time not proven)",
                "fail": " (timestamp invalid)",
            }.get(a.time, " (time not proven)")
    lines = [
        f"{'Integrity':<13}{a.integrity.upper():<10}{integrity}",
        f"{'Time':<13}{a.time.upper():<10}{time}",
        f"{'Attribution':<13}{a.attribution.upper():<10}{attribution}",
        f"{'Coverage':<13}{coverage_value.upper():<10}{coverage}",
    ]
    if a.integrity == "fail":
        conclusion = "Do not rely on this record."
    elif backend:
        conclusion = ("This verifier could not run here; that is not a verdict on "
                      "the evidence.")
    elif a.integrity != "pass":
        conclusion = "No integrity verdict was reached."
    elif a.time != "pass":
        conclusion = ("Evidence pack invalid: the timestamp authority is not in the "
                      "recognized list (Sectigo, FreeTSA)"
                      if a.time == "untrusted" else
                      "Evidence pack invalid: no recognizable timestamp was detected.")
    elif a.time == "pass" and a.coverage == "complete":
        conclusion = ("Record intact; its time is independently proven."
                      if a.attribution == "pass" else
                      "Record intact; its time is independently proven, but "
                      "attribution is not established.")
    else:
        conclusion = "Record intact; its time is not fully proven."
    lines.append(f"Conclusion: {conclusion}")
    return "\n".join(lines)


def _result_word(rep: Report, strict: bool = False) -> str:
    """The word after ``RESULT:`` - the one verdict text, JSON and scripts share.

    ``ok``/``result`` in JSON describe integrity only; this is the headline.
    With ``strict`` a missing device signature is not acceptable, so the word
    agrees with the non-zero exit code.
    """
    if rep.result == "malformed":
        return "NOT A PROVELOG BUNDLE"
    if rep.result == "empty":
        return "NO RECORDS"
    if rep.result == "cannot_verify":
        return "CANNOT VERIFY"
    a = rep.axes
    if a.integrity == "fail":
        return "TAMPERING DETECTED"
    attributed = a.attribution == "pass" or (a.attribution == "absent" and not strict)
    if a.integrity == "pass" and attributed and a.time == "pass":
        if a.coverage == "complete":
            return "VERIFIED"
        if a.coverage == "partial":
            return "VERIFIED (partial timestamp coverage)"
    return "INVALID"


def _human_verify(rep: Report, bundle: dict[str, Any], display_name: str,
                  strict: bool = False) -> None:
    """The concise user-facing text report. Warnings go to stderr; axes stay stdout."""
    import sys as _sys

    a = rep.axes
    source = bundle if isinstance(bundle, dict) else {}
    records = source.get("records") or []
    n = len(records)
    covered = max((int(x.get("covers_up_to") or 0)
                   for x in (source.get("anchors") or [])
                   if isinstance(x, dict)), default=0)
    has_local = bool({f.code for f in rep.findings} &
                     {"anchor_local_only", "local_anchor_only"})

    if rep.result == "malformed":
        print("RESULT: NOT A PROVELOG BUNDLE")
        print()
        print("Error: The file does not contain a recognized ProveLog bundle.",
              file=_sys.stderr)
        print("Use an evidence pack created by provelog export.", file=_sys.stderr)
        return
    if rep.result == "empty":
        print("RESULT: NO RECORDS")
        print()
        print("This evidence pack contains no records to verify.")
        return

    result = _result_word(rep, strict)

    backend = any(f.code in BACKEND_ERROR_CODES for f in rep.findings)
    integrity = (
        (f"{n} records; hashes and signatures verified"
         if a.attribution == "pass" else
         f"{n} records; hash chain verified")
        if a.integrity == "pass" else
        "Record integrity verification failed"
        if a.integrity == "fail" else
        "Verification backend unavailable; integrity was not checked"
        if backend else
        "No integrity verdict was reached"
    )
    time = {
        "pass": "Trusted RFC 3161 timestamp verified",
        "absent": "No trusted third-party timestamp",
        "untrusted": "Timestamp authority is not in ProveLog's recognized TSA list (Sectigo, FreeTSA)",
        "fail": "Timestamp verification failed",
        "unknown": "Timestamp was not checked (verification backend unavailable)",
    }.get(a.time, "No trusted third-party timestamp")
    attribution = {
        "pass": "Device signatures verified",
        "fail": "Device signatures failed",
        "absent": "No device signatures",
    }.get(a.attribution, "No device signatures")
    if a.integrity == "fail":
        cov_value, coverage = "N/A", "not applicable (integrity failed)"
    elif a.integrity == "unknown" and backend:
        cov_value = "N/A"
        coverage = "not applicable (verification backend unavailable)"
    else:
        cov_value = a.coverage.upper()
        if a.coverage == "complete" and a.time == "pass":
            coverage = f"{n} / {n} records covered by a trusted timestamp"
        elif a.coverage == "complete" and has_local:
            coverage = f"{n} / {n} records covered by local anchors only"
        elif a.coverage == "complete":
            coverage = f"{n} / {n} records covered by a timestamp from an unrecognized TSA"
        elif a.coverage == "partial":
            coverage = f"{covered} / {n} records covered by a trusted timestamp"
        else:
            coverage = f"0 / {n} records covered"

    print(f"RESULT: {result}")
    print()
    print(f"{'Integrity':<13}{a.integrity.upper():<10}{integrity}")
    print(f"{'Time':<13}{a.time.upper():<10}{time}")
    print(f"{'Attribution':<13}{a.attribution.upper():<10}{attribution}")
    print(f"{'Coverage':<13}{cov_value:<10}{coverage}")

    if result == "CANNOT VERIFY":
        print()
        detail = next((f.detail for f in rep.findings
                       if f.code in BACKEND_ERROR_CODES), "")
        if detail:
            print(f"Reason: {detail}", file=_sys.stderr)
        print("This is not a verdict on the evidence: this machine's verifier could "
              "not load the RFC 3161 decoder.", file=_sys.stderr)
        print("Next: open verify-offline.html from the pack in a browser, or run "
              "provelog verify on a machine where the native extension is permitted "
              "(a WDAC/AV policy can block it; `python -m provelog` works when the "
              "provelog.exe launcher is blocked).", file=_sys.stderr)
    if result == "VERIFIED" and a.integrity == "pass" and a.time == "pass" and a.coverage == "complete":
        rfc = _rfc_anchor_cover(source)
        if rfc:
            max_cover = max(c for c, _t, _u in rfc)
            times = [t for c, t, _u in rfc if c >= max_cover and t]
            cover_time = min(times) if times else ""
            owners = [u for c, t, u in rfc if c >= max_cover and t == cover_time]
            tsa = _tsa_label(owners[0] if owners else _first_tsa_label(source))
            if cover_time:
                print()
                shown = (cover_time[:19].replace("T", " ") + " UTC"
                         if len(cover_time) >= 19 else cover_time)
                print(f"All {n} records existed no later than {shown}.")
                print(f"Timestamp authority: {tsa}")
    elif result == "VERIFIED (partial timestamp coverage)":
        print(f"Warning: {n - covered} records have no verified third-party timestamp.",
              file=_sys.stderr)
    elif result == "INVALID":
        print()
        if a.time == "untrusted":
            print("Reason: timestamp authority not in the recognized list (Sectigo, FreeTSA)")
        elif a.time != "pass":
            print("Reason: no third-party timestamp")
            print("Wait until the evidence pack has been timestamped, then export it "
                  "again and verify again.")
        elif a.coverage not in ("complete", "partial"):
            print("Reason: the timestamp does not cover any record")
        elif a.attribution == "fail":
            print("Reason: device signature invalid")
        else:
            print("Reason: no device signature (--strict requires one)")

    if result.startswith("VERIFIED"):
        print()
        print(NOT_PROVEN_TEXT)
        if a.attribution != "pass":
            print(NOT_SIGNED_TEXT)

    if a.integrity == "fail":
        detail = next((f.detail for f in rep.findings if f.severity == "fail"),
                      "verification failed")
        print(f"Error: {detail}", file=_sys.stderr)
    else:
        already = {
            "anchor_local_only", "local_anchor_only", "no_anchor",
            "anchor_unverified_tsa", "no_device_signature",
        }
        for f in rep.findings:
            if f.severity == "warn" and f.code not in already:
                print(f"Warning: {f.detail}", file=_sys.stderr)


                                                                              
                                         
NOT_PROVEN_TEXT = (
    "What this does not prove: that nothing was withheld before recording, that the "
    "recorded event times\nare the true wall-clock time, or who was at the keyboard.")
NOT_SIGNED_TEXT = (
    "No device signature is present, so it also does not prove who produced these records.")


def _print_axes(rep: Report, bundle: dict[str, Any]) -> None:
    print("\n" + _axes_text(rep, bundle))



def _safe_axes(bundle: dict[str, Any], findings: list[Finding]) -> Axes:
    """Compute axes without ever raising: a hostile bundle must not traceback.

    ``_axes_for`` classifies findings, so if it cannot run we still return the
    conservative default - the verdict itself is carried by the findings list,
    not by the axes.
    """
    try:
        return _axes_for(bundle, findings)
    except Exception:
        return Axes()


def verify_bundle(bundle: dict[str, Any],
                  pack_files: dict[str, bytes] | None = None) -> Report:
    """Verify a bundle and return a Report. Never raises on bad input.

    A bundle is an untrusted file, often a hostile one: this is the function a stranger's
    upload reaches. A malformed bundle must come back as a verdict, not as a traceback, so
    bad shapes are reported as invalid records rather than leaked as internal errors.
    """
    try:
        rep = _verify_bundle(bundle, pack_files)
        rep.axes = _safe_axes(bundle, rep.findings)
        if rep.axes.integrity == "pass" and rep.axes.attribution == "absent":
            rep.add("warn", "no_device_signature",
                    "no device signature present: the timestamp can prove the records "
                    "existed by that time, but attribution is not established")
        return rep
    except CanonicalError as exc:
        rep = Report()
        rep.add("fail", "not_canonicalizable",
                f"a record in this bundle cannot be canonicalized, so no hash over it can be "
                f"checked and nothing in it can be verified: {exc}")
        rep.axes = _safe_axes(bundle, rep.findings)
        return rep
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        rep = Report()
        rep.add("fail", "malformed_bundle",
                f"this bundle is structurally invalid and could not be verified "
                f"({type(exc).__name__}: {exc})")
        rep.axes = _safe_axes(bundle, rep.findings)
        return rep
    except Exception as exc:  # pragma: no cover - last-resort guard
        rep = Report()
        rep.add("fail", "malformed_bundle",
                f"this bundle could not be verified ({type(exc).__name__}: {exc})")
        rep.axes = _safe_axes(bundle, rep.findings)
        return rep


def _structural_defect(bundle: dict[str, Any]) -> str | None:
    """The bundle's shape, checked before anything is hashed. None means the shape is sound.

    Two verifiers exist so that one catches what the other misses, and the only outcome that
    makes the pair worth less than either alone is the two of them disagreeing about the same
    file. The shape is therefore decided up front, by the same explicit rules in both
    implementations, rather than left to whichever exception happens to fire first. This
    checks ONLY the types the format requires; it never looks at a hash, a signature or a
    value. A bundle that passes here can still be tampered with, and that is the next step's
    job.

    Kept in lockstep with the same function in web/verify.js; the test suite asserts the two
    agree over a family of malformed shapes.
    """
    for key in ("records", "anchors"):
        value = bundle.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            return f"'{key}' must be a list, got {type(value).__name__}"
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                return (f"'{key}[{i}]' must be an object, got "
                        f"{'null' if item is None else type(item).__name__}")
    for i, sr in enumerate(bundle.get("records") or []):
        rec = sr.get("record")
        if rec is not None and not isinstance(rec, dict):
            return (f"'records[{i}].record' must be an object, got "
                    f"{type(rec).__name__}")
        if isinstance(rec, dict):
            seq = rec.get("seq")
                                                                                         
            if seq is not None and (isinstance(seq, bool) or not isinstance(seq, int)):
                return (f"'records[{i}].record.seq' must be an integer, got "
                        f"{type(seq).__name__}")
    for i, a in enumerate(bundle.get("anchors") or []):
        receipt = a.get("receipt")
        if receipt is not None and not isinstance(receipt, dict):
            return (f"'anchors[{i}].receipt' must be an object, got "
                    f"{'null' if receipt is None else type(receipt).__name__}")
        covers = a.get("covers_up_to")
        if covers is not None and (isinstance(covers, bool) or not isinstance(covers, int)):
            return (f"'anchors[{i}].covers_up_to' must be an integer, got "
                    f"{type(covers).__name__}")
        seq = a.get("anchor_seq")
        if seq is not None and (isinstance(seq, bool) or not isinstance(seq, int)):
            return (f"'anchors[{i}].anchor_seq' must be an integer, got "
                    f"{type(seq).__name__}")
    return None


def _anchor_kind(a: dict[str, Any]) -> str | None:
    """The receipt kind of one anchor, or None when the receipt is absent/invalid.

    Structural validation guarantees ``receipt`` is an object when present, but
    this helper keeps the anchors loop safe even when that gate is bypassed by a
    caller that feeds ``_verify_bundle`` directly.
    """
    if not isinstance(a, dict):
        return None
    receipt = a.get("receipt")
    if not isinstance(receipt, dict):
        return None
    return receipt.get("kind")


def _as_int(value: Any) -> int:
    """An integer value, or 0 for anything else (a bool is not a count)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _hex_digest(value: Any) -> str | None:
    """Normalize an ``sha256:...`` / bare-hex attachment id to lowercase hex."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if s.startswith("sha256:"):
        s = s[7:]
    if len(s) != 64 or any(c not in "0123456789abcdefABCDEF" for c in s):
        return None
    return s.lower()


def _attachment_digests(bundle: dict[str, Any]) -> set[str]:
    """Every sha256 digest the signed records reference as an attachment.

    The manifest's attachment index is unsigned, so the signed record is the
    only thing that binds a file to the chain. This walks the record payloads
    (the same content/parts/message/item/data shape the importer walks) and
    returns the digests the records actually cite.
    """
    digests: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child)
            return
        if not isinstance(node, dict):
            return
        att = node.get("attachment")
        if isinstance(att, dict):
            d = _hex_digest(att.get("attachmentId") or att.get("sha256"))
            if d:
                digests.add(d)
        d = _hex_digest(node.get("attachmentId") or node.get("sha256"))
        if d:
            digests.add(d)
        for key in ("content", "parts", "message", "item", "data"):
            child = node.get(key)
            if isinstance(child, (list, dict)):
                visit(child)

    for sr in bundle.get("records") or []:
        rec = sr.get("record") or {}
        visit(rec.get("payload"))
    return digests


def _is_attachment_path(name: str) -> bool:
    return "attachments" in name.split("/")[:-1]


def _verify_attachments(bundle: dict[str, Any], pack_files: dict[str, bytes] | None,
                        rep: Report) -> None:
    """Attachment bytes: swapped bytes fail, missing bytes warn, injected files fail.

    The signed records commit to attachment digests. Bytes that are present but
    disagree with the signed digest (or a declared one) are tampering; bytes
    that are simply absent are incompleteness, not tampering (the user may have
    deleted the file, and a future copy can still be checked). A file under
    ``attachments/`` that nothing references and nothing declares is an
    injection and fails. A file named after a 64-hex digest must hash to that
    digest: the filename is itself a commitment, so rewriting the unsigned
    manifest list cannot cover a swap under the original name.

    ``pack_files`` is the evidence ZIP's ``{name: bytes}``; it is ``None`` for a
    raw ``bundle.json`` (no bytes available, so the check degrades to a warning,
    never a false pass).
    """
    import hashlib

    declared = [a for a in (bundle.get("attachments") or []) if isinstance(a, dict)]
    digests = _attachment_digests(bundle)
    if not declared and not digests:
        return

    if pack_files is None:
        rep.add("warn", "attachment_not_included",
                "the bundle references attachments but no files were provided, "
                "so attachment contents were not verified")
        return

    by_name = {n.rsplit("/", 1)[-1]: b for n, b in pack_files.items()}
    by_hash: dict[str, bytes] = {}
    for data in pack_files.values():
        by_hash.setdefault(hashlib.sha256(data).hexdigest(), data)

    declared_digests: set[str] = set()
    declared_names: set[str] = set()
    for a in declared:
        d = _hex_digest(a.get("sha256") or a.get("attachmentId"))
        name = str(a.get("path") or a.get("file") or "").rsplit("/", 1)[-1]
        if name:
            declared_names.add(name)
        if d is None:
                                                                              
                                                                             
                                                                            
            rep.add("warn", "attachment_entry_missing_digest",
                    f"attachment entry {a.get('path') or a.get('file') or '?'} "
                    "has no parseable sha256 digest and was not checked")
            continue
        declared_digests.add(d)
        if d not in digests:
                                                                             
                                                                              
                                                                            
                                                                           
            rep.add("warn", "attachment_unreferenced_declaration",
                    f"attachment {d} is declared in the manifest but no signed "
                    "record references it; it is outside the signed scope")
        data = by_name.get(name) if name else None
        if data is None and d in by_hash:
            data = by_hash[d]
        if data is None:
                                                                               
                                                                             
                                                                      
            rep.add("warn", "attachment_missing",
                    f"attachment {d} is declared but its file is not in the pack; "
                    "the attachment could not be verified")
            continue
        actual = hashlib.sha256(data).hexdigest()
        if actual != d:
            rep.add("fail", "attachment_hash_mismatch",
                    f"attachment {d} hashes to {actual}, not the declared digest")
        size = a.get("bytes")
        if size is not None and _as_int(size) != len(data):
            rep.add("fail", "attachment_size_mismatch",
                    f"attachment {d} is {len(data)} bytes, not the declared {_as_int(size)}")

    for d in sorted(digests - declared_digests):
        if d in by_hash:
            continue                                                     
                                                                             
                                                                           
                                                                     
                                                                             
                                      
        rep.add("warn", "attachment_reference_missing",
                f"record references attachment {d} which is not included in the "
                "pack; the attachment could not be verified")

                                                                           
                                                                          
                                                                             
                                                                              
             
    for name in sorted(n for n in pack_files if _is_attachment_path(n)):
        data = pack_files[name]
        h = hashlib.sha256(data).hexdigest()
        base = name.rsplit("/", 1)[-1]
                                                                              
                                                                             
                                                                             
                                                                          
                                        
        stem = base.rsplit(".", 1)[0] if "." in base else base
        if len(stem) == 64 and all(c in "0123456789abcdef" for c in stem) and h != stem:
            rep.add("fail", "attachment_name_hash_mismatch",
                    f"attachment {base} is named after digest {stem} but its "
                    f"content hashes to {h}")
        if h in digests or h in declared_digests or base in declared_names:
            continue
        rep.add("fail", "attachment_unreferenced_file",
                f"the pack contains attachment {base} that no signed record "
                "references and no manifest entry declares")


def _verify_bundle(bundle: dict[str, Any], pack_files: dict[str, bytes] | None = None) -> Report:
    rep = Report()
    if not isinstance(bundle, dict):
        rep.add("fail", "not_a_bundle",
                f"this file is valid JSON but a {type(bundle).__name__}, not a Provelog "
                f"bundle (a JSON object). Check you pointed at the right file.")
        return rep
                                                                                            
                                                                                              
                                                                                  
    if bundle.get("format") not in ("provelog.bundle/1", "flightrecorder.bundle/1"):
        rep.add("fail", "bad_format", f"unexpected bundle format {bundle.get('format')!r}")
        return rep

    shape = _structural_defect(bundle)
    if shape is not None:
        rep.add("fail", "malformed_bundle", shape)
        return rep

    server_records = bundle.get("records") or []
    if not server_records:
        rep.add("fail", "empty", "bundle has no records")
        return rep

                                                                        
                                                                                            
                                                                                             
                                                             
    prev = GENESIS_PREV_HASH
    recomputed_srh: list[str] = []
    for i, sr in enumerate(server_records):
        if sr.get("recv_seq") != i:
            rep.add("fail", "recv_gap", f"recv_seq gap at index {i}: got {sr.get('recv_seq')}")
        recv_hash = sha256_hex(canonicalize(sr.get("record")))
        if recv_hash != sr.get("recv_hash"):
            rep.add("fail", "recv_hash_mismatch",
                    f"recv_seq {sr.get('recv_seq')}: stored record bytes do not match recv_hash")
        srh = sha256_hex(canonicalize({
            "recv_seq": sr.get("recv_seq"),
            "recv_ts": sr.get("recv_ts"),
            "recv_hash": recv_hash,                                                    
            "server_prev_hash": prev,                                                   
        }))
        if srh != sr.get("server_record_hash"):
            rep.add("fail", "server_chain_break",
                    f"recv_seq {sr.get('recv_seq')}: server receipt chain broken (delete/reorder)")
        recomputed_srh.append(srh)
        prev = srh

                                                                                            
                                                                                                 
                                                                                                 
                                                                                                 
                                                                                                 
                                                                                
    head = bundle.get("server_head")
    if isinstance(head, dict) and recomputed_srh:
        last_seq = server_records[-1].get("recv_seq")
        if head.get("server_record_hash") != recomputed_srh[-1]:
            rep.add("fail", "server_head_mismatch",
                    "server_head does not match the recomputed tail of the receipt chain")
        elif head.get("recv_seq") != last_seq:
            rep.add("fail", "server_head_mismatch",
                    f"server_head claims recv_seq {head.get('recv_seq')}, but the chain ends at "
                    f"{last_seq}")

                                                                
                                                                                      
                                                                                    
                                                                                     
                                    
    client_records = [sr["record"] for sr in server_records]
    sessions: list[list[dict[str, Any]]] = []
    _by_sid: dict[Any, list[dict[str, Any]]] = {}
    for r in client_records:
        sid = r.get("session_id")
        if sid not in _by_sid:
            _by_sid[sid] = []
            sessions.append(_by_sid[sid])
        _by_sid[sid].append(r)
    multi = len(sessions) > 1
    for sess in sessions:
        sess.sort(key=lambda r: r.get("seq", 1 << 30))
        label = f"session {sess[0].get('session_id')}: " if multi else ""
        for issue in verify_client_chain(sess):
            rep.add("fail", f"client_{issue.code}", f"{label}seq={issue.seq}: {issue.detail}")

                                                                                           
    for sid, arrived in _by_arrival_sid(server_records).items():
        label = f"session {sid}: " if multi else ""
        if arrived != sorted(arrived, key=lambda s: s if isinstance(s, int) else 1 << 30):
            rep.add("fail", "arrival_reorder",
                    f"{label}records did not arrive in emission order "
                    "(possible replay or reordering)")
        if len(set(arrived)) != len(arrived):
            rep.add("fail", "duplicate_seq", f"{label}duplicate client seq values (replay)")

                        
                                                                                              
                                                                                            
                                                                                        
                                                                           
    anchors = bundle.get("anchors") or []
    if not anchors:
        rep.add("warn", "no_anchor",
                "no external anchor present; records are not bound to a trusted external time")
    elif all(_anchor_kind(a) == "local" for a in anchors):
        rep.add("warn", "local_anchor_only",
                "only LOCAL anchors present; these carry no independent third-party time proof. "
                "Use an RFC 3161 anchor for evidence that must stand against the sink operator.")
    covering_time: dict[int, str] = {}                                                         
    for a in anchors:
        receipt = a.get("receipt") or {}
        covers = a.get("covers_up_to", -1)
        try:
                                                                                
                                                                               
                                                                           
                                                                          
            leaves = [recomputed_srh[sr["recv_seq"]] for sr in server_records
                      if sr["recv_seq"] < covers and sr["recv_seq"] < len(recomputed_srh)]
            root = merkle_root(leaves)
        except (ValueError, KeyError, TypeError) as e:
            rep.add("fail", "anchor_malformed", f"anchor {a.get('anchor_seq')}: malformed receipt chain: {e}")
            continue
        if receipt.get("kind") in ("aggregate", "aggregate-local"):
                                                                           
                                                                
            status = _verify_aggregate_receipt(receipt, root, rep, a.get("anchor_seq"))
            if status == "fail":
                continue
            if status == "trusted":
                gt = (receipt.get("agg_receipt") or {}).get("gen_time")
                for sr in server_records:
                    if sr["recv_seq"] < covers and sr["recv_seq"] not in covering_time:
                        covering_time[sr["recv_seq"]] = gt
            continue
        if root != receipt.get("merkle_root"):
            rep.add("fail", "anchor_root_mismatch",
                    f"anchor {a.get('anchor_seq')}: Merkle root does not cover the receipt chain")
            continue
        status = _verify_anchor_receipt(receipt, rep, a.get("anchor_seq"))
        if status == "fail":
            continue
                                                                                          
        if status == "trusted":
            gt = receipt.get("gen_time")
            for sr in server_records:
                if sr["recv_seq"] < covers and sr["recv_seq"] not in covering_time:
                    covering_time[sr["recv_seq"]] = gt

                                         
                                                                                         
                                                                                           
                                                                                           
    for sr in server_records:
        gt = covering_time.get(sr["recv_seq"])
        if gt is None:
            continue
        ts = sr["record"].get("ts_utc", "")
        if ts and _seconds_after(ts, gt) > ANCHOR_SKEW_TOLERANCE_S:
            rep.add("warn", "ts_after_anchor",
                    f"recv_seq {sr['recv_seq']}: client ts_utc {ts} is well after its anchor time {gt} "
                    f"(clock skew or backdating; record hash is intact)")
        if _seconds_after(sr.get("recv_ts", ""), gt) > ANCHOR_SKEW_TOLERANCE_S:
            rep.add("warn", "recv_after_anchor",
                    f"recv_seq {sr['recv_seq']}: server recv_ts is well after the covering anchor")

                                                                               
                                                                                 
                                                                                   
    for sess in sessions:
        if not sess:
            continue
        label = f"session {sess[0].get('session_id')}: " if multi else ""
        commits = [r for r in sess if r.get("action_type") == COMMIT]
        if commits:
            covers = int((commits[-1].get("payload") or {}).get("covers_up_to") or 0)
            tail = max(0, len(sess) - covers - 1)
            rep.add("info", "commitment",
                    f"{label}a signed commitment covers {covers} record(s)"
                    + (f"; {tail} after it are still live" if tail else
                       "; nothing has been recorded since"))
        else:
            rep.add("info", "no_commitment",
                    f"{label}no commitment yet: records are hash-chained, but nothing "
                    "fixes how many of them existed at a point in time")
                                                                                
                                                                             
                                                                       
                                                            
    open_pauses: dict[str, str] = {}
    for r in client_records:
        action = r.get("action_type")
        payload = r.get("payload") or {}
        session = str(r.get("session_id") or "")
        if action == "lifecycle.pause":
            open_pauses[session] = str(payload.get("at") or "")
            continue
        if action != "lifecycle.resume":
            continue
        gap = payload.get("gap")
        open_pauses.pop(session, None)
        if not isinstance(gap, dict):
            continue
        if gap.get("unavailable"):
            detail = "the paused interval could not be read from the host log"
        else:
            if gap.get("from_turn") is not None and gap.get("to_turn") is not None:
                span = f"turns {gap['from_turn']}-{gap['to_turn']}"
            else:
                span = (f"host seq {gap.get('from_host_seq', '?')}-"
                        f"{gap.get('to_host_seq', '?')}")
            detail = f"{span} were not recorded"
        detail += (f" - user paused {gap.get('paused_at') or '?'}"
                   f" to {gap.get('resumed_at') or '?'}")
        rep.add("info", "gap", detail)
    for paused_at in open_pauses.values():
        rep.add("info", "gap_open",
                f"recording was paused at {paused_at} and has not resumed; "
                "later events are not in this record")

    n_hb = sum(1 for r in client_records if r.get("action_type") == HEARTBEAT)
    rep.add("info", "summary",
            f"{len(server_records)} records, {len(anchors)} anchors, {n_hb} heartbeats. "
            f"Completeness is never claimed by this tool.")
    _verify_attachments(bundle, pack_files, rep)
    return rep


def _same_instant(a: str, b: str) -> bool:
    """Do two ISO-8601 stamps name the same moment, allowing for how they were written?

    Compared as instants rather than as strings: a TSA that reports whole seconds and a receipt
    that pads microseconds describe the same timestamp, and failing a bundle over trailing zeroes
    would teach operators to ignore the finding that matters. One second of slack, no more,
    because anything larger is a different claim about when something happened.
    """
    def _parse(v: str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    da, db = _parse(a), _parse(b)
    if da is None or db is None:
        return False
    return abs((da - db).total_seconds()) <= 1.0


def _verify_aggregate_receipt(receipt: dict[str, Any], stream_root: str,
                              rep: Report, seq: Any) -> str:
    """Verify an AGGREGATE anchor receipt (one TSA stamp shared by a whole
    epoch of streams). Four independent checks, each its own failure code:

    1. commitment: recompute compute_anchor_commit over THIS stream's claim
       (stream identity, epoch, coverage, and the stream root rebuilt from
       the bundle's own records) and compare to receipt["commitment"] - the
       receipt-substitution defense.
    2. inclusion: fold the proof (AGG_DOMAIN leaf) to a root and compare to
       agg_root - this stream really was in the epoch tree.
    3. root consistency: the shared stamp's merkle_root must be the same
       agg_root it is presented as covering.
    4. the shared stamp itself: validated by the SAME _verify_anchor_receipt
       path a per-stream receipt takes (rfc3161 CMS chain or local sig).

    Returns "trusted" / "untrusted" / "fail" with the same meaning as the
    per-stream path: an aggregate stamp counts as coverage exactly when its
    underlying stamp does."""
    import base64
    import hashlib

    from .. import merkle
    epoch = receipt.get("epoch") or {}
    agg_root = str(receipt.get("agg_root", ""))
    try:
        commit = merkle.compute_anchor_commit(
            receipt.get("stream_id", ""), epoch.get("index", 0),
            receipt.get("covers_up_to", 0), stream_root,
            epoch.get("started_at", ""), "aggregate", "").hex()
    except Exception as e:
        rep.add("fail", "agg_commitment_mismatch",
                f"anchor {seq}: cannot recompute the aggregate commitment: {e}")
        return "fail"
    if commit != receipt.get("commitment"):
        rep.add("fail", "agg_commitment_mismatch",
                f"anchor {seq}: the aggregate commitment does not bind this stream's "
                f"root/coverage/epoch (receipt substitution)")
        return "fail"
                                                                             
                                                                         
    try:
        leaf = bytes.fromhex(merkle.compute_leaf_hash(
            bytes.fromhex(commit), merkle.AGG_DOMAIN))
        proof = [base64.b64decode(p) for p in receipt.get("inclusion_proof") or []]
        size = int(epoch.get("size", 0))
                                                                             
                                                                
        leaf_index = int(receipt.get("leaf_index", -1))
        if leaf_index < 0 or leaf_index >= size:
            raise ValueError("leaf_index missing or out of range")
    except Exception as e:
        rep.add("fail", "agg_inclusion_invalid",
                f"anchor {seq}: malformed aggregate inclusion data: {e}")
        return "fail"
    fn, sn = leaf_index, size - 1
    node = leaf
    for sib in proof:
        if len(sib) != 32:
            rep.add("fail", "agg_inclusion_invalid",
                    f"anchor {seq}: aggregate proof sibling is not 32 bytes")
            return "fail"
        if (fn & 1) or (fn == sn):
            node = hashlib.sha256(b"\x01" + sib + node).digest()
            if not (fn & 1):
                while fn != 0 and not (fn & 1):
                    fn >>= 1
                    sn >>= 1
        else:
            node = hashlib.sha256(b"\x01" + node + sib).digest()
        fn >>= 1
        sn >>= 1
    if sn != 0 or node.hex() != agg_root:
        rep.add("fail", "agg_root_mismatch",
                f"anchor {seq}: the inclusion proof does not fold to the aggregate root")
        return "fail"
    agg_receipt = receipt.get("agg_receipt") or {}
    if agg_receipt.get("merkle_root") != agg_root:
        rep.add("fail", "agg_root_mismatch",
                f"anchor {seq}: the shared stamp covers a different root than the "
                f"aggregate root it is presented with")
        return "fail"
                                                                  
    status = _verify_anchor_receipt(agg_receipt, rep, seq)
    if receipt.get("primary_failed"):
        rep.add("info", "anchor_primary_failed",
                f"anchor {seq}: primary TSA was unavailable; the stamp came from "
                f"{_tsa_label(agg_receipt.get('tsa_url', ''))} as fallback")
    return status


def _verify_anchor_receipt(receipt: dict[str, Any], rep: Report, seq: Any) -> str:
    """Return "trusted" (third-party RFC3161 timestamp validated), "untrusted"
    (signature good but no external trust anchor, e.g. local anchor or unknown TSA),
    or "fail" (the receipt is broken or does not cover this root)."""
    kind = receipt.get("kind")
    root = receipt.get("merkle_root", "")
    if kind == "local":
        ok = verify_signature(
            receipt.get("anchor_pubkey", ""),
            (root + "|" + receipt.get("gen_time", "")).encode("utf-8"),
            receipt.get("signature", ""),
        )
        if not ok:
            rep.add("fail", "anchor_bad_sig", f"anchor {seq}: local anchor signature invalid")
            return "fail"
        rep.add("warn", "anchor_local_only",
                f"anchor {seq}: LOCAL anchor only (no third-party trusted timestamp); "
                f"time {receipt.get('gen_time')} is self-asserted and not externally provable")
        return "untrusted"
    if kind == "rfc3161":
        import base64
        import hashlib

        from ..trust import roots_for_tsa
        from ..tsa_backend import BackendUnavailable, TokenError, decode, verify_token

        try:
            raw = base64.b64decode(receipt["token_b64"])
        except Exception as e:
            rep.add("fail", "anchor_decode_error", f"anchor {seq}: token is not base64: {e}")
            return "fail"
        try:
            token = decode(raw)
        except BackendUnavailable as e:
            rep.add("fail", "verification_backend_unavailable",
                    f"anchor {seq}: the RFC 3161 backend is unavailable on this machine "
                    f"({e}). The evidence itself has not been judged.")
            return "fail"
        except (ImportError, ModuleNotFoundError, OSError) as e:
            rep.add("fail", "verification_backend_unavailable",
                    f"anchor {seq}: the RFC 3161 backend is unavailable on this machine "
                    f"({e}). The evidence itself has not been judged.")
            return "fail"
        except Exception as e:
            rep.add("fail", "anchor_decode_error",
                    f"anchor {seq}: cannot decode RFC3161 token: {e}")
            return "fail"

        mi = token.tst_info.message_imprint
        oid = getattr(mi.hash_algorithm, "dotted_string", "") or getattr(mi.hash_algorithm, "_name", "")
        alg = _DIGEST_OIDS.get(oid, oid).lower()
        if alg not in hashlib.algorithms_available:
            rep.add("fail", "anchor_bad_alg", f"anchor {seq}: unsupported imprint algorithm {oid!r}")
            return "fail"
        expected = hashlib.new(alg, bytes.fromhex(root)).digest()
        if mi.message != expected:
            rep.add("fail", "anchor_imprint_mismatch",
                    f"anchor {seq}: RFC3161 token does not cover this Merkle root")
            return "fail"

        cas = roots_for_tsa(receipt.get("tsa_url"))
        if not cas:
            rep.add("warn", "anchor_unverified_tsa",
                    f"anchor {seq}: imprint matches but TSA {receipt.get('tsa_url')!r} is not in "
                    f"ProveLog's recognized TSA list (Sectigo, FreeTSA), so the TSA "
                    f"signature was not validated")
            return "untrusted"
        last_error: Exception | None = None
        for ca in cas:
            try:
                verify_token(token, ca, bytes.fromhex(root))
                last_error = None
                break
            except BackendUnavailable as e:
                rep.add("fail", "verification_backend_unavailable",
                        f"anchor {seq}: the RFC 3161 verifier is unavailable on this machine "
                        f"({e}). The evidence itself has not been judged.")
                return "fail"
            except TokenError as e:
                last_error = e
                continue
            except (ImportError, ModuleNotFoundError, OSError) as e:
                rep.add("fail", "verification_backend_unavailable",
                        f"anchor {seq}: the RFC 3161 backend is unavailable on this machine "
                        f"({e}). The evidence itself has not been judged.")
                return "fail"
            except Exception as e:
                last_error = e
                continue
        if last_error is not None:
            rep.add("fail", "anchor_sig_invalid",
                    f"anchor {seq}: RFC3161 signature or cert chain did not validate: {last_error}")
            return "fail"

        token_time = token.tst_info.gen_time.astimezone(UTC).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ")
        claimed = str(receipt.get("gen_time") or "")
        if claimed and not _same_instant(claimed, token_time):
            rep.add("fail", "anchor_time_mismatch",
                    f"anchor {seq}: the receipt says it was timestamped at {claimed}, but the "
                    f"token the TSA signed says {token_time}. Trust the token; the receipt is "
                    f"misstating its own evidence.")
            return "fail"
        rep.add("info", "anchor_rfc3161",
                f"anchor {seq}: RFC3161 trusted timestamp from {receipt.get('tsa_url')} "
                f"at {token_time}, signature and cert chain validated")
        return "trusted"
    rep.add("fail", "anchor_unknown_kind", f"anchor {seq}: unknown anchor kind {kind!r}")
    return "fail"


def strictly_verify_rfc3161_receipt(receipt: Any, root_hex: str) -> tuple[bool, str]:
    """(ok, reason) - the gate a REMOTE anchor receipt must pass before it may
    enter the local chain. Unlike _verify_anchor_receipt (which grades into
    trusted/untrusted/fail for a report), this is binary: the token's message
    imprint must cover exactly `root_hex`, the TSA signature and certificate
    chain must validate, and the signing TSA's root must be in the trust
    store. A real signature over someone ELSE'S root - the forged-receipt
    case - fails on the imprint; an unknown TSA fails on the root; a broken
    chain fails on the signature. Anything but (True, _) is treated by the
    caller as remote failure and falls back, never a silent accept.

    Shares every primitive with _verify_anchor_receipt above, so the gate and
    the verifier cannot drift apart."""
    try:
        import base64
        import hashlib

        if not isinstance(receipt, dict) or receipt.get("kind") != "rfc3161":
            return False, "the service returned no RFC 3161 token"
        from ..tsa_der import decode_timestamp_response
        raw = base64.b64decode(receipt["token_b64"])
        tst = decode_timestamp_response(raw)
        mi = tst.tst_info.message_imprint
        oid = getattr(mi.hash_algorithm, "dotted_string", "") \
            or getattr(mi.hash_algorithm, "_name", "")
        alg = _DIGEST_OIDS.get(oid, oid).lower()
        if alg not in hashlib.algorithms_available:
            return False, f"unsupported imprint algorithm {oid!r}"
        expected = hashlib.new(alg, bytes.fromhex(str(root_hex))).digest()
        if mi.message != expected:
            return False, "the token's imprint does not cover this merkle root"
        from ..trust import root_for_tsa
        ca = root_for_tsa(receipt.get("tsa_url"))
        if ca is None:
            return False, (f"TSA {receipt.get('tsa_url')!r} has no root in the "
                           f"trust store; refusing the receipt")
        from rfc3161_client import VerifierBuilder
        verifier = VerifierBuilder().add_root_certificate(ca).build()
        if not verifier.verify_message(tst, bytes.fromhex(str(root_hex))):
            return False, "TSA signature or cert chain did not validate"
        return True, "verified"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


                                                                                 
 
                                                                              
                                                                              
                                                                          
                                                                             
                                                                             
                                                                             
                                                                               
                                                                            
               

                                                                        
DOC_URL = "https://provelog.ai/doc"

_TSA_NAMES = {
    "timestamp.sectigo.com": "Sectigo",
    "timestamp.digicert.com": "DigiCert",
    "timestamp.certum.pl": "Certum",
    "freetsa.org": "FreeTSA",
}


def _tsa_label(tsa_url: str) -> str:
    host = (tsa_url or "").split("://")[-1].split("/")[0]
    return _TSA_NAMES.get(host, host or "unknown TSA")


def _fmt_stamp_time(gen_time: str) -> str:
    """'YYYY-MM-DDTHH:MM:SS.ssssssZ' -> 'YYYY-MM-DD HH:MM UTC' (minute is the
    honest resolution to display; the full value stays in the bundle)."""
    t = (gen_time or "")[:16].replace("T", " ")
    return f"{t} UTC" if len(t) == 16 else (gen_time or "time unknown")


def fact_sentence(bundle: dict[str, Any], filename: str,
                  tsa_signature_verified: bool = True) -> str:
    """The plain-words fact statement printed after a VERIFIED verdict.

    PURE OUTPUT: reads the bundle, asserts nothing, verifies nothing - the
    verdict above it has already been computed. `tsa_signature_verified` is
    the verb discipline between the two entry points: the CLI validated the
    TSA signatures, so it says the chain root IS covered; the browser page
    did NOT (it defers CMS validation), so it says "detected" and appends
    the not-checked clause. web/verify.js's factSentence has its own
    page-side words (browser surface, out of CLI scope). The local-only
    form is identical in both entry points because the local anchor's
    Ed25519 signature IS checked everywhere."""
    from ..strings import t

    records = bundle.get("records") or []
    n = len(records)

                                                                         
                                                                            
                                                             
    rfc = []
    for a in bundle.get("anchors") or []:
        r = a.get("receipt") or {}
        if r.get("kind") == "rfc3161":
            rfc.append({"gen_time": r.get("gen_time", ""),
                        "tsa_url": r.get("tsa_url", ""),
                        "covers_up_to": a.get("covers_up_to", -1)})
        elif r.get("kind") == "aggregate":
            shared = r.get("agg_receipt") or {}
            rfc.append({"gen_time": shared.get("gen_time", ""),
                        "tsa_url": shared.get("tsa_url", ""),
                        "covers_up_to": a.get("covers_up_to", -1)})
    head = t("fact.head", n=n, file=filename)
    if not rfc:
                                                                         
        return head + t("fact.local_only") + t("fact.end_doc",
                                                       doc=DOC_URL)

                                                                            
    counts: dict[str, int] = {}
    for r in rfc:
        name = _tsa_label(r.get("tsa_url", ""))
        counts[name] = counts.get(name, 0) + 1
    ordered = sorted(rfc, key=lambda r: r.get("gen_time", ""))
    t1 = _fmt_stamp_time(ordered[0].get("gen_time", ""))
    t2 = _fmt_stamp_time(ordered[-1].get("gen_time", ""))
    n1 = _tsa_label(ordered[0].get("tsa_url", ""))
    n2 = _tsa_label(ordered[-1].get("tsa_url", ""))
    k = len(rfc)

                                                                  
    max_cover = max(a.get("covers_up_to", -1) for a in rfc)
    tail = sum(1 for r in records if r.get("recv_seq", 0) >= max_cover)
    tail_part = (t("fact.tail", tail=tail) if tail > 0 else "")

    groups = t("fact.group_sep").join(
        t("fact.group_item", name=name, k=c) for name, c in counts.items())
    single = len(counts) == 1
    if tsa_signature_verified:
                                                                                
        ts = (t("fact.covered_single", k=k, name=n1, t1=t1, t2=t2)
              if single else
              t("fact.covered_multi", k=k, groups=groups,
                 t1=t1, n1=n1, t2=t2, n2=n2))
        return head + ts + tail_part + t("fact.end_doc", doc=DOC_URL)
                                                        
    unchecked = t("fact.unchecked")
    ts = (t("fact.detected_single", k=k, name=n1, t1=t1, t2=t2)
          if single else
          t("fact.detected_multi", k=k, groups=groups,
             t1=t1, n1=n1, t2=t2, n2=n2))
    return head + ts + tail_part + unchecked


def _first_mismatch_seq(rep: Any) -> int | None:
    """The first record a tampering report names as ALTERED (a recv_/
    client_hash_mismatch detail carries 'recv_seq N' or 'seq=N'); chain
    breaks downstream of it are consequences, so they never pick {n}."""
    import re as _re
    for f in rep.findings:
        if f.code in ("recv_hash_mismatch", "client_hash_mismatch"):
            m = _re.search(r"(?:recv_seq|seq=?)\s*(\d+)", f.detail)
            if m:
                return int(m.group(1))
    return None


def _rfc_anchor_cover(bundle: dict[str, Any]) -> list[tuple[int, str, str]]:
    """(covers_up_to, gen_time, tsa_url) for every RFC 3161 stamp in the bundle.

    Kept separate from `_first_tsa_label` because the plain-words paragraph must
    name the stamp that actually covers the records it claims, not simply the
    earliest one in the file."""
    out: list[tuple[int, str, str]] = []
    for a in bundle.get("anchors") or []:
        r = a.get("receipt") or {}
        shared = r.get("agg_receipt") or {}
                                                                           
                                                                            
        kind = r.get("kind")
        if kind in (None, "aggregate"):
            kind = shared.get("kind")
        if kind != "rfc3161":
            continue
        try:
            covers = int(a.get("covers_up_to") or 0)
        except (TypeError, ValueError):
            covers = 0
        out.append((covers,
                    r.get("gen_time") or shared.get("gen_time", ""),
                    r.get("tsa_url") or shared.get("tsa_url", "")))
    return out


def _first_tsa_label(bundle: dict[str, Any]) -> str:
    """The TSA name for the plain-words stamped-paragraph: the first RFC 3161
    stamp's label in gen_time order (same discipline fact_sentence uses)."""
    rfc = _rfc_anchor_cover(bundle)
    if not rfc:
        return "?"
    first = min(rfc, key=lambda r: r[1])
    return _tsa_label(first[2])


def say_not_json(e: json.JSONDecodeError, name: str = "bundle.json") -> None:
    """A parse failure is a path mistake, not a verdict about evidence."""
    print("RESULT: NOT A BUNDLE")
    print(f'Error: Could not parse "{name}" as a ProveLog evidence file.',
          file=sys.stderr)
    print(f"Reason: {e.msg} at line {e.lineno}", file=sys.stderr)


def say_not_utf8(name: str = "bundle.json") -> None:
    """A binary file is not a bundle; say so without a decoder traceback."""
    print("RESULT: NOT A BUNDLE")
    print(f'Error: Could not read "{name}".', file=sys.stderr)
    print("Reason: the file is not UTF-8 text.", file=sys.stderr)


def load_bundle_path(path: str) -> tuple[dict[str, Any], str, dict[str, bytes] | None]:
    """Load a bundle JSON or an evidence-pack ZIP.

    A pack carries ``bundle.json`` and optionally ``anchor.json``; the anchor is
    attached as an anchor entry so the same verifier checks both. Returns the
    bundle, the name to show in human output, and the pack's file bytes (or
    ``None`` for a raw JSON bundle), so attachment bytes can be verified.
    """
    import zipfile
    p = Path(path)
    if not zipfile.is_zipfile(p):
        return json.loads(p.read_text(encoding="utf-8")), path, None
    with zipfile.ZipFile(p) as z:
        names = set(z.namelist())
        pack_files = {n: z.read(n) for n in names}
        bundle_name = next((n for n in names if n.rsplit("/", 1)[-1] == "bundle.json"), "")
        if not bundle_name:
            raise ValueError("evidence pack has no bundle.json")
        bundle = json.loads(z.read(bundle_name).decode("utf-8"))
        anchor_name = next((n for n in names if n.rsplit("/", 1)[-1] == "anchor.json"), "")
        if anchor_name:
            anchor = json.loads(z.read(anchor_name).decode("utf-8"))
            receipt = anchor.get("receipt") if isinstance(anchor, dict) else None
            if receipt is None:
                receipt = anchor
            covers = anchor.get("covers_up_to") if isinstance(anchor, dict) else None
            if covers is None:
                covers = len(bundle.get("records") or [])
            anchors = list(bundle.get("anchors") or [])
            if not anchors:
                anchors = [{"anchor_seq": 0, "covers_up_to": covers,
                            "receipt": receipt}]
            bundle = {**bundle, "anchors": anchors}
    return bundle, path, pack_files



def _exit_code_for(rep: Report, strict: bool) -> int:
    """The process exit code for one report (shared by single packs and members)."""
    if rep.result == "cannot_verify":
                                                                             
                                                                            
                                         
        return 7
    if strict:
        if rep.axes.integrity != "pass":
            return 1
        if rep.axes.time != "pass":
            return 4
        if rep.axes.coverage != "complete":
            return 5
        if rep.axes.attribution != "pass":
            return 3
        return 0
    if not rep.ok:
        return 1
    a = rep.axes
    if a.integrity == "pass":
        if a.time != "pass":
            return 4
        if a.attribution == "fail":
            return 3
        if a.coverage not in ("complete", "partial"):
            return 5
    return 0


                                                                          
                                                                      
_COLLECTION_RANK = {1: 6, 7: 5, 3: 4, 4: 3, 5: 2, 0: 1}


def _collection_verdict(words: list[str]) -> str:
    if any(w == "TAMPERING DETECTED" for w in words):
        return "TAMPERING DETECTED"
    if any(w == "CANNOT VERIFY" for w in words):
        return "CANNOT VERIFY"
    if words and all(w.startswith("VERIFIED") for w in words):
        if any(w.endswith("(partial timestamp coverage)") for w in words):
            return "VERIFIED (partial timestamp coverage)"
        return "VERIFIED"
    return "INVALID"


def _collection_reason(rep: Report) -> str:
    a = rep.axes
    if rep.result == "cannot_verify":
        return "verification backend unavailable"
    if a.integrity == "fail":
        return "record integrity failed"
    if a.time == "untrusted":
        return "TSA not in the recognized list"
    if a.time != "pass":
        return "no verified timestamp"
    if a.attribution == "fail":
        return "device signature invalid"
    if a.coverage not in ("complete", "partial"):
        return "timestamp covers no record"
    return ""


def verify_collection(path: str, args) -> int:
    """Verify every member of a collection ZIP and aggregate the verdicts."""
    from ..collection import read_collection
    try:
        got = read_collection(path)
    except (OSError, ValueError) as exc:
        print("RESULT: NOT A BUNDLE", file=sys.stderr)
        print(f'Error: Could not read "{path}".', file=sys.stderr)
        print(f"Reason: {exc}", file=sys.stderr)
        return 2
    if got is None:
        print("RESULT: NOT A BUNDLE", file=sys.stderr)
        print(f'Error: "{path}" is not a ProveLog collection.', file=sys.stderr)
        return 2
    meta, members = got
    rows = []
    codes: list[int] = []
    words: list[str] = []
    for member in members:
        rep = verify_bundle(member["bundle"], member["files"])
        word = _result_word(rep, args.strict)
        code = _exit_code_for(rep, args.strict)
        rows.append((member, rep, word, code))
        words.append(word)
        codes.append(code)
    verdict = _collection_verdict(words)
    worst = max(codes, key=lambda c: _COLLECTION_RANK.get(c, 0)) if codes else 1
    verified = sum(1 for c in codes if c == 0)

    if args.json:
        print(json.dumps({
            "format": "provelog.collection/1",
            "ok": worst == 0,
            "verdict": verdict,
            "members": [
                {"path": m["meta"].get("path"), "verdict": w, "ok": c == 0,
                 "records": len((m["bundle"] or {}).get("records") or []),
                 "axes": rep.to_dict(args.strict)["axes"],
                 "findings": rep.to_dict(args.strict)["findings"]}
                for m, rep, w, c in rows
            ],
            "summary": {"total": len(rows), "verified": verified,
                        "failed": len(rows) - verified},
        }, indent=2, ensure_ascii=False))
        return worst

    print(f"Collection: {len(rows)} sessions")
    print()
    for member, rep, word, _code in rows:
        m = member["meta"]
        title = " ".join(str(m.get("title") or m.get("session_id") or "").split())
        if len(title) > 44:
            title = title[:43] + "…"
        records = len((member["bundle"] or {}).get("records") or [])
        note = "" if word.startswith("VERIFIED") else _collection_reason(rep)
        print(f"  {word:<10} {title:<44} {records:>6} records"
              + (f"  {note}" if note else ""))
    print()
    print(f"RESULT: {verdict}")
    print(f"Collection: {verified} of {len(rows)} sessions verified.")
    if worst == 0:
        print()
        print(NOT_PROVEN_TEXT)
        print("This container is a convenience wrapper; the set itself is not "
              "proven complete.")
    return worst


def main(argv: list[str] | None = None) -> int:
    from ..strings import t
    p = argparse.ArgumentParser(prog="provelog verify",
                                description=t("verify.sp_description"),
                                epilog=t("verify.exit_codes"),
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("bundle", help=t("verify.sp_bundle"))
    p.add_argument("--json", action="store_true", help=t("verify.sp_json"))
    p.add_argument("--tsa-root", action="append", metavar="HOST=CERT.pem",
                   help=t("verify.sp_tsa_root"))
    p.add_argument("--strict", action="store_true",
                   help=t("verify.sp_strict"))
    args = p.parse_args(argv)

    if args.tsa_root:
        from .. import trust
        for pair in args.tsa_root:
            if "=" not in pair:
                print(t("verify.err_tsa_root_fmt", value=pair))
                return 2
            host, cert_path = pair.split("=", 1)
            trust.add_root(host.strip(), cert_path.strip())

                                                                       
                                                                               
                                                                    
    if args.bundle != "-":
        from ..collection import read_collection
        try:
            _collection = read_collection(args.bundle)
        except (OSError, ValueError) as exc:
            import zipfile as _zipfile
            if _zipfile.is_zipfile(args.bundle):
                with _zipfile.ZipFile(args.bundle) as _z:
                    if "collection.json" in _z.namelist():
                        print("RESULT: NOT A BUNDLE", file=sys.stderr)
                        print(f'Error: Could not read "{args.bundle}".', file=sys.stderr)
                        print(f"Reason: {exc}", file=sys.stderr)
                        return 2
            _collection = None
        if _collection is not None:
            return verify_collection(args.bundle, args)

                                                                                                         
                                                                                              
                        
    display_name = args.bundle if args.bundle != "-" else "bundle.json"
    pack_files: dict[str, bytes] | None = None
    try:
        if args.bundle == "-":
            raw = sys.stdin.read()
            bundle = json.loads(raw)
        else:
            bundle, display_name, pack_files = load_bundle_path(args.bundle)
    except UnicodeDecodeError:
        say_not_utf8(args.bundle)
        return 2
    except json.JSONDecodeError as e:
        say_not_json(e, args.bundle)
        return 2
    except (OSError, ValueError) as e:
        print("RESULT: NOT A BUNDLE", file=sys.stderr)
        print(f'Error: Could not read "{args.bundle}".', file=sys.stderr)
        print(f"Reason: {e}", file=sys.stderr)
        return 2
    required = upgrade_required(bundle)
    if required is not None:
        message = (f"This bundle requires ProveLog {required} or newer.\n"
                   f"Installed version: {__version__}")
        if args.json:
            print(json.dumps({
                "ok": False, "result": "upgrade_required",
                "required_verifier": required, "current_verifier": __version__,
                "download_url": DOWNLOAD_URL,
                "message": message,
            }, ensure_ascii=False))
        else:
            print(message)
            print()
            print("Update:")
            print("  python -m pip install --upgrade provelog")
        return 6

    rep = verify_bundle(bundle, pack_files)

    if args.json:
        print(json.dumps(rep.to_dict(strict=args.strict), indent=2))
    else:
        _human_verify(rep, bundle, display_name, strict=args.strict)
    if args.strict and rep.axes.attribution != "pass":
        print("Warning: --strict requires a valid device signature; this pack "
              "has none.", file=sys.stderr)
    return _exit_code_for(rep, args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
