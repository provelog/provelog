"""One-click pack.

Produces a single self-contained ZIP a person can hand to someone else with no
engineering involvement: the verifiable bundle, a plain-text verification guide,
and a manifest listing the SHA-256 of every file. The pack is self-verifying:
the recipient runs `provelog verify` (or the offline verifier) on the enclosed
bundle and gets the same result, trusting neither the agent, the sink, nor us. Nothing in the pack is a
certification of compliance; it is evidence. (The regime report layers were
removed with the report family; the pack is the evidence, the verifier is
the judgment.)
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from .canonical import sha256_hex

BUNDLE_FORMAT_VERSION = 1
BUNDLE_VERIFIER_MIN = "1.0.0"
                                                                          
                                                                           
ATTACHMENTS_VERIFIER_MIN = "1.2.0"


def with_format_headers(bundle: dict[str, Any]) -> dict[str, Any]:
    """Add the bundle version contract without overriding a caller's explicit value."""
    out = dict(bundle)
    out.setdefault("format_version", BUNDLE_FORMAT_VERSION)
    out.setdefault("verifier_min", BUNDLE_VERIFIER_MIN)
    return out


_DISCLAIMER = (
    "This pack is evidence produced by an automated system. It records what the "
    "recording pipeline received; it does not certify completeness (that nothing "
    "was withheld before being recorded) and it is not a legal certification."
)

_GUIDE = """Double-click verify-offline.html first. Drop bundle.json (or this ZIP) on it.
It verifies offline, with no install and no network.

How to verify this pack
=================================

This pack contains a tamper-evident record of what an AI agent did. You do not
have to trust the party who produced it.

1. Double-click verify-offline.html, then drop bundle.json (or this whole
   evidence.zip) onto the page. It verifies entirely in your browser, with no
   network connection and no installation.

2. If you prefer the command line, install the open-source verifier:
       pip install provelog

3. Verify the record, trusting neither the agent nor the sink that stored it.
   The CLI accepts the whole pack (it reads bundle.json and anchor.json):
       provelog verify evidence.zip
   You can also unzip it and run:
       provelog verify bundle.json

   A clean result means every record in this pack is signed, hash-chained, and
   (when a trusted timestamp is present) bound to an RFC 3161 time: nothing inside
   the pack has been altered or reordered, and stamped records are proven to have
   existed no later than that time. Any tampering inside the pack is reported.
   This does not prove that nothing was withheld before recording, that nothing
   after the last included record is missing, or that the recorded event times are
   the true wall-clock time (see "What this proves" below).

4. Or verify in a browser on another machine: open https://provelog.ai/verify and
   drop in evidence.zip (or bundle.json). The file is checked locally in your
   browser and is never uploaded.

5. Cross-check file integrity against MANIFEST.json (SHA-256 of each file).

Exit artifact promise
---------------------
The verifier and evidence format are open source. This pack contains the
verifier itself; if ProveLog is gone, verify-offline.html still works.

What this proves, and what it does not
--------------------------------------
Proven: the recorded events are tamper-evident, locally stored, and independently verifiable.
NOT proven: completeness. A record cannot prove that nothing was withheld before it was
recorded when the host exposes it in its own session log.

Legal notice
------------
{disclaimer}
"""


_OFFLINE_VERIFIER = "verify-offline.html"
_OFFLINE_README = """This pack proves itself.
========================

verify-offline.html   double-click this file, then drop bundle.json or the
                      whole evidence.zip onto the page. It verifies every
                      hash, signature, chain link, Merkle root, and RFC 3161
                      timestamp locally in your browser. No network, no
                      installation.

bundle.json           the recorded conversation / record set.
MANIFEST.json         the SHA-256 and byte count of every file here.
VERIFY.txt            the short human instructions.
trust/                the pinned Sectigo and FreeTSA TSA roots used by the
                      offline verifier. They are public certificates, not keys.

Nothing in this pack is uploaded anywhere. The CLI is an alternative, not a
replacement: `provelog verify bundle.json`. The verifier and the evidence
format are open source; if ProveLog itself is gone, this file still verifies.
"""


def _offline_assets() -> dict[str, bytes]:
    """The self-contained verifier and the public TSA roots it pins.

    These are package data, generated from web/verify.* at build time.
    A pack without them is not the artifact the product promise describes.
    """
    base = Path(__file__).resolve().parent
    verifier = base / "web" / _OFFLINE_VERIFIER
    if not verifier.is_file():
        raise RuntimeError(
            "verify-offline.html is missing; rebuild the package data")
    assets = {_OFFLINE_VERIFIER: verifier.read_bytes(),
              "README.txt": _OFFLINE_README.encode("utf-8")}
                                                                              
                                                                             
    notices = base / "web" / "THIRD_PARTY_NOTICES.md"
    if not notices.is_file():
        notices = base.parents[1] / "web" / "THIRD_PARTY_NOTICES.md"
    if notices.is_file():
        assets["THIRD_PARTY_NOTICES.txt"] = notices.read_bytes()
    for source, target in (("sectigo_tsa_cacert.pem", "trust/sectigo.pem"),
                           ("freetsa_cacert.pem", "trust/freetsa.pem")):
        path = base / "trust" / source
        if path.is_file():
            assets[target] = path.read_bytes()
    return assets


def build_pack(bundle: dict[str, Any], *,
               extra_files: dict[str, bytes] | None = None,
               guide: str | None = None,
               unverified: bool = False) -> bytes:
    """Return the bytes of a ZIP pack for the given bundle.

    extra_files ride along as members of the same pack (and land in
    MANIFEST.json, so nothing ships unmanifested); guide replaces the
    default VERIFY.txt text - for packs whose evidence is shaped
    differently than a full-run bundle (the chat viewer's session
    export), the guide must say what actually checks and what does not."""
    bundle = with_format_headers(bundle)
    if guide is None:
        guide = _GUIDE.format(disclaimer=_DISCLAIMER)
    if unverified:
        guide = (
            "IMPORTANT: this pack was exported on a machine where the RFC 3161\n"
            "verifier could not run, so it was NOT verified locally.\n"
            "Verify it with verify-offline.html in a browser, or on another machine.\n\n"
            + guide
        )
    tier = str(bundle.get("capture_tier") or "")
    if tier:
        guide = guide.replace(
            "How to verify this pack\n=================================\n",
            f"How to verify this pack\n=================================\n\n"
            f"Capture method: {tier}\n", 1)
    files: dict[str, bytes] = {
        "bundle.json": json.dumps(bundle, indent=2, ensure_ascii=False).encode("utf-8"),
        "VERIFY.txt": guide.encode("utf-8"),
    }
    files.update(_offline_assets())
    if extra_files:
        files.update(extra_files)

    manifest = {
        "format": "provelog.pack/1",
        "stream_id": bundle.get("stream_id"),
        "files": {name: {"sha256": sha256_hex(data), "bytes": len(data)}
                  for name, data in files.items()},
        "disclaimer": _DISCLAIMER,
    }
    files["MANIFEST.json"] = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")

    buf = io.BytesIO()
                                                                                               
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, files[name])
    return buf.getvalue()
