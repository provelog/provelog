"""Capture SDK: the thing an agent installs to record what it does.

Captures at two boundaries:
  - model calls (record_model_call / wrap_openai-style helpers)
  - the tool-dispatch boundary (@recorder.tool, record_tool_call), which catches LOCAL
    tool calls a network proxy never sees. This directly addresses the red-team finding
    that a network chokepoint misses shell/exec/filesystem/local-model activity.

Storage default: the COMPLETE trajectory. Inputs and outputs are stored together with
their SHA-256 fingerprint; there is no hash-only mode. The local sink is therefore a
complete conversation store - disk security equals content security.

Delivery is LOCAL-FIRST and local-only: every record lands in the embedded sink
(the folder-canonical store on this disk, ~/ProveLog by default) and the
receipt chain is computed right there. The one network act left in the product
is anchoring - 32 bytes and a count, signed by the device key - and it happens
in the automatic collector, not here.

Honest limitation: this SDK runs inside the agent process, so a hostile agent can refuse
to call it. It cannot make capture complete. Its job is to seal whatever IS recorded
into a signed, receipt-chained local store immediately, so it becomes immutable.
Completeness is surfaced, never guaranteed.
"""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from . import GENESIS_PREV_HASH
from .canonical import hash_value
from .chain import (
    DATA_ACCESS,
    DECISION,
    HUMAN_OVERSIGHT,
    MODEL_CALL,
    TOOL_CALL,
    Chain,
)
from .keys import SigningKey

_log = logging.getLogger(__name__)


class DeliveryError(RuntimeError):
    """A local store write failed. Kept as the named failure mode for callers."""

    def __init__(self, msg: str, persisted: bool = False):
        super().__init__(msg)
        self.persisted = persisted


class SinkIntegrityError(RuntimeError):
    """The local store returned a receipt inconsistent with the chain it already
    issued (reorder, fork, or rollback). Cannot happen through the folder path
    today - the append computes its own links under one lock - but
    the class stays: it is the named failure mode every receipt consumer
    reasons about, and a future sink backend raises it rather than inventing
    a new one."""


def _float_text(value: Any) -> Any:
    """Floats as text, recursively. Records cannot carry floats (see
    canonical.py); host logs do (a tool argument such as ``scale: 0.5``), and
    an importer must record the session rather than refuse it."""
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _float_text(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_float_text(v) for v in value]
    return value


def _digest(value: Any) -> dict[str, Any]:
    """Return a content descriptor: always a hash, plus the value if small and allowed."""
    try:
        return {"hash": hash_value(value)}
    except Exception:
                                                                                             
        return {"hash": hash_value(repr(value)), "note": "hashed_repr"}


class Provelog:
    def __init__(
        self,
        stream_id: str,
        key: SigningKey | None = None,
        capture_content: bool = True,
        flush: bool = True,
        store: Any | None = None,
        chain: Chain | None = None,
        float_text: bool = False,
    ):
                                                                               
                                                                             
        self._float_text = float_text
        self.key = key or SigningKey.generate()
        self.chain = chain or Chain(stream_id=stream_id, key=self.key)
        self.stream_id = stream_id
                                                                          
                                                                             
                                                                                      
                                                                          
                                                                           
                                                                     
                                                              
        if store is None:
            from .store import open_store
            store = open_store()
        self._local_store = store
        self.capture_content = capture_content
        self._flush = flush
        self._lock = threading.RLock()
        self._hb_stop: threading.Event | None = None
        self._hb_thread: threading.Thread | None = None
        self.head = {"recv_seq": -1, "server_record_hash": GENESIS_PREV_HASH}

                         
    def _push(self, record: dict[str, Any]) -> None:
        if not self._flush:
            return
        self._send([record])

    def _send_with_retry(self, batch: list[dict[str, Any]]) -> None:
        self._send(batch)

    def _send(self, batch: list[dict[str, Any]]) -> None:
                                                                             
                                                                       
                                                                                   
         
                                                                           
                                                                            
                                                                            
                                              
        store = self._local_store
        if hasattr(store, "append_records"):
            receipts = store.append_records(self.stream_id, list(batch))
        else:                                                             
            receipts = [store.append_record(self.stream_id, r) for r in batch]
        for receipt in receipts:
            if receipt.get("duplicate") or receipt.get("skipped"):
                continue
            prev = receipt.get("server_prev_hash")
            with self._lock:
                if (self.head["recv_seq"] == -1
                        and self.head["server_record_hash"] == GENESIS_PREV_HASH
                        and prev is not None):
                    self.head["server_record_hash"] = prev
                self.head = {
                    "recv_seq": receipt.get("recv_seq", self.head["recv_seq"]),
                    "server_record_hash": receipt.get("server_record_hash",
                                                      self.head["server_record_hash"]),
                }

    @property
    def session_id(self) -> str:
        """The current session's id. The chain owns it, and it is reset per session, so read it
        here rather than caching a copy at construction time."""
        return self.chain.session_id

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.stop_heartbeat()

                         
    def start(self, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        """Open the signed session (genesis record). Returns the record, so a
        batch caller can collect it and submit genesis/event/commitment in one POST."""
        with self._lock:
            rec = self.chain.start(meta)
        self._push(rec)
        return rec

    def commit(self, reason: str = "normal") -> dict[str, Any]:
        """Checkpoint the session ("everything up to here is final"). Returns the
        commitment record. Recording may continue afterwards in the same chain."""
        with self._lock:
            rec = self.chain.commit(reason=reason)
        self._push(rec)
        return rec

    @contextmanager
    def session(self, meta: dict[str, Any] | None = None):
                                                                          
                                                                          
                                                                              
                                                  
        from . import active
        token = active.set_active(self)
        try:
            self.start(meta)
            yield self
            self.stop_heartbeat()                                                   
            self.commit(reason="session_exit")
        except BaseException:
            self.stop_heartbeat()
            self.commit(reason="session_error")
            raise
        finally:
            active.reset(token)
            self.close()                                                           

                       
    def record(self, action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
                                                                        
                                                                               
                                                                               
                                                                             
                                                                            
                                                                          
                                                                       
        if self._float_text:
            payload = _float_text(payload)
        with self._lock:
            rec = self.chain.append(action_type, payload)
        self._push(rec)
        return rec

    def _content(self, label: str, value: Any) -> dict[str, Any]:
        """Wrap one captured value: the digest always, and the content beside it.

        The hash stays beside the content so verification can re-derive the
        commitment without trusting the store.
        """
        if self._float_text:
            value = _float_text(value)
        d = _digest(value)
        d["content"] = value
        return {label: d}

    def record_model_call(self, provider: str, model: str, request: Any, response: Any,
                          usage: dict | None = None, **extra) -> dict[str, Any]:
        payload = {"provider": provider, "model": model}
        payload.update(self._content("request", request))
        payload.update(self._content("response", response))
        if usage:
            payload["usage"] = usage
        if extra:
            payload["extra"] = extra
        return self.record(MODEL_CALL, payload)

    def record_tool_call(self, name: str, args: Any, result: Any,
                        outcome: str = "success", kind: str = TOOL_CALL,
                        truncated: dict | None = None, **extra) -> dict[str, Any]:
        payload = {"tool": name, "outcome": outcome}
        payload.update(self._content("args", args))
        payload.update(self._content("result", result))
        if truncated:
                                                                             
                                                                              
            payload["truncated"] = truncated
        if extra:
            payload["extra"] = extra
        return self.record(kind, payload)

    def record_decision(self, summary: str, **fields) -> dict[str, Any]:
        return self.record(DECISION, {"summary": summary, **fields})

    def record_data_access(self, resource: str, op: str, **fields) -> dict[str, Any]:
        return self.record(DATA_ACCESS, {"resource": resource, "op": op, **fields})

    def record_human_oversight(self, action: str, **fields) -> dict[str, Any]:
        """Record that a human signed off on an action, as evidence in the signed chain."""
        return self.record(HUMAN_OVERSIGHT, {"action": action, **fields})

    def tool(self, name: str | None = None, kind: str = TOOL_CALL) -> Callable:
        """Decorator capturing a local tool's args + result at the dispatch boundary."""
        def deco(fn: Callable) -> Callable:
            tool_name = name or fn.__name__

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                try:
                    result = fn(*args, **kwargs)
                except Exception as e:
                    self.record_tool_call(tool_name, {"args": args, "kwargs": kwargs},
                                          {"error": str(e)}, outcome="failure", kind=kind)
                    raise
                self.record_tool_call(tool_name, {"args": args, "kwargs": kwargs}, result,
                                      kind=kind)
                return result
            return wrapper
        return deco

                         
    def start_heartbeat(self, interval_s: float = 30.0) -> None:
        if self._hb_thread is not None:
            return
        self._hb_stop = threading.Event()

        def loop():
            while not self._hb_stop.wait(interval_s):
                try:
                    with self._lock:
                        rec = self.chain.heartbeat()
                    self._push(rec)
                except Exception:
                    break

        self._hb_thread = threading.Thread(target=loop, daemon=True)
        self._hb_thread.start()

    def stop_heartbeat(self) -> None:
        if self._hb_stop is not None:
            self._hb_stop.set()
        self._hb_thread = None
        self._hb_stop = None
