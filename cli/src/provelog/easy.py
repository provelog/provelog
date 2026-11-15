"""Internal configuration and recording helpers.

This module is not a public SDK. It exists because the importers, CLI, doctor
and tests share device-key/config resolution; the `record`/`make_recorder`
helpers are kept for internal tests only. The public product is the `provelog`
CLI.
"""

from __future__ import annotations

import functools
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .sdk import Provelog

CONFIG_FILENAME = ".provelog.json"
KEY_FILENAME = ".provelog.key"

_GLOBAL: dict[str, Any] = {}


def current_recorder() -> Provelog | None:
    """The recorder for the innermost open session - set by record() AND by a
    bare Provelog.session(), so instrumentation never crosses sessions."""
    from . import active
    return active.current()


def configure(*, stream_id: str | None = None, store: str | None = None,
              anchor_endpoint: str | None = None,
              capture_content: bool | None = None) -> None:
    """Set process-wide defaults so later record() calls need no arguments."""
    for k, v in {"stream_id": stream_id, "store": store,
                 "anchor_endpoint": anchor_endpoint,
                 "capture_content": capture_content}.items():
        if v is not None:
            _GLOBAL[k] = v


def find_config_file(start: Path | None = None) -> Path | None:
    """Locate `.provelog.json`, searching upward from `start` and then the home directory.

    Upward search is not a convenience here, it is a correctness property: an agent is
    routinely launched from a subdirectory (a package inside a monorepo, `apps/web`, a
    nested worktree), and looking only at the current directory would silently find no
    config and record nowhere.

    The walk stops at the filesystem root. Home stays the last resort so a machine-wide default
    still works when a project has no file of its own.
    """
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    home = Path.home() / CONFIG_FILENAME
    return home if home.is_file() else None


def _load_config_file() -> dict[str, Any]:
    path = find_config_file()
    if path is None:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _resolve(**overrides: Any) -> dict[str, Any]:
    """Merge recorder settings: explicit args > configure() > env > config file.

    The keys that still mean anything: stream_id (which local stream to write),
    store (path to the folder store; default ~/ProveLog), and
    anchor_endpoint (where the collector sends roots for automatic stamping). The legacy endpoint/token keys are still READ, so an
    old .provelog.json neither breaks recording nor silently pins a stream to
    a server that no longer accepts records - they are simply ignored."""
    file_cfg = _load_config_file()

    def _env(name: str) -> str | None:
        return os.environ.get(f"PROVELOG_{name}")

    env = {
        "stream_id": _env("STREAM_ID"),
        "store": _env("STORE"),
                                                                               
                                                                             
        "anchor_endpoint": _env("ANCHOR_URL") or _env("ANCHOR_ENDPOINT"),
    }
    out: dict[str, Any] = {}
    for key in ("stream_id", "store", "anchor_endpoint", "capture_content"):
        for source in (overrides, _GLOBAL, env, file_cfg):
            if source.get(key) is not None:
                out[key] = source[key]
                break
    return out


def write_config(path: str | Path, **cfg: Any) -> Path:
    """Persist a .provelog.json so subsequent record() calls need zero arguments."""
    p = Path(path)
    p.write_text(json.dumps({k: v for k, v in cfg.items() if v is not None}, indent=2),
                 encoding="utf-8")
    return p


def device_key_path() -> Path:
    """Where the next device key would live, without creating one.

    Mirrors _device_key: beside the config when one exists, otherwise the
    user-scoped ~/.provelog/device.key; existing legacy locations are honored.
    """
    config = find_config_file()
    if config is not None:
        return config.with_name(KEY_FILENAME)
    home_key = Path.home() / ".provelog" / "device.key"
    for path in (home_key, Path.cwd() / KEY_FILENAME, Path.home() / KEY_FILENAME):
        if path.is_file():
            return path
    return home_key


def _device_key() -> Any:
    """Load the persistent device signing key, creating it on first use.

    The key is anchored to the CONFIG, not the cwd: an agent is routinely launched
    from a subdirectory (the reason find_config_file() searches upward), and a key
    that resolved per-cwd would silently mint a second identity mid-stream — which
    the verifier reports as tampering. When a config exists, the search is bounded
    to BESIDE the config and nowhere else (crawling past it let a stray key in a
    parent directory hijack this project's identity); with no config anywhere,
    cwd-then-home applies. Permissions 0600; keep it out of version control;
    losing it only means future runs sign under a new identity."""
    from .keys import SigningKey
    config = find_config_file()
    if config is not None:
        beside = config.with_name(KEY_FILENAME)
        if beside.is_file():
            return SigningKey.load(beside)
        key = SigningKey.generate()
        key.save(beside)
        return key
                                                                         
                                                                          
                                                                          
                                   
    home_key = Path.home() / ".provelog" / "device.key"
    for path in (home_key, Path.cwd() / KEY_FILENAME, Path.home() / KEY_FILENAME):
        if path.is_file():
            return SigningKey.load(path)
    home_key.parent.mkdir(parents=True, exist_ok=True)
    key = SigningKey.generate()
    key.save(home_key)
    return key


def make_recorder(agent: str | None = None, **conn: Any) -> Provelog:
    """Build a ready Provelog against the LOCAL sink. Recording never leaves
    the device; anchoring is the separate, device-signed act.

    The store keeps the complete trajectory: capture_content is always on. The
    argument is kept only so older call sites do not break; it no longer
    selects a hash-only mode."""
    from .store import open_store

    cfg = _resolve(**conn)
    store = open_store(cfg.get("store"))
    stream_id = cfg.get("stream_id") or f"local-{agent or 'agent'}"
    return Provelog(stream_id, key=_device_key(),
                    store=store, capture_content=True)


@contextmanager
def record(agent: str = "agent", *, meta: dict[str, Any] | None = None,
           **conn: Any):
    """Open a recorded session in one line. Provisions, builds, sessions, seals, and drains.

    Yields the Provelog so you can still call fr.record_decision(...) etc. inside."""
    fr = make_recorder(agent, **conn)
    session_meta = {"agent": agent, **(meta or {})}
    with fr.session(session_meta):
        yield fr


def recorded(agent: str = "agent", **kw: Any):
    """Decorator form: wrap a whole function in a recorded session.

        @provelog.recorded("nightly-job")
        def run(): ...
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with record(agent, **kw):
                return fn(*args, **kwargs)
        return wrapper
    return deco
