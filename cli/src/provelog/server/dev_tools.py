"""The hosted stamping service command.

It lives on the server side of the tree and registers into the CLI when
fastapi is importable (it is part of the normal install).

  - python -m provelog.server  the operator entry point for the hosted stamping service.
           The chain is fixed Sectigo -> FreeTSA, aggregate always on;
           `--anchor local` is development-only and self-asserted.

Recording, verification and anchoring need no local service: that is why
`serve` is not on the core CLI.
"""

from __future__ import annotations

import sys

from ..cli import _require_server_extra
from ..strings import t


def _build_anchor(args):
    """The server's TSA chain is fixed: Sectigo primary -> FreeTSA fallback.
    `--anchor local` stays available for development/testing only."""
    from ..anchor import FailoverTSAAnchor, LocalAnchor

    if args.anchor == "local":
        return LocalAnchor(), "local (development only)"
    return FailoverTSAAnchor(), "rfc3161 (Sectigo -> FreeTSA failover)"


def _warn_serve_defaults(args) -> None:
    """Loud, non-fatal warning for the one remaining dev-only mode."""
    if args.anchor == "local":
        print("WARNING: --anchor local is self-asserted time, not third-party "
              "time. Use it only for development/testing.", file=sys.stderr)
        sys.stderr.flush()


def _cmd_serve(args) -> int:
    _require_server_extra("serve")
    import uvicorn

    from .app import create_app

    anchor, anchor_desc = _build_anchor(args)
    _warn_serve_defaults(args)
    app = create_app(args.db, anchor=anchor,
                     external_anchor_days=args.external_anchor_retention_days,
                     aggregate_anchors=True,
                     aggregate_interval=args.aggregate_interval,
                     minimum_client=getattr(args, "minimum_client", ""),
                     trusted_proxies=getattr(args, "trusted_proxies", "127.0.0.1,::1"))
    import provelog.server.app as appmod
    appmod.app = app
    base = f"http://{args.host}:{args.port}"
    print(t("serve.banner_title", base=base))
    print(t("serve.banner_roots"))
    print(t("serve.banner_verifier", base=base))
    print(t("serve.banner_dev"))
                                                                                     
                                                                                         
    sys.stdout.flush()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Operator entry point (`python -m provelog.server`); not a user subcommand."""
    import argparse

    p = argparse.ArgumentParser(prog="python -m provelog.server",
                                description="ProveLog relay: roots in, RFC 3161 receipts out")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--db", default="provelog.db")
    p.add_argument("--anchor", choices=["local", "rfc3161"], default="rfc3161",
                   help="rfc3161 (default): Sectigo -> FreeTSA; local: development only")
    p.add_argument("--aggregate-interval", type=float, default=60.0)
    p.add_argument("--external-anchor-retention-days", type=int, default=7)
    p.add_argument("--minimum-client", default="",
                   help="minimum client version accepted for stamping (default: any)")
    p.add_argument("--trusted-proxies", default="127.0.0.1,::1",
                   help="comma-separated reverse-proxy peer IPs whose X-Real-IP/"
                        "X-Forwarded-For headers are trusted (default: loopback)")
    args = p.parse_args(argv)
    return _cmd_serve(args)
