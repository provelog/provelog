"""ProveLog stamping service: receive a root, stamp it, deliver the receipt.

The cloud end of the anchoring-only contract. It never receives a record:
the schema of POST /v1/anchors is the privacy guarantee (there is no field a
record could travel in), and the storage layer beneath it has no table one
could land in. There is no account layer: a push authenticates with a
device signature over the claim itself - verified mathematically, then
discarded, nothing persisted.

Design constraints enforced here:
  - No DELETE or UPDATE route exists for hosted anchors (append-only by construction).
  - An anchor row dies with its delivery: the receipt rides the response, the
    hosted copy is garbage the moment it is handed over.
"""

from __future__ import annotations

import collections
import hashlib
import logging
import pathlib

from fastapi import BackgroundTasks, FastAPI, HTTPException, Path, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .. import __version__ as _provelog_version
from .. import anchor as anchor_mod
from ..anchor import Anchor, LocalAnchor
from . import security as sec
from . import storage as storage_mod
from .abuse import FloodGuard
from .pressure import UNDER_ATTACK, PressureGuard
from .risk import LANES
from .scheduler import (
    AGG_BATCH_MAX_ENTRIES,
    AggregateBatch,
    AggregatingAnchorScheduler,
    AnchorScheduler,
    BatchFull,
    LaneClosed,
    RetentionScheduler,
)

                                                                               
MAX_ANCHOR_GUARDS = 100_000

_log_limit = logging.getLogger("provelog.anchor-limit")
_log_miss = logging.getLogger("provelog.anchor-miss")


def _tag(value: str) -> str:
    """Short, non-reversible tag: the log names a stream/IP without naming it."""
    return hashlib.sha256(str(value or "").encode()).hexdigest()[:8]


                                                                             
                                                                              
                                    
_MISS_WINDOW_S = 60.0
_miss_last = 0.0
_miss_count = 0


def _note_anchor_miss(anchor_id: str) -> None:
    global _miss_last, _miss_count
    import time

    _miss_count += 1
    now = time.time()
    if now - _miss_last < _MISS_WINDOW_S:
        return
    _miss_last = now
    count, _miss_count = _miss_count, 0
    _log_miss.warning("404 unknown anchor %s (%s miss(es) in the last window)",
                      _tag(anchor_id), count)


class CreateAccountIn(BaseModel):
    label: str | None = None


class AnchorRootIn(BaseModel):
    """One anchor from a customer-held sink.

    Note what is absent: there is no field for a record, a prompt, an output, or a name. The
    schema is the privacy guarantee. A customer keeps their records locally and sends the root
    of their own hash tree, so this endpoint cannot receive personal data even if a caller
    wanted to send it."""
    stream_id: str
    merkle_root: str
                                                                                            
                                                                                           
                                                                         
    covers_up_to: int = Field(ge=1, le=2**53)
                                                                            
                                                                                  
                                                                            
                                                                             
                                                         
    device_pubkey: str | None = None
    device_sig: str | None = None
                                                                          
                                             
    lane: str = "live"


                                                                        
                                                                              
                                     
_SECURITY_HEADERS = (
    ("X-Frame-Options", "DENY"),                                             
    ("Referrer-Policy", "no-referrer"),                                 
    ("X-Content-Type-Options", "nosniff"),                                     
)


def read_web_asset(name: str) -> str | None:
    """A static asset from the repo's web/ directory (verify.js, the
    conformance page, ...) as text, or None when this deployment carries
    neither copy.

    Package copy FIRST, repository layout second: a pip-installed
    provelog ships the files inside the package (provelog/web/, placed
    there by the build - see setup.py), and that copy wins wherever the
    process runs from; the repo-root layout three parents above this
    file is the fallback that keeps a source checkout (`pip install -e`,
    running from the tree) working. Reading only - the verification
    code inside the files is not touched.
    """
    import importlib.resources

    try:
        res = importlib.resources.files("provelog").joinpath("web", name)
        if res.is_file():
            return res.read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError, NotImplementedError):
        pass                                                              
    path = _REPO_WEB_ROOT / name
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


                                                                           
                                                                         
                                                                          
_REPO_WEB_ROOT = pathlib.Path(__file__).resolve().parents[3] / "web"


def _client_address(request: Request) -> str:
    """The visitor's address, not the edge's.

    Direct peer is the truth unless the peer is a configured trusted reverse
    proxy. In that case the edge sets ``X-Real-IP`` (Nginx: ``$remote_addr``);
    fall back to the rightmost ``X-Forwarded-For`` hop. A client-supplied
    ``CF-Connecting-IP`` / ``X-Forwarded-For`` from an untrusted peer is never
    read, so nobody can forge an IP to dodge a limit or frame another user.
    """
    peer = request.client.host if request.client else "unknown"
    trusted = getattr(getattr(request.app, "state", None), "trusted_proxies", None)
    if not trusted or peer not in trusted:
        return peer
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")
    for hop in reversed(forwarded):
        hop = hop.strip()
        if hop:
            return hop
    return peer


def _pending(app: FastAPI) -> int:
    """Pending claims, best effort: 0 when there is no aggregate queue."""
    getter = getattr(getattr(app.state, "scheduler", None), "pending", None)
    try:
        return int(getter()) if callable(getter) else 0
    except Exception:
        return 0


def create_app(
    db_path: str = ":memory:",
    anchor: Anchor | None = None,
                                                                                  
                                                                                  
                                                                               
                                                                                
                                                       
    anchor_per_min: int = 60,
    retention_interval_s: float = 3600.0,
    external_anchor_days: int = 7,
    aggregate_anchors: bool = False,
    aggregate_interval: float = 60.0,
    minimum_client: str = "",
    trusted_proxies: str = "127.0.0.1,::1",
) -> FastAPI:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _app.state.scheduler.start()
        _app.state.retention.start()
        yield
        _app.state.retention.stop()
        _app.state.scheduler.stop()

    app = FastAPI(title="ProveLog", version=_provelog_version, lifespan=lifespan)

                                                                         
                                                                          
                                                                    
    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS:
            if name not in response.headers:
                response.headers[name] = value
        return response

    store = storage_mod.Storage(db_path)
    anchor_backend: Anchor = anchor or LocalAnchor()

                                                                           
                                                                         
                                   
    if aggregate_anchors:
        scheduler = AggregatingAnchorScheduler(
            store, anchor_backend,
            aggregate_interval_s=aggregate_interval,
            batch=AggregateBatch(f"{db_path}.aggbatch" if db_path != ":memory:" else None))
    else:
        scheduler = AnchorScheduler(store, anchor_backend)
    app.state.store = store
    app.state.anchor = anchor_backend
    app.state.minimum_client = minimum_client.strip()
    app.state.trusted_proxies = {p.strip() for p in trusted_proxies.split(",") if p.strip()}
    app.state.scheduler = scheduler
    app.state.retention = RetentionScheduler(store, interval_s=retention_interval_s,
                                          external_anchor_days=external_anchor_days)
                                                                              
    app.state.anchor_guards = collections.OrderedDict()
                                                                            
                                                                          
                                                                     
    app.state.anchor_limiter = sec.RateLimiter(anchor_per_min, 60.0)
                                                                                    
    app.state.pressure = PressureGuard(AGG_BATCH_MAX_ENTRIES)
                                                                                
                                                                               
                                                    
    app.state.attack_ip_limiter = sec.RateLimiter(
        max(2000, AGG_BATCH_MAX_ENTRIES // 20), 60.0)
                                                                              
                                                                              
    app.state.flood = FloodGuard()

    @app.post("/v1/anchors")
    def anchor_root(body: AnchorRootIn, background: BackgroundTasks,
                    request: Request):
        from dataclasses import asdict

        from ..chain import _utc_now_iso
        from ..version import DOWNLOAD_URL, is_older, version_tuple

        minimum = app.state.minimum_client
        if minimum:
            got = request.headers.get("x-provelog-version", "").strip()
            if not got or version_tuple(got) is None or is_older(got, minimum):
                return JSONResponse(
                    status_code=426,
                    content={
                        "error": "upgrade_required",
                        "minimum_client": minimum,
                        "url": DOWNLOAD_URL,
                        "message": "This client version is no longer supported "
                                   "for stamping.",
                    },
                )

        def _discard_after_delivery(anchor_id: str) -> None:
                                                                           
                                                                               
                                                                             
                                                                               
                                                                
            try:
                if not store.delete_external_anchor(anchor_id):
                    _log_external.debug("anchor %s already gone (retry path)", anchor_id)
            except Exception as e:  # noqa: BLE001 - delivery succeeded; cleanup must not fail it
                _log_external.warning("could not discard delivered anchor row %s (%s); "
                                      "the 7-day TTL sweep will remove it", anchor_id, e)

        _log_external = logging.getLogger("provelog.anchor-delivery")

        stream_id = (body.stream_id or "").strip()
                                                     
                                                                      
                                                                           
                                                                           
                                                                        
                                                                      
                                    
        if not (body.device_pubkey and body.device_sig):
            raise HTTPException(401, "device_pubkey and device_sig are required: the "
                                    "recording key's signature over the claim is the "
                                    "authentication")
        address = _client_address(request)
        app.state.flood.record(address)
        if app.state.flood.is_blocked(address):
            _log_limit.warning("429 address blocked ip=%s", _tag(address))
            raise HTTPException(429, "this address is blocked for abusive volume; retry later",
                                headers={"Retry-After": "600"})
        from ..keys import verify_signature
        signed = (f"{stream_id}||{body.merkle_root}||{body.covers_up_to}"
                  f"||{body.lane}").encode()
        legacy = f"{stream_id}||{body.merkle_root}||{body.covers_up_to}".encode()
                                                                           
                                                                
        if not verify_signature(body.device_pubkey, signed, body.device_sig):
            if not (body.lane == "live"
                    and verify_signature(body.device_pubkey, legacy, body.device_sig)):
                raise HTTPException(401, "device signature does not match the "
                                        "supplied public key, root, and lane")
        if not stream_id or len(stream_id) > 200:
            raise HTTPException(422, "stream_id must be a label of 1 to 200 characters")
        if body.covers_up_to < 1:
            raise HTTPException(422, "covers_up_to is how many records the root spans, so it "
                                     "must be at least 1")
                                                                              
                                                                                
                                                                          
        if body.lane not in LANES:
            raise HTTPException(422, f"lane must be one of {', '.join(LANES)}")
        if not app.state.anchor_limiter.allow(stream_id):
            _log_limit.warning("429 stream rate limit stream=%s ip=%s",
                               _tag(stream_id), _tag(address))
            raise HTTPException(429, "anchor rate limit exceeded for this stream")
                                                                                  
                                                                                   
                                   
        pending = _pending(app)
        if app.state.pressure.observe(pending) == UNDER_ATTACK:
            if app.state.pressure.ratio(pending) >= app.state.pressure.panic_at:
                for blocked in app.state.flood.consider():
                    logging.getLogger("provelog.anchor-guard").warning(
                        "blocking %s for abusive volume", blocked)
            if not app.state.attack_ip_limiter.allow(address):
                raise HTTPException(429, "the service is under load; retry shortly")
        try:
            root = anchor_mod._checked_root(body.merkle_root)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

                                                                                                 
                                                                                                 
                                                                                       
                                                                                
                                                                            
                                                                              
                                                                               
                                                                                
        _guards = app.state.anchor_guards
        gk = stream_id
        existing = store.newest_external_anchor(stream_id)
        guarded = existing is None and gk in _guards
        if guarded:
            existing = {"covers_up_to": _guards[gk][0], "merkle_root": _guards[gk][1]}
            _guards.move_to_end(gk)
        if existing is not None:
            if body.covers_up_to < existing["covers_up_to"]:
                raise HTTPException(409, storage_mod.coverage_went_backwards_message(
                    existing["covers_up_to"], body.covers_up_to))
            if body.covers_up_to == existing["covers_up_to"]:
                if existing["merkle_root"] != root:
                    raise HTTPException(409, storage_mod.two_histories_message(body.covers_up_to))
                                                                             
                                                                               
                                                                            
                                                                             
                                                                               
                                                                      
                if not guarded:
                    background.add_task(_discard_after_delivery, existing["anchor_id"])
                    return existing

        _guards[gk] = (body.covers_up_to, root)
        _guards.move_to_end(gk)
                                                                           
                                                                     
                                                                       
                                                    
        while len(_guards) > MAX_ANCHOR_GUARDS:
            _guards.popitem(last=False)

                                                                            
                                                                           
                                                                         
        if aggregate_anchors:
            anchor_id = sec.new_anchor_id()
            try:
                app.state.scheduler.enqueue_push(
                    anchor_id=anchor_id, stream_id=stream_id,
                    covers_up_to=body.covers_up_to, merkle_root=root,
                    lane=body.lane)
            except BatchFull:
                raise HTTPException(503, "stamping queue is full; retry in a minute",
                                    headers={"Retry-After": "60"}) from None
            except LaneClosed as exc:
                raise HTTPException(429, str(exc),
                                    headers={"Retry-After": "60"}) from None
            return {"status": "queued", "anchor_id": anchor_id,
                    "stream_id": stream_id, "covers_up_to": body.covers_up_to,
                    "note": f"aggregating; the receipt is minted at the next "
                            f"epoch (every {aggregate_interval:.0f}s) and reads "
                            f"back at GET /v1/anchors/{anchor_id}"}

        backend = app.state.scheduler.anchor
        receipt = backend.anchor_root(root)                                                
        try:
            stored = store.append_external_anchor(
                anchor_id=sec.new_anchor_id(),
                stream_id=stream_id,
                merkle_root=root,
                covers_up_to=body.covers_up_to,
                receipt=asdict(receipt),
                created_at=_utc_now_iso(),
            )
        except storage_mod.DuplicateAnchor as e:
                                                                                                 
            winner = store.get_external_anchor(e.anchor_id)
            background.add_task(_discard_after_delivery, e.anchor_id)
            return winner
        except storage_mod.CoverageWentBackwards as e:
                                                                                              
                                                                     
            raise HTTPException(409, str(e)) from None
        background.add_task(_discard_after_delivery, stored["anchor_id"])
        return stored

    @app.get("/v1/anchors/{anchor_id}")
    def get_anchor(anchor_id: str = Path(...), background: BackgroundTasks = None):
        """Deliver one queued aggregate receipt. JSON only: the evidence pack is
        the user-facing artifact, and this endpoint exists for receipt pickup."""
        found = store.get_external_anchor(anchor_id)
        if found is None:
                                                                              
                                                                                
                                                                                 
                                                                      
            if hasattr(store, "anchor_tombstone") and store.anchor_tombstone(anchor_id):
                raise HTTPException(410, "this receipt is gone: it was already delivered "
                                        "or passed its retention window")
            _note_anchor_miss(anchor_id)
            raise HTTPException(404, "no such anchor")
                                                                              
                                                                          
        if background is not None:
            def _discard():
                try:
                    store.delete_external_anchor(anchor_id)
                except Exception:   # noqa: BLE001 - delivery succeeded; cleanup must not fail it
                    logging.getLogger("provelog.anchor-delivery").warning(
                        "could not discard delivered anchor row %s", anchor_id)
            background.add_task(_discard)
        return found

    @app.get("/")
    def root():
        """The base URL goes to the hosted verifier: it is the one page a
        stranger following an anchor link may need, and the stamping service
        has nothing else to browse."""
        return RedirectResponse(url="/verify", status_code=307)

    @app.get("/verify", response_class=HTMLResponse)
    @app.get("/verify.html", response_class=HTMLResponse)
    def verify_page():
        from .verify_page import VERIFY_HTML
        return HTMLResponse(VERIFY_HTML)

    @app.get("/verify.js")
    def verify_js():
        """Serve the standalone JS verifier so the hosted page can verify fully client-side,
        trusting not even this server. Verification is 100% in the browser; when this file
        is absent the page says so and points at the CLI instead - the server never
        receives a user's bundle."""
        data = read_web_asset("verify.js")
        if data is None:
            raise HTTPException(404, "client verifier not bundled in this deployment")
        return Response(data,
                        media_type="text/javascript",
                        headers={"Cache-Control": "public, max-age=300"})

    @app.get("/rfc3161.js")
    def rfc3161_js():
        """Pinned-root RFC 3161/CMS verifier used by verify.js (offline)."""
        data = read_web_asset("rfc3161.js")
        if data is None:
            raise HTTPException(404, "RFC 3161 verifier not bundled in this deployment")
        return Response(data,
                        media_type="text/javascript",
                        headers={"Cache-Control": "public, max-age=300"})

    @app.get("/v1/meta")
    def meta():
        from .. import __version__
                                                                                              
                                                                                          
                                                                                           
        return {"version": __version__}

    @app.get("/healthz")
    def healthz():
                                                                              
                                                                              
                                                                         
                                 
        out = {"ok": True, "state": app.state.pressure.state,
               "pending": _pending(app), "capacity": app.state.pressure.capacity,
               "blocked": app.state.flood.blocked_count()}
        snap = getattr(app.state.scheduler, "lane_snapshot", None)
        if callable(snap):
            try:
                out.update(snap())
            except Exception:
                pass
        stats = getattr(app.state.anchor, "stats", None)
        if callable(stats):
            try:
                out["anchor"] = stats()
            except Exception:
                pass
        return out

    return app




def run():
    """Internal operator entry, invoked by `python -m provelog.server`."""
    import sys

    from .dev_tools import main as serve_main

    raise SystemExit(serve_main(sys.argv[1:]))
