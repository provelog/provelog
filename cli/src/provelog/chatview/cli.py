"""`provelog chat`: the records viewer's command surface.

Registration is registered when fastapi is importable, like the server commands
(provelog.cli._server_extra_present), and this module imports nothing
heavy at registration time - the app, the page and every reader load
inside the handler, so `provelog --help` pays nothing for this command.

Stopping: Ctrl+C stays as it is. `provelog chat --stop` - the
terminal way to close a viewer from anywhere - drops a stop file under
the user's own ~/.provelog/ that a running viewer consumes and answers
by shutting down gracefully (the same should_exit path Ctrl+C takes:
in-flight requests finish, the port is released, exit 0, the file
gone). No viewer running: a friendly line, not an error. The file
stays under the real home's ~/.provelog/ - never the --home being
read, which the stopping terminal does not know - and nothing but this
user's own viewers ever looks at it. The viewer is invoked from a
terminal only (no host slash commands).
"""

from __future__ import annotations

import argparse
import socket
from pathlib import Path

                                                                         
                                                                         
                            
_BASE_PORT = 8790
_PORT_SPAN = 100

                                                                        
                                                                    
                                                 
_STOP_FILE_NAME = "chat-viewer.stop"
                                                                      
                              
_STOP_WAIT_S = 4.0
                                                               
_STOP_POLL_S = 0.4


def _port_path():
    from pathlib import Path
    return Path.home() / ".provelog" / "chat-port"


def _last_port() -> int | None:
    try:
        return int(_port_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _remember_port(port: int) -> None:
    try:
        target = _port_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(port), encoding="utf-8")
    except OSError:
        pass


def _free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _token_path():
    from pathlib import Path
    return Path.home() / ".provelog" / "chat-token"


def _stable_token() -> str:
    """The viewer's URL token, kept across runs.

    A fresh token every run changes the URL, so a tab left open from yesterday
    cannot be refreshed: the browser asks the same port for a token that no
    longer exists. The token is not a secret from the user (it is in their own
    URL and in a 0600 file); it exists to keep other sites from poking the local
    server. Keeping it stable makes one URL work for as long as the viewer does,
    which is what a bookmark or a launcher should do.
    """
    import secrets
    from pathlib import Path
    target = Path(_token_path())
    try:
        token = target.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if len(token) >= 16:
        return token
    token = secrets.token_urlsafe(24)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(token, encoding="utf-8")
        import os
        os.chmod(target, 0o600)
    except OSError:
        pass
    return token


def _pick_port(wanted: int | None) -> int:
    """An exact port when asked for; otherwise the port this viewer used last
    when it is free, then the first free one from _BASE_PORT. Probing races by
    nature - if uvicorn still loses the port, that is an honest error, not
    something to hide."""
    if wanted is not None:
        return wanted
    last = _last_port()
    if last is not None and _free(last):
        return last
    for p in range(_BASE_PORT, _BASE_PORT + _PORT_SPAN):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise SystemExit(f"no free port found in {_BASE_PORT}-{_BASE_PORT + _PORT_SPAN}; "
                     f"pass --port")


def _stop_file_path():
    from pathlib import Path

    return Path.home() / ".provelog" / _STOP_FILE_NAME


def _clear_stale_stop_file() -> None:
    """A stop file nobody consumed (the viewer was already down when
    `chat --stop` ran, or died mid-shutdown): gone before we start, so a
    fresh viewer is never stopped by the last session's request."""
    _stop_file_path().unlink(missing_ok=True)


def _start_stop_watcher(server, port: int) -> None:
    """Watch the stop file while the viewer runs, in a daemon thread. A
    file whose port matches ours (or names no port) is consumed - deleted
    - and answered with the graceful-shutdown flag; a file naming some
    OTHER port is left for its own viewer."""
    import json
    import sys
    import threading
    import time

    path = _stop_file_path()

    def loop():
        while not server.should_exit:
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                raw = ""
            if raw:
                try:
                    doc = json.loads(raw)
                except ValueError:
                    doc = {}
                if doc.get("port") in (None, port):
                    path.unlink(missing_ok=True)                                  
                    print("  stop requested (provelog chat --stop); shutting down…")
                    sys.stdout.flush()
                    server.should_exit = True
                    return
            time.sleep(_STOP_POLL_S)

    threading.Thread(target=loop, daemon=True,
                     name="provelog-chat-stop-watcher").start()


def _request_stop(port: int | None) -> int:
    """`provelog chat --stop`: drop the stop file, give a running viewer a
    moment to consume it. Consumed -> it is shutting down (confirmation);
    not -> nothing was listening, the leftover is removed and the user
    hears the friendly truth. Never an error exit."""
    import json
    import os
    import sys
    import time
    from datetime import UTC, datetime

    from ..strings import t

    path = _stop_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "port": port,                                                   
        "pid": os.getpid(),
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
    }) + "\n", encoding="utf-8")
    sys.stdout.flush()
    deadline = time.monotonic() + _STOP_WAIT_S
    while time.monotonic() < deadline:
        time.sleep(0.1)
        if not path.exists():                                                
            print(t("chat.stopped"))
            return 0
    path.unlink(missing_ok=True)                                            
    print(t("chat.not_running"))
    return 0


def _state_path():
    from pathlib import Path
    return Path.home() / ".provelog" / "chat.json"


def _running_viewer():
    """The viewer already open on this device, if any is still alive.

    Reusing the running viewer keeps a launcher idempotent instead of starting
    a second server and a second token on every click.
    """
    import json as _json
    import os

    try:
        state = _json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    port, token, pid = state.get("port"), state.get("token"), state.get("pid")
    if not (port and token and pid):
        return None
    try:
        os.kill(int(pid), 0)
    except OSError:
        return None
    from ..netutil import client_for
    url = f"http://127.0.0.1:{int(port)}/{token}/"
    try:
        with client_for(url, timeout=1.5) as client:
            if client.get(url).status_code != 200:
                return None
    except Exception:
        return None
    return {"port": int(port), "token": str(token), "pid": int(pid), "url": url}


def _write_state(port: int, token: str) -> None:
    import json as _json
    import os

    target = _state_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_json.dumps({"port": port, "token": token,
                                       "pid": os.getpid()}), encoding="utf-8")
        os.chmod(target, 0o600)
    except OSError:
        pass


def _clear_state() -> None:
    try:
        _state_path().unlink(missing_ok=True)
    except OSError:
        pass


def _records_dir(home: Path) -> Path:
    from .records import store_root
    return store_root(home)


def _start_idle_watcher(server, timeout_s: float, app) -> None:
    """Exit after `timeout_s` with no HTTP request.

    The page polls every five seconds, so this only fires after the last tab
    is gone.
    """
    import threading
    import time

    timeout = float(timeout_s)
    if timeout <= 0:
        return
    state = {"last": time.monotonic(), "active": 0}

    inner = getattr(app, "_app", app)
    @inner.middleware("http")
    async def _touch(request, call_next):
        state["active"] += 1
        state["last"] = time.monotonic()
                                                                            
                                                               
        if not request.url.path.endswith("/__provelog/bye"):
            inner.state.viewer_bye_at = None
        try:
            return await call_next(request)
        finally:
            state["active"] -= 1
            state["last"] = time.monotonic()

    def loop():
        interval = min(2.0, max(1.0, timeout / 10))
        while not server.should_exit:
            time.sleep(interval)
            if server.should_exit:
                return
            bye_at = getattr(inner.state, "viewer_bye_at", None)
            if (bye_at is not None and state["active"] == 0
                    and time.monotonic() - bye_at >= 8.0):
                print("  viewer closed; exiting\n")
                import sys
                sys.stdout.flush()
                server.should_exit = True
                return
            if state["active"] == 0 and time.monotonic() - state["last"] >= timeout:
                print("  viewer idle; exiting\n")
                import sys
                sys.stdout.flush()
                server.should_exit = True
                return

    threading.Thread(target=loop, daemon=True,
                     name="provelog-chat-idle-watcher").start()


def _cmd_chat(args) -> int:
    from pathlib import Path

    from ..cli import _require_server_extra
    _require_server_extra("browse")
    import os
    import sys
    import threading
    import webbrowser

    args.no_browser = args.no_browser or bool(
        os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))

    def open_browser(url):
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False
        if not opened:
            print("Could not open a browser. Open the viewer URL above manually.", file=sys.stderr)

    import uvicorn

    if args.stop:
        return _request_stop(args.port)

    from ..strings import t
    from .app import create_app

    home = Path(args.home).expanduser() if args.home else Path.home()
    token = _stable_token()
    _clear_stale_stop_file()                                                         

    if (not args.stop and args.port is None
            and not getattr(args, "no_reuse", False)):
        running = _running_viewer()
        if running is not None:
            print(t("chat.already"))
            print()
            print(t("chat.url", url=running["url"]))
            if not args.no_browser:
                open_browser(running["url"])
            return 0

    port = _pick_port(args.port)
    app = create_app(home=home, token=token)
    url = f"http://127.0.0.1:{port}/{token}/"

    print("Opening your records…")
    print(t("chat.running"))
    print()
    print(t("chat.url", url=url))
    print(t("chat.records_dir", path=_records_dir(home)))
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        print("This URL is on the remote machine; use SSH port forwarding to open it locally.")
    print()
    print(t("chat.foreground_stop"))
    sys.stdout.flush()
    _remember_port(port)
    _write_state(port, token)
    if not args.no_browser:
        threading.Timer(0.6, lambda: open_browser(url)).start()
                                                                        
                                                                          
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    _start_stop_watcher(server, port)
    idle = float(getattr(args, "idle_timeout", 0) or 0)
                                                                             
                                                                        
                                                                        
                                                                          
    if idle <= 0 and not args.no_browser:
        idle = 120.0
    _start_idle_watcher(server, idle, app)
    try:
        server.run()
        if not server.started:
            print("Error: Viewer failed to start. Check the port and try "
                  "provelog browse --port PORT.", file=sys.stderr)
            return 1
    finally:
        _clear_state()
    return 0


def register_cli(sub: argparse._SubParsersAction) -> None:
    """Add `provelog chat` (when the server runtime is importable)."""
    c = sub.add_parser(
        "browse", aliases=["view", "chat"],
        help="open your recorded conversations in a browser (read-only, local-only)")
    c.add_argument("--port", type=int,
                   help=f"port for the viewer (default: first free from {_BASE_PORT})")
    c.add_argument("--no-browser", action="store_true",
                   help="do not auto-open a browser tab")
    c.add_argument("--stop", action="store_true",
                   help="stop a running viewer (does nothing if none is running)")
    c.add_argument("--home", metavar="DIR",
                   help="read another home's records (default: your own)")
    c.add_argument("--no-reuse", action="store_true",
                   help="start a second viewer even if one is already running")
    c.add_argument("--idle-timeout", type=float, default=0.0,
                   help="exit after N seconds with no viewer requests "
                        "(default 120 when a browser is opened; 0 disables)")
    c.set_defaults(func=_cmd_chat)
