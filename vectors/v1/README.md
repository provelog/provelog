# ProveLog test vectors

Frozen golden bundles used by the Python and JavaScript verifier tests.
They are intentionally independent of the public vectors in
[`../../vectors/v1/`](../../vectors/v1/), which are the external conformance
contract.

Each `<name>.json` is a complete `provelog.bundle/1` (older vectors may still
use the inherited `flightrecorder.bundle/1` tag). `manifest.json` records the
expected verdict for each case.

The tests exercise these files with both `provelog.verifier.verify` and
`web/verify.js`. Do not edit a vector to make a failing verifier pass; a
genuine verification change requires a spec-versioned regeneration and a new
public vector.
