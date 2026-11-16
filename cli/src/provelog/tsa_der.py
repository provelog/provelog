"""Lenient RFC 3161 token decoding for real-world TSAs.

rfc3161-client's parser is strictly DER and rejects tokens whose certificate
SET is not lexicographically sorted - and at least one major commercial TSA
(Sectigo, our new default) ships tokens with the leaf certificate first and
the intermediates after, which violates SET OF ordering while remaining
perfectly parseable everywhere else. The verifier would report those tokens
as undecodable, i.e. the DEFAULT anchor would produce receipts that fail
`provelog verify`.

The fix is narrow and safe: re-sort the certificate blobs. CMS signs the
content and the signed attributes, never the certificates field, so the
signature stays valid; and reordering equal-length blobs keeps every
enclosing DER length prefix correct, so the rebuilt bytes are valid strict
DER of the same object. Nothing else in the token is touched.
"""

from __future__ import annotations


def _tlv_end(data: bytes, i: int) -> int:
    """End offset of the TLV whose tag byte is at i (limited-length forms)."""
    first = data[i + 1]
    if first < 0x80:
        n, header = first, 2
    else:
        k = first & 0x7F
        n, header = int.from_bytes(data[i + 2:i + 2 + k], "big"), 2 + k
    return i + header + n


def _is_certificate(blob: bytes) -> bool:
    try:
        from cryptography.x509 import load_der_x509_certificate
        load_der_x509_certificate(blob)
        return True
    except Exception:
        return False


def _certificate_runs(data: bytes) -> list[list[tuple[int, int, bytes]]]:
    """Every maximal byte-adjacent run of blobs that parse as certificates.

    Certificates sit side by side inside the SignedData certificates field
    with nothing between them; anything else (content, signer infos) is
    separated by at least a tag-and-length header, so adjacency plus
    parses-as-a-certificate isolates the set."""
    runs: list[list[tuple[int, int, bytes]]] = []
    current: list[tuple[int, int, bytes]] = []
    i = 0
    while True:
        j = data.find(b"\x30\x82", i)
        if j < 0:
            break
        try:
            end = _tlv_end(data, j)
            blob = data[j:end]
        except (IndexError, ValueError):
            i = j + 1
            continue
        if 0 < end <= len(data) and _is_certificate(blob):
            if current and current[-1][1] != j:
                runs.append(current)
                current = []
            current.append((j, end, blob))
            i = end
            continue
        i = j + 1
    if current:
        runs.append(current)
    return runs


def normalize_token(raw: bytes) -> bytes:
    """Re-sort any unsorted certificate run; identity when none or sorted."""
    for run in _certificate_runs(raw):
        if len(run) < 2:
            continue
        blobs = [b for _, _, b in run]
        if blobs == sorted(blobs):
            continue
        start, end = run[0][0], run[-1][1]
        raw = raw[:start] + b"".join(sorted(blobs)) + raw[end:]
    return raw


def decode_timestamp_response(raw: bytes):
    """The package's decode choke point: normalize, then strict-decode."""
    from rfc3161_client import decode_timestamp_response as _strict
    return _strict(normalize_token(raw))


def normalize_receipt_tokens(receipt: dict) -> dict:
    """Return a copy of a receipt with every RFC 3161 token normalized.

    Relay aggregate receipts keep the token under ``agg_receipt.token_b64``;
    that token is not part of the aggregate commitment (the relay commits to
    the stream root, not the TSA response), so normalizing it is safe and makes
    exported packs strict-DER parseable. Historical stored receipts are only
    copied on the way out, never rewritten in place.
    """
    import base64

    out = dict(receipt)
    for key in ("agg_receipt", "receipt"):
        child = out.get(key)
        if not isinstance(child, dict):
            continue
        child = dict(child)
        token = child.get("token_b64")
        if token:
            try:
                child["token_b64"] = base64.b64encode(
                    normalize_token(base64.b64decode(token))).decode("ascii")
            except (ValueError, TypeError):
                pass
        out[key] = child
    token = out.get("token_b64")
    if token:
        try:
            out["token_b64"] = base64.b64encode(
                normalize_token(base64.b64decode(token))).decode("ascii")
        except (ValueError, TypeError):
            pass
    return out
