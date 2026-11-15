# ProveLog

**Tamper-evident evidence for AI-agent work.** ProveLog keeps a complete
trajectory of an agent session on your own disk, signs it with a device key, and
sends only a Merkle root to a relay for an independent RFC 3161 timestamp.
Anyone can verify the resulting evidence pack offline.

ProveLog records. It does not block, gate, approve, or intercept. It is not an
audit platform, a compliance product, or a self-hosted service.

## Install

```bash
pip install provelog
provelog setup
```

`setup` installs a background collector that reads the coding agents' own
local session logs. To browse what was recorded, run `provelog browse`; it opens
a local, read-only viewer on macOS, Windows and Linux.

It does not install hooks, change model base URLs, or rewrite host settings.
It records from install time forward; earlier conversations are not replayed.

Then use your agent normally. ProveLog discovers the local transcript and
collects new events.

## Everyday commands

```text
provelog setup       Set up automatic recording
provelog status      Check status
provelog doctor      Check recording and verification health
provelog browse        View records
provelog export      Export an evidence pack
provelog verify      Verify an evidence pack
provelog stop        Pause recording
provelog resume      Resume recording
provelog uninstall   Remove background service files and keep existing evidence
```

Internal mechanisms (not `provelog` subcommands) are the collector module
and the operator relay (invoked as `python -m provelog.server`). The hosted
relay is operated by the project, not deployed by users.

## Evidence

Each session is one folder under `~/ProveLog Records/<agent>/`:

```text
transcript-preview.md    human-readable conversation
trajectory.jsonl.zst     append-only signed record (source of truth)
manifest.json            chain head, capture tier, coverage, anchors
attachments/             referenced local assets
```

The common loop is:

```bash
provelog export -o my-evidence.zip
provelog verify my-evidence.zip
```

The ZIP contains the bundle, the anchor receipts when present, the pinned TSA
roots, `VERIFY.txt`, and a single-file `verify-offline.html` that works with no
network and no installation. The browser verifier is also available at
<https://provelog.ai/verify>.

`provelog export --bundle my-run.json` writes the raw bundle JSON for
scripts/debugging instead of the ZIP.

## What the evidence proves

With a trusted timestamp and a passing verification, v1 proves:

- the recorded bytes existed before the timestamp;
- a holder of the device key signed the commitment;
- the records were not altered after signing.

It does **not** prove:

- that a specific model generated the content;
- that the model/provider fields are true;
- that the user was at the keyboard;
- that nothing outside the captured source happened;
- that the local machine was free of root/malware.

The verifier reports four axes — integrity, time, attribution, coverage — and
will say `RESULT: INVALID` when no recognizable timestamp is found. It never
turns a missing timestamp into a green claim.

## Privacy and security

- Full content stays local; it is never uploaded.
- Only the Merkle root, count, device public key/signature and protocol
  metadata reach the relay.
- Host auth headers, cookies and API keys are not stored in records.
- Export writes your records as they are: it does not scan, redact or
  modify them. Review a pack before you share it.
- The device key (`~/.provelog/device.key`, or beside a project config; the
  legacy `~/.provelog.key` is also read) is the proof of attribution. Back it
  up separately from the record store. Losing it does not invalidate old
  evidence, but future records sign under a new identity.

## Internal / operator

The timestamp relay is part of the product infrastructure, not a user-facing
self-host feature:

```bash
python -m provelog.server --host 127.0.0.1 --port 8080 --db provelog.db
```

It aggregates claims into RFC 3161 epochs (Sectigo primary, FreeTSA fallback)
and never receives record content. Operator runbooks are internal and ship with
the relay deployment, not the public package.

## Documentation

User documentation is at <https://provelog.ai/doc>. The wire format and the
verification rules are specified in
[docs/SPEC.md](https://github.com/provelog/provelog/blob/main/cli/docs/SPEC.md).

Contact: <contact@provelog.ai>.

## License

Open-core, dual-licensed:

- **MIT** for the client, verifier, bundle format, specification and web
  verifier (`LICENSE`);
- **AGPL-3.0-or-later** for the relay (`src/provelog/server/`), see
  `LICENSE-AGPL`.

The recorder is derived from the recording half of
[pofky/provenrail](https://github.com/pofky/provenrail); upstream's MIT/AGPL
split and attribution are retained.
