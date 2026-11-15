"""The process-local active-recorder slot, shared by the SDK and the easy front.

A ContextVar lives here rather than in either module so instrumentation can
prefer the innermost OPEN session regardless of whether it was opened by
the internal `easy.record(...)` helper or by a bare `Provelog.session()` - both set it. Without the
SDK setting it, an explicitly-instrumented client recorded into whatever
recorder the wrapper was last bound to, silently crossing sessions.
"""

from __future__ import annotations

from contextvars import ContextVar

_active: ContextVar = ContextVar("provelog_active_recorder", default=None)


def current():
    return _active.get()


def set_active(recorder):
    return _active.set(recorder)


def reset(token):
    _active.reset(token)
