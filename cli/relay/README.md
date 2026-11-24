# provelog-relay

Operator-only relay for ProveLog. This package contains only
`provelog.server`; the normal `provelog` wheel no longer includes the relay.

**This package is not published to PyPI.** It is built locally by the operator:

```bash
python build.py
pip install dist/provelog_relay-*.whl
python -m provelog.server --host 127.0.0.1 --port 8080 --db provelog.db
```

The relay is AGPL-3.0-or-later. It is not installed by `pip install provelog`
and is not a user-facing subcommand.

## Build

From this directory:

```bash
python build.py
```

The script stages `../src/provelog/server`, strips first-party comments with
the same publish-clean rules as the public package, builds wheel + sdist into
`dist/`, and removes the staging copy.
