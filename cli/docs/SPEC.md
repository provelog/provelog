# ProveLog evidence format and verification protocol, v1

This is the v1 wire format. It is deliberately small so an independent verifier can
reimplement it. All hashes are SHA-256 unless stated; all signatures are Ed25519;
canonicalization is the JCS subset in §1.

Two reference implementations exist: the Python verifier (`provelog verify`, including
RFC 3161 CMS validation) and the browser verifier (`web/verify.js`). They share frozen
vectors.

## 1. Canonicalization

To hash or sign a structure:

1. NFC-normalize every string (keys and values).
2. Sort object keys by Unicode code point (ASCII field names in this format).
3. No insignificant whitespace; separators `,` and `:`.
4. UTF-8 output; non-ASCII string values are preserved raw.
5. Floating-point numbers are forbidden. Integers outside `[-(2^53-1), 2^53-1]`
   must be encoded as strings.

`H(x)` means `SHA-256(canonicalize(x))`, hex.

## 2. Client record

Signed content is every field except `record_hash` and `record_sig`:

```
content = {
  v: "1", stream_id, session_id, record_id, seq, prev_hash, ts_utc,
  pubkey,
  action_type, payload
}
record_hash = H(content)
record_sig  = Ed25519_sign(privkey, canonicalize(content))
```

- `seq` is monotonic from 0 within a stream.
- `prev_hash` is the previous `record_hash`, or 64 zeros for genesis.
- `ts_utc` is client-asserted and untrusted; it is protected by the hash/signature,
  but its real-world truth is only bounded by an RFC 3161 anchor.
- Lifecycle records:
  - `lifecycle.session_start`: genesis.
  - `lifecycle.commitment`: a checkpoint; `payload.covers_up_to` names how many
    records are final and `payload.root` carries their Merkle root.
  - `lifecycle.heartbeat`: liveness.

## 3. Receipt chain

The recording side extends a receipt chain keyed only on data it controls:

```
recv_hash          = H(received_record)   // full record, including record_hash/record_sig
server_record_hash = H({ recv_seq, recv_ts, recv_hash, server_prev_hash })
```

- `recv_seq` is monotonic from 0 per stream (arrival order).
- `server_prev_hash` is the previous `server_record_hash`, or 64 zeros for the first.
- Duplicate `recv_hash` is idempotent: the original receipt is returned, not re-appended.

A bundle carries this chain; the verifier needs nothing but the bundle. The hosted
service receives no records and computes no receipts.

## 4. Anchor

A Merkle tree (RFC 6962) is built over the ordered `server_record_hash` leaves:

```
leaf = SHA-256(0x00 || h)
node = SHA-256(0x01 || left || right)
odd final node promoted unchanged
```

The root is hex-encoded as `merkle_root`.

An anchor receipt is one of:

- `kind: "rfc3161"`: an RFC 3161 timestamp over `bytes.fromhex(merkle_root)`.
  Fields: `merkle_root`, `gen_time`, `token_b64`, `tsa_url`.
- `kind: "local"`: a development/self-asserted anchor with no third-party trust.
  Fields: `merkle_root`, `gen_time`, `signature`, `anchor_pubkey`.
- `kind: "aggregate"` / `"aggregate-local"`: a production receipt that folds many
  streams into ONE epoch tree. It carries `commitment` (the stream's own root and
  coverage), `leaf_index`, `inclusion_proof`, `agg_root`, and the shared
  `agg_receipt` (itself an `rfc3161` or `local` receipt over `agg_root`).

`covers_up_to` is the number of leaves covered; it is the count, not the last `recv_seq`.

The aggregate fields, so a third-party verifier can recompute them:

```
commitment = SHA-256(canonicalize({stream_id, anchor_seq, covers_up_to,
                                    merkle_root, gen_time, kind, token_b64_or_sig}))
leaf       = SHA-256(0x00 || "provelog.ai/v1/agg-leaf:" || commitment)
```

- `anchor_seq` is the epoch index, `merkle_root` the stream root this receipt
  covers, `gen_time` the epoch start (`epoch.started_at`), `kind` is `"aggregate"`,
  and `token_b64_or_sig` is `""`. A verifier recomputes `commitment` over these
  fields (with the stream root rebuilt from the bundle's own records) and
  rejects the receipt on mismatch.
- The epoch tree is an RFC 6962 tree over the leaf hashes above (nodes
  `SHA-256(0x01 || left || right)`, odd final node promoted unchanged).
  `leaf_index` is this stream's position in that tree (NOT the epoch counter),
  and `inclusion_proof` is the RFC 6962 audit path folding `leaf` to `agg_root`
  (RFC 6962 sec 2.1.1).
- `agg_receipt` is an `rfc3161` or `local` receipt over
  `bytes.fromhex(agg_root)`, and its `merkle_root` must equal `agg_root`.

## 5. Bundle

```
{
  format: "provelog.bundle/1",
  stream_id,
  server_head: { recv_seq, server_record_hash },
  records: [
    { recv_seq, recv_ts, recv_hash, server_prev_hash, server_record_hash, record }
  ],
  anchors: [ { anchor_seq, covers_up_to, receipt } ]
}
```

`server_head` must match the receipt chain the verifier recomputes; disagreement is
`server_head_mismatch`.

Before hashing, reject structural errors as `malformed_bundle`: `records`/`anchors` not
lists, an element not an object, a `record` not an object, or `seq` not an integer.

## 6. Verification algorithm

1. **Bundle shape.** Check `format` is `provelog.bundle/1`; reject malformed structure.
2. **Records.** For each record in order:
   - recompute `record_hash` over its content and compare;
   - verify `record_sig` with `pubkey`;
   - check `seq` continuity and `prev_hash`.
3. **Server chain.** Recompute `recv_hash` and `server_record_hash` for each row; check
   `recv_seq` continuity and `server_prev_hash`; compare the final value to
   `server_head.server_record_hash` and `recv_seq`.
4. **Anchors.** For each anchor:
   - rebuild the Merkle root over leaves with `recv_seq < covers_up_to` (a count, so the
     first `covers_up_to` leaves);
   - assert it equals `receipt.merkle_root`;
   - RFC 3161: check the token's messageImprint and verify CMS/certificate chain to a
     trusted TSA root. Unknown root is a warning, not trusted.
   - local: verify the Ed25519 signature; report no third-party trust.
5. **Time.** For records covered by a trusted anchor, the anchor `gen_time` is the
   authoritative upper bound. Flag a `ts_utc` later than that by more than the tolerance
   as a warning; the hash chain already protects integrity.

## 7. Verdicts

Derived only from `fail` findings:

- `verified`: no `fail`.
- `malformed`: a format-error fail (`not_a_bundle`, `bad_format`, `malformed_bundle`).
  No integrity claim is made.
- `empty`: a well-formed bundle with zero records.
- `tampered`: any other `fail`.

## 8. Guarantees and limits

A `verified` result proves the recorded client chain and receipt chain are internally
consistent, the signatures are valid, and any trusted RFC 3161 anchor covers the
claimed Merkle root.

It does **not** prove:

- completeness: nothing can prove an event was not omitted before recording;
- that a `ts_utc` written by the agent is truthful, except within a trusted anchor's
  bound;
- that a local anchor is independent time (it is not).

The local plaintext is the evidence. Disk security equals content security.
