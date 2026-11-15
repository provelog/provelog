# ProveLog public source

This repository contains the minimum public source needed to inspect and build
ProveLog's client, verifier, and relay:

```text
cli/src/        Python client, importer, verifier, relay
cli/web/        static verifier assets
cli/docs/       SPEC.md: wire format and verification protocol
vectors/v1/     frozen conformance vectors
LICENSE         MIT for client/verifier/format
LICENSE-AGPL    AGPL-3.0-or-later for the relay
NOTICE.md      licensing and upstream provenance
THIRD_PARTY_NOTICES.md  bundled JS and trademark notices
```

User documentation lives at <https://provelog.ai/doc>. The wire format and the
verification rules are in [`cli/docs/SPEC.md`](cli/docs/SPEC.md); apart from that
this repository carries source, verifier assets and frozen vectors only.

Contact: <contact@provelog.ai>.

## Build from source

```bash
cd cli
python -m pip install .
provelog --version
provelog --help
```

The published package is also available as `pip install provelog`.

## License boundary

- MIT: client, verifier and evidence format.
- AGPL-3.0-or-later: `provelog/server/` relay.
- The bundled `cli/web/` verifier assets are part of the MIT-licensed client.

Licensing and provenance: [`NOTICE.md`](NOTICE.md).
Third-party notices: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

The recorder is derived from the recording half of
[pofky/provenrail](https://github.com/pofky/provenrail); upstream attribution
and the MIT/AGPL split are retained.
