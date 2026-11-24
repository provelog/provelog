"""Client-side anchor submission for the log collector process.

A claim is tiny: one Merkle root over a stream's receipt hashes, a count, and a
device signature. The service folds every claim it receives into a single
RFC 3161 stamp per 60 s epoch, so the number of TSA calls grows with time, not
with the number of conversations or users - which is what makes stamping
free to offer.

Rules, all of them deliberate:

* **One claim per stream per minute.** A newer claim supersedes an older one
  (``covers_up_to`` only grows), so more often than that buys nothing and
  spends the service's per-IP budget, of which a busy machine with subagents
  already has several streams.
* **Only when the stream grew.** A quiet conversation is not re-pushed.
* **Never break collection.** Every failure here is silent: the records are
  already on disk and signed, and the next pass retries. Stamping is the only
  network dependency in the product, so it must never be able to stop it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .netutil import client_for

_log = logging.getLogger("provelog.anchoring")


def _tag(value: Any) -> str:
    """Short, non-reversible tag so a log line names a stream without naming it."""
    return hashlib.sha256(str(value or "").encode()).hexdigest()[:8]

                                                                              
                                                        
ANCHOR_EVERY_S = 60.0
ANCHOR_TIMEOUT_S = 4.0
                                                                                
                                                                                 
ANCHOR_BACKOFF_S = 300.0
                                                                              
                                                                   
ANCHOR_CONFLICT_BACKOFF_S = 600.0
                                                                           
                                                                            
                                                                             
                                                                         
PENDING_TTL_S = 600.0
                                                                                
                                                                        
ORPHAN_GRACE_S = 60.0

                                                                               
                                                           
_last_attempt: dict[str, float] = {}
_down_until: float = 0.0
_last_429: float = 0.0
                                                                             
                                         
_conflict_until: dict[str, float] = {}
_upgrade_required: dict[str, Any] | None = None
_upgrade_noticed = False
                                                                              
                                                                           
                                                                           
                                                                        
_pickup_at: dict[str, float] = {}


def anchor_error_path() -> Path:
    return Path.home() / ".provelog" / "anchor-errors.json"


def last_anchor_error() -> dict[str, Any] | None:
    """The last 409 the relay returned, or None. Never raises on a bad file."""
    try:
        data = json.loads(anchor_error_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _record_anchor_error(stream_id: str, detail: str) -> None:
    data = {"at": time.time(), "stream_id": stream_id, "status": 409,
            "detail": detail}
    path = anchor_error_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _clear_anchor_error(stream_id: str) -> None:
    """A successful claim clears only its own last error."""
    data = last_anchor_error()
    if data is not None and str(data.get("stream_id") or "") not in ("", stream_id):
        return
    try:
        anchor_error_path().unlink(missing_ok=True)
    except OSError:
        pass


def _response_detail(resp: Any) -> str:
    try:
        body = resp.json()
    except Exception:
        body = None
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("message")
        if detail:
            return str(detail)
    return str(getattr(resp, "text", "") or "").strip()[:500]


def _aliases_path() -> Path:
    return Path.home() / ".provelog" / "stream-aliases.json"


def _aliases() -> dict[str, str]:
    try:
        data = json.loads(_aliases_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v}


def _alias_for(stream_id: str) -> str | None:
    return _aliases().get(stream_id)


def _set_alias(local: str, relay: str) -> bool:
    rows = _aliases()
    rows[local] = relay
    path = _aliases_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def _new_alias(local: str) -> str:
    return f"{local}-{secrets.token_hex(3)}"


def _ensure_alias(local: str) -> str:
    """Every new stream gets a random relay alias.

    Relay only ever sees the alias; the local store, export and verification
    keep using the original local stream id. If the alias file cannot be
    written, fall back to the local id rather than creating a new stream on
    every pass.
    """
    existing = _alias_for(local)
    if existing:
        return existing
    candidate = _new_alias(local)
    if _set_alias(local, candidate):
        return candidate
    _log.warning("could not persist stream alias for %s; using local id", _tag(local))
    return local


def _fresh_local_stream(store: Any, stream_id: str) -> bool:
    """True when this local stream has never held an anchor.

    Only a fresh local chain may open a new relay stream on 409. If this store
    already has anchors, a 409 means the relay and this device disagree about a
    history we are keeping; silently forking it would hide that.
    """
    try:
        if hasattr(store, "anchors_for_stream") and store.anchors_for_stream(stream_id):
            return False
        if hasattr(store, "pending_anchor") and store.pending_anchor(stream_id):
            return False
    except Exception:
        return False
    return True


def upgrade_info() -> dict[str, Any] | None:
    """The relay's last 426 response, if this process received one."""
    return _upgrade_required


def _journal_path(store: Any) -> Path | None:
    root = getattr(store, "root", None)
    return None if root is None else Path(root) / ".internal" / "pending_anchors.json"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _journal(store: Any) -> dict[str, dict[str, Any]]:
    """{stream_id: {anchor_id, created_at}} for claims whose receipt is queued.

    A file, not a scan: with thousands of conversations, "ask every stream whether
    it has a pending receipt" is O(n^2) per pass (98 ms per call at 3,000 folders,
    measured), while this is one small read.

    Entries carry their own creation time so an orphaned claim can be aged out
    without the manifest: older builds wrote a bare anchor id, which is read as
    ``created_at: None`` (age unknown) and treated as old.
    """
    path = _journal_path(store)
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            anchor_id = str(value.get("anchor_id") or "")
            created_at = value.get("created_at")
        else:
            anchor_id, created_at = str(value or ""), None
        if anchor_id:
            rows[str(key)] = {"anchor_id": anchor_id, "created_at": created_at}
    return rows


def _journal_set(store: Any, stream_id: str, anchor_id: str | None,
                 created_at: str | None = None) -> None:
    path = _journal_path(store)
    if path is None:
        return
    rows = _journal(store)
    if anchor_id:
        rows[stream_id] = {"anchor_id": str(anchor_id),
                           "created_at": created_at or _now_iso()}
    else:
        rows.pop(stream_id, None)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    except OSError:
        pass


def pickup_pending(store: Any, *, url: str | None = None,
                   limit: int = 50, timeout: float = ANCHOR_TIMEOUT_S,
                   now: float | None = None) -> int:
    """Fetch receipts for claims recorded on this device that are queued. Bounded per pass.

    A 404 means the epoch has not minted the receipt yet (normal right after a
    claim): the journal pointer is KEPT and re-polled at most once per epoch, so
    a quiet session still receives its receipt promptly. The pointer is dropped
    only when the receipt is delivered, the relay says it is gone (410), or the
    claim outlives the TTL - the TTL path in ``maybe_anchor`` then fails and
    re-claims it.
    """
    rows = _journal(store)
    if not rows:
        return 0
    base = (url or endpoint()).rstrip("/")
    resolved = 0
    fetched = 0
    stamp = now if now is not None else time.time()
    for stream_id, entry in list(rows.items()):
        anchor_id = str(entry.get("anchor_id") or "")
        if fetched >= limit:
            break
        if not anchor_id:
            _journal_set(store, stream_id, None)
            continue
        if _pickup_at.get(stream_id, 0.0) > stamp:
            continue                                                         
        try:
            with client_for(base, timeout=timeout) as client:
                resp = client.get(f"{base}/v1/anchors/{anchor_id}")
        except Exception:
            return resolved
        fetched += 1
        if resp.status_code == 200:
            try:
                store.resolve_pending_anchor(stream_id, resp.json())
            except Exception:
                continue
            _journal_set(store, stream_id, None)
            _pickup_at.pop(stream_id, None)
            _clear_anchor_error(stream_id)
            resolved += 1
        elif resp.status_code == 410:
                                                                            
                                                                             
                                                                   
            _journal_set(store, stream_id, None)
            _pickup_at.pop(stream_id, None)
        else:
            pending = (store.pending_anchor(stream_id)
                       if hasattr(store, "pending_anchor") else None)
            if pending is None:
                                                                             
                                                                              
                                                                               
                                                                       
                journal_age = _age_seconds(entry.get("created_at"))
                if journal_age < ORPHAN_GRACE_S:
                    _pickup_at[stream_id] = stamp + ANCHOR_EVERY_S
                    continue
                if hasattr(store, "fail_pending_anchor"):
                    store.fail_pending_anchor(
                        stream_id, anchor_id,
                        "receipt no longer served; journal entry orphaned")
                _journal_set(store, stream_id, None)
                _pickup_at.pop(stream_id, None)
                _log.info("anchor receipt for %s is orphaned; stopped polling %s",
                          _tag(stream_id), _tag(anchor_id))
                continue
            age = _age_seconds(pending.get("created_at"))
            if age == float("inf"):
                                                                                
                                                                                 
                age = _age_seconds(entry.get("created_at"))
            if age >= PENDING_TTL_S:
                                                                           
                                                                            
                _journal_set(store, stream_id, None)
                _pickup_at.pop(stream_id, None)
            else:
                _pickup_at[stream_id] = stamp + ANCHOR_EVERY_S
    return resolved


def endpoint(config: dict[str, Any] | None = None) -> str:
    """Where claims go: the configured endpoint, else the hosted default."""
    if config and config.get("anchor_endpoint"):
        return str(config["anchor_endpoint"]).rstrip("/")
    from .easy import _resolve
    return str(_resolve().get("anchor_endpoint") or "https://provelog.ai").rstrip("/")


def rate_limited_recently(now: float | None = None, window: float = 180.0) -> bool:
    """Did the service push back in the last few minutes?"""
    stamp = now if now is not None else time.time()
    return bool(_last_429) and (stamp - _last_429) < window


def _age_seconds(stamp: Any) -> float:
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return float("inf")
    return (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()


def pickup(store: Any, stream_id: str, *, url: str | None = None,
           timeout: float = ANCHOR_TIMEOUT_S) -> bool:
    """Fetch a receipt the service has finished minting. Silent on failure."""
    if not hasattr(store, "pending_anchor"):
        return False
    pending = store.pending_anchor(stream_id)
    if not pending or not pending.get("anchor_id"):
        return False
    import httpx

    target = url or endpoint()
    try:
        with client_for(target, timeout=timeout) as client:
            resp = client.get(f"{target}/v1/anchors/{pending['anchor_id']}")
    except httpx.HTTPError:
        return False
    if resp.status_code != 200:
                                                                             
                                                                               
                                                                          
        return False
    _clear_anchor_error(stream_id)
    try:
        body = resp.json()
    except ValueError:
        return False
    store.resolve_pending_anchor(stream_id, body)
    return True


def maybe_anchor(store: Any, stream_id: str, *, url: str | None = None,
                 key: Any | None = None, now: float | None = None,
                 timeout: float = ANCHOR_TIMEOUT_S,
                 force: bool = False, lane: str = "live") -> dict[str, Any] | None:
    """Push one claim for this stream if it grew; pick up a finished receipt first.

    A 409 from the relay means its history for this stream disagrees with ours.
    The local chain is never dropped: we record the refusal for status/doctor,
    back off, and -- only for a fresh local stream (the reinstall case) -- open
    a new relay stream and retry once. A store that already has anchors keeps
    its history; a conflict there is surfaced, not silently forked.
    """
    if not hasattr(store, "anchor_leaves"):
        return None
    global _down_until, _upgrade_required, _upgrade_noticed

    stamp_now = now if now is not None else time.time()
    if _upgrade_required is not None:
        return None
    if stamp_now < _down_until:
        return None
    if stamp_now < _conflict_until.get(stream_id, 0.0):
        return None
    url = (url or endpoint()).rstrip("/")

    pickup(store, stream_id, url=url, timeout=timeout)

    leaves = store.anchor_leaves(stream_id)
    if not leaves:
        return None
    anchors = store.anchors_for_stream(stream_id) if hasattr(store, "anchors_for_stream") else []
                                                                               
                                                                         
    delivered = [a for a in anchors if not a.get("pending") and not a.get("failed")]
    covered = max((int(a.get("covers_up_to") or 0) for a in delivered), default=0)
    if len(leaves) <= covered:
        return None
                                                                               
                                                                              
                                                                               
                                                                       
    pending = store.pending_anchor(stream_id) if hasattr(store, "pending_anchor") else None
    if pending:
        stale = _age_seconds(pending.get("created_at")) >= PENDING_TTL_S
        if stale:
            if hasattr(store, "fail_pending_anchor"):
                store.fail_pending_anchor(stream_id, pending["anchor_id"],
                                          "no receipt within TTL; re-claiming")
        elif len(leaves) <= int(pending.get("covers_up_to") or 0):
            return None
    if not force:
        last = _last_attempt.get(stream_id, 0.0)
        if stamp_now - last < ANCHOR_EVERY_S:
            return None
        if delivered and _age_seconds(delivered[-1].get("created_at")) < ANCHOR_EVERY_S:
            return None
    _last_attempt[stream_id] = stamp_now

    import httpx

    from .anchor import merkle_root
    from .easy import _device_key

    root = merkle_root(leaves)
    signing_key = key or _device_key()
    covers = len(leaves)
    local_stream = stream_id
    fresh = _fresh_local_stream(store, local_stream)
    relay_stream = _ensure_alias(local_stream)

    def payload_for(relay_stream: str) -> dict[str, Any]:
        return {
            "stream_id": relay_stream,
            "merkle_root": root,
            "covers_up_to": covers,
            "device_pubkey": signing_key.public_key_hex(),
            "device_sig": signing_key.sign(
                f"{relay_stream}||{root}||{covers}||{lane}".encode()),
            "lane": lane,
        }

    def send(relay_stream: str):
        with client_for(url, timeout=timeout) as client:
            return client.post(f"{url}/v1/anchors", json=payload_for(relay_stream),
                               headers={"X-ProveLog-Version": __version__})

    from . import __version__

    try:
        resp = send(relay_stream)
    except httpx.HTTPError:
        _down_until = stamp_now + ANCHOR_BACKOFF_S
        return None

    if resp.status_code == 409:
        detail = _response_detail(resp)
        _record_anchor_error(local_stream, detail)
        if fresh:
            new_stream = _new_alias(local_stream)
            if not _set_alias(local_stream, new_stream):
                _conflict_until[local_stream] = stamp_now + ANCHOR_CONFLICT_BACKOFF_S
                return None
            relay_stream = new_stream
            try:
                resp = send(relay_stream)
            except httpx.HTTPError:
                _down_until = stamp_now + ANCHOR_BACKOFF_S
                return None
            if resp.status_code == 409:
                _record_anchor_error(local_stream, _response_detail(resp))
                _conflict_until[local_stream] = stamp_now + ANCHOR_CONFLICT_BACKOFF_S
                _log.warning("anchor conflict for %s on a new stream; backing off",
                             _tag(local_stream))
                return None
        else:
            _conflict_until[local_stream] = stamp_now + ANCHOR_CONFLICT_BACKOFF_S
            _log.warning("anchor conflict for %s; backing off (%s)",
                         _tag(local_stream), detail[:160])
            return None

    if resp.status_code == 426:
                                                                             
                                                                                
        try:
            body = resp.json()
        except ValueError:
            body = {}
        _upgrade_required = body if isinstance(body, dict) else {}
        if not _upgrade_noticed:
            from .version import DOWNLOAD_URL
            url_hint = str(_upgrade_required.get("url") or DOWNLOAD_URL)
            print("ProveLog: this version is no longer supported by the stamping "
                  f"service. Update: {url_hint}", file=__import__("sys").stderr)
            _upgrade_noticed = True
        return None
    if resp.status_code == 429:
                                                                              
                                                                          
        global _last_429
        _last_429 = stamp_now
        return None
    if resp.status_code >= 500:
        _down_until = stamp_now + ANCHOR_BACKOFF_S
        return None
    if resp.status_code != 200:
        return None
    try:
        out = resp.json()
    except ValueError:
        return None
    if not isinstance(out, dict):
        return None
    _clear_anchor_error(local_stream)
    if out.get("status") == "queued":
                                                                              
                                                         
        store.add_anchor(local_stream, out, covers_up_to=covers,
                         anchor_id=out.get("anchor_id"), pending=True)
        _journal_set(store, local_stream, out.get("anchor_id"),
                     created_at=str(out.get("created_at") or "") or None)
        _pickup_at.pop(local_stream, None)                                           
        return out
    store.add_anchor(local_stream, out, covers_up_to=covers,
                     anchor_id=out.get("anchor_id"))
    return out
