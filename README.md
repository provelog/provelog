# ProveLog public source

This repository contains the minimum public source needed to inspect and build
ProveLog's client, verifier, and relay:

```text
cli/src/        Python client, importer, verifier, relay
cli/web/        static verifier assets
cli/docs/       public product, format, CLI, and licensing docs
LICENSE         MIT for client/verifier/spec
LICENSE-AGPL    AGPL-3.0-or-later for the relay
NOTICE.md      licensing and upstream provenance
THIRD_PARTY_NOTICES.md  bundled JS and trademark notices
```

## Build from source

```bash
cd cli
python -m pip install .
provelog --version
provelog --help
```

The published package is also available as `pip install provelog`.

## License boundary

- MIT: client, verifier, evidence format and specification.
- AGPL-3.0-or-later: `provelog/server/` relay.
- The bundled `cli/web/` verifier assets are part of the MIT-licensed client.

Licensing and provenance: [`NOTICE.md`](NOTICE.md).
Third-party notices: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

The recorder is derived from the recording half of
[pofky/provenrail](https://github.com/pofky/provenrail); upstream attribution
and the MIT/AGPL split are retained.

## What is intentionally not public here

This is not the full development workspace. The public GitHub repository does
not include:

- website source or built website (operator-controlled);
- tests and test fixtures;
- local development tools and fake APIs;
- deployment templates or operator runbooks;
- release binaries;
- demo evidence bundles;
- internal status, handoff, launch, audit, or decision logs;
- local paths, credentials, IP allowlists, or private keys.

The package and evidence format remain verifiable without those materials.
