"""The viewer's local app: the evidence store over HTTP, behind a token.

Threat model and discipline (the hosted /verify page's, stricter):

  - BOUND TO 127.0.0.1 by the CLI; this module never chooses a host.
  - TOKEN-PATH: every route lives under /<token>/…, minted fresh per run.
    A random web page in the user's browser (or a DNS-rebinding domain
    resolving to 127.0.0.1) cannot guess it, so it cannot read the user's
    records through this server.
  - HOST HEADER: only 127.0.0.1/localhost[:port] are accepted - closes the
    DNS-rebinding lane outright rather than relying on the token alone.
  - CSP default-src 'none': the page is self-contained by CONSTRUCTION.

What it reads is ``~/ProveLog Records`` (see ``records.py``): the recorded
evidence, not the hosts' transcripts. The only writes are two the user asked
for and confirmed in the page: deleting one session's folder, and writing an
export zip to the browser's download.

Routes (all relative to the token prefix):

    GET    /                                   the page
    GET    /api/records/sessions               the list
    GET    /api/records/session/{sid}          one conversation
    GET    /api/records/session/{sid}/trajectory  turn/step event ledger
    DELETE /api/records/session/{sid}          delete its folder
    POST   /api/records/session/{sid}/reveal   reveal in Finder (macOS)
    POST   /api/records/session/{sid}/open-folder     open its evidence folder
    POST   /api/records/session/{sid}/open-workspace  open the session's project
    POST   /api/records/agent/{agent}/open            launch the agent's app
    GET    /api/records/session/{sid}/export   evidence zip download
    GET    /api/records/export-all             one zip with every session
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from . import agent_apps, export_dir, records, view_state

                                                                  
                                                                           
                                                                         
                                                                             
                                                                           
                             
_SECURITY_HEADERS = (
    (b"content-security-policy",
     b"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
     b"img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
     b"frame-ancestors 'none'"),
    (b"x-frame-options", b"DENY"),
    (b"x-robots-tag", b"noindex, nofollow"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
    (b"cache-control", b"no-store"),
)


def _lang(request: Request) -> str:
    """The language this response should speak, from the request alone."""
    return "zh" if "zh" in (request.headers.get("accept-language") or "").lower() else "en"


def _say(lang: str, en: str, zh: str) -> str:
    return zh if lang == "zh" else en


def reveal_command(target: Path) -> list[str]:
    """The platform's "show me this" command.

    macOS reveals the file in Finder, Windows selects it in Explorer, and on
    Linux there is no standard reveal - the file manager opens the folder.
    """
    import sys

    if sys.platform == "darwin":
        return ["open", "-R", str(target)]
    if sys.platform.startswith("win"):
        return ["explorer", f"/select,{target}"]
    return ["xdg-open", str(target if target.is_dir() else target.parent)]


def open_path_command(target: Path) -> list[str]:
    """Open a *folder* in the platform's file manager.

    Different from ``reveal_command``: reveal selects the item in its parent,
    while this opens the folder itself (the double-click gesture in the
    viewer means "open this directory", not "show me where it sits").
    """
    import sys

    if sys.platform == "darwin":
        return ["open", str(target)]
    if sys.platform.startswith("win"):
        return ["explorer", str(target)]
    return ["xdg-open", str(target)]


def _open_target(target: Path, request: Request) -> None:
    """Run the platform's open command, or explain why it could not."""
    import subprocess

    try:
        subprocess.run(open_path_command(target), check=False, timeout=10,
                       capture_output=True)
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(
            status_code=501,
            detail=_say(_lang(request),
                        "no file manager could be opened on this system",
                        "这个系统上打不开文件夹")) from None


def _host_header(scope) -> str:
    for k, v in scope.get("headers", []):
        if k == b"host":
            return v.decode("latin-1")
    return ""


def _is_local_host_header(host: str) -> bool:
    """127.0.0.1[:port] / [::1][:port] / localhost[:port] only."""
    host = host.lower()
    if host.startswith("["):                                    
        return host.startswith("[::1]")
    name = host.split(":", 1)[0] if ":" in host else host
    return name in ("127.0.0.1", "localhost")


class GatedApp:
    """Pure-ASGI wrapper: token prefix check, Host check, prefix strip, and
    the security headers stamped on every response (errors included)."""

    def __init__(self, app, token: str):
        self._app = app
        self._prefix = "/" + token
                                            
        self.title = "provelog chat"

                                                                                
                                                            
    _PUBLIC = ("/favicon.ico", "/favicon.svg", "/favicon.png",
               "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in self._PUBLIC:
            if not _is_local_host_header(_host_header(scope)):
                await self._reject(send)
                return
            await self._app(scope, receive, send)                          
            return
        if ((not _is_local_host_header(_host_header(scope)))
                or not (path == self._prefix or path.startswith(self._prefix + "/"))):
                                                                             
                                                                     
                                                                            
                                                                           
                              
            if path == "/" and _is_local_host_header(_host_header(scope)):
                await send({"type": "http.response.start", "status": 302,
                            "headers": [(b"location", (self._prefix + "/").encode())]
                            + list(_SECURITY_HEADERS)})
                await send({"type": "http.response.body", "body": b""})
                return
            await self._reject(send)
            return

        scope = dict(scope)
        scope["path"] = path[len(self._prefix):] or "/"

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                existing = {name.lower() for name, _ in message.get("headers", [])}
                                                                                 
                                                                                  
                                                       
                extra = [h for h in _SECURITY_HEADERS if h[0] not in existing]
                message = {**message,
                           "headers": list(message.get("headers", [])) + extra}
            await send(message)

        await self._app(scope, receive, send_with_headers)

    @staticmethod
    async def _reject(send):
        await send({"type": "http.response.start", "status": 404,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8")]
                    + list(_SECURITY_HEADERS)})
        await send({"type": "http.response.body", "body": b"not found"})


def create_app(home: Path | None = None,
               token: str | None = None) -> GatedApp:
    """home: whose records to read (tests inject a fixture tree)."""
    home = home if home is not None else Path.home()
    token = token or secrets.token_urlsafe(24)
    from .page import PAGE_HTML

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def _not_found(lang: str):
        return HTTPException(
            status_code=404,
            detail=_say(lang, "no such conversation", "没有这个会话"))

    @app.api_route("/favicon.ico", methods=["GET", "HEAD"])
    def favicon_ico():
        from .. import __file__ as package_file
        icon = Path(package_file).resolve().parent / "assets" / "favicon.ico"
        return FileResponse(icon, media_type="image/x-icon",
                            headers={"cache-control": "public, max-age=86400"})

    @app.api_route("/apple-touch-icon.png", methods=["GET", "HEAD"])
    @app.api_route("/apple-touch-icon-precomposed.png", methods=["GET", "HEAD"])
    def apple_touch_icon():
        from .. import __file__ as package_file
        icon = Path(package_file).resolve().parent / "assets" / "apple-touch-icon.png"
        return FileResponse(icon, media_type="image/png",
                            headers={"cache-control": "public, max-age=86400"})

    @app.api_route("/favicon.svg", methods=["GET", "HEAD"])
    def favicon_svg():
        """The tab/bookmark icon. Browsers ask the page's own directory for it,
        so it lives under the token prefix like everything else."""
        from .. import __file__ as package_file
        icon = Path(package_file).resolve().parent / "assets" / "favicon.svg"
        return FileResponse(icon, media_type="image/svg+xml",
                            headers={"cache-control": "public, max-age=86400"})

    @app.api_route("/favicon.png", methods=["GET", "HEAD"])
    def favicon_png():
        from .. import __file__ as package_file
        icon = Path(package_file).resolve().parent / "assets" / "favicon-64.png"
        return FileResponse(icon, media_type="image/png",
                            headers={"cache-control": "public, max-age=86400"})

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
    def index():
                                                                                 
                                                                
        return HTMLResponse(PAGE_HTML)

    @app.post("/__provelog/bye")
    def viewer_bye():
        """Best-effort signal that the last tab is closing.

        `navigator.sendBeacon` is not guaranteed (the tab may crash or the
        process may be killed), so the idle watcher remains the fallback.
        """
        import time

        app.state.viewer_bye_at = time.monotonic()
        return {"ok": True}

    @app.get("/api/records/sessions")
    def api_sessions():
        import sys

        return {"home": str(home),
                "store": str(records.store_root(home)),
                "platform": sys.platform,
                "apps": agent_apps.available_apps(),
                "sessions": records.sessions(home)}

    @app.get("/api/records/session/{session_id}")
    def api_session(session_id: str, request: Request, limit: int | None = None):
        row = records.find(home, session_id)
        if row and row.get("nested_child") and not row.get("nested_fork"):
            raise _not_found(_lang(request))
        verify_integrity = (limit is None
                            or int((row or {}).get("records") or 0) <= 2000)
        view = records.load(home, session_id, verify_integrity=verify_integrity)
        if view is None:
            raise _not_found(_lang(request))
        if limit is not None:
            total = len(view.get("messages") or [])
            view["messages_total"] = total
            if limit >= 0:
                view["messages"] = (view.get("messages") or [])[:limit]
        view["children"] = []
        view["siblings"] = []
        return view

    @app.get("/api/records/session/{session_id}/trajectory")
    def api_trajectory(session_id: str, request: Request,
                       limit: int | None = None):
        row = records.find(home, session_id)
        if row and row.get("nested_child") and not row.get("nested_fork"):
            raise _not_found(_lang(request))
        view = records.trajectory(home, session_id, limit=limit)
        if view is None:
            raise _not_found(_lang(request))
        return view

    @app.delete("/api/records/session/{session_id}")
    def api_delete(session_id: str, request: Request):
        """Delete one conversation = delete its folder (the product's rule)."""
        if records.find(home, session_id) is None:
            raise _not_found(_lang(request))
        if not records.delete(home, session_id):
            raise HTTPException(
                status_code=409,
                detail=_say(_lang(request), "could not delete this conversation",
                            "删除失败"))
                                                                             
                                                        
        view_state.clear(home, session_id)
                                                                             
                                                                           
        try:
            from ..capture_state import mark_deleted
            mark_deleted(session_id)
        except Exception:
            pass
        return {"deleted": session_id}

    @app.get("/api/records/session/{session_id}/attachment/{file_name}")
    def api_attachment(session_id: str, file_name: str, request: Request):
        """One image/blob the record references, served from the session folder."""
        import mimetypes

        from .. import attachments

        folder = records.folder_for(home, session_id)
        if folder is None:
            raise _not_found(_lang(request))
        path = attachments.resolve(folder, file_name)
        if path is None:
            raise _not_found(_lang(request))
        media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media,
                            headers={"cache-control": "no-store"})

    @app.get("/api/records/session/{session_id}/file")
    def api_session_file(session_id: str, path: str, request: Request):
        """A local image referenced by Markdown in this session's messages.

        DSH resolves relative Markdown images against the session workspace and
        absolute paths through the local file API. This route matches that
        behavior, but only for existing image files under the workspace or the
        user's home; the token path still gates every request.
        """
        import mimetypes

        row = records.find(home, session_id)
        if row is None:
            raise _not_found(_lang(request))
                                                                             
                                                                       
        folder = records.folder_for(home, session_id)
        if folder is not None:
            from .. import markdown_images
            captured = markdown_images.lookup(
                records._manifest(folder), path, Path(folder))
            if captured is not None:
                media = mimetypes.guess_type(captured.name)[0] or "application/octet-stream"
                return FileResponse(captured, media_type=media,
                                    headers={"cache-control": "no-store"})
        project = Path(str(row.get("project") or "")).expanduser()
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = project / target
        try:
            target = target.resolve()
        except OSError:
            raise _not_found(_lang(request)) from None
        allowed = [Path.home().resolve()]
        if str(project):
            try:
                allowed.append(project.resolve())
            except OSError:
                pass
        if not any(target == root or target.is_relative_to(root) for root in allowed):
            raise _not_found(_lang(request))
        if not target.is_file() or target.suffix.lower() not in {
                ".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".bmp"}:
            raise _not_found(_lang(request))
        media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, media_type=media,
                            headers={"cache-control": "no-store"})

    @app.post("/api/records/session/{session_id}/reveal-path")
    def api_reveal_path(session_id: str, path: str, request: Request):
        """Reveal a local file linked from a message, never an arbitrary path."""
        import subprocess

        row = records.find(home, session_id)
        if row is None:
            raise _not_found(_lang(request))
        project = Path(str(row.get("project") or "")).expanduser()
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = project / target
        try:
            target = target.resolve()
        except OSError:
            raise _not_found(_lang(request)) from None
                                                                              
                                                                            
                                                                             
                                               
        allowed: list[Path] = []
        if str(project):
            try:
                allowed.append(project.resolve())
            except OSError:
                pass
        folder = records.folder_for(home, session_id)
        if folder is not None:
            allowed.append(folder)
        if not any(target == root or target.is_relative_to(root) for root in allowed):
            raise _not_found(_lang(request))
        if not target.exists():
            raise _not_found(_lang(request))
        try:
                                                                              
                                                                        
                                             
            subprocess.run(reveal_command(target), check=False)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"revealed": str(target)}

    @app.post("/api/records/session/{session_id}/reveal")
    def api_reveal(session_id: str, request: Request,
                   file: str | None = None):
        """Show the session's folder - or one of its files - in the file manager.

        ``file`` is a bare member name from a fixed list: the viewer can reveal
        the record file, never an arbitrary path.
        """
        import subprocess

        folder = records.folder_for(home, session_id)
        if folder is None:
            raise _not_found(_lang(request))
        target = folder
        if file is not None:
            if file not in ("trajectory.jsonl.zst", "trajectory.jsonl",
                            "transcript-preview.md", "manifest.json"):
                raise HTTPException(status_code=422,
                                    detail=_say(_lang(request), "unknown file",
                                                "未知文件"))
            target = folder / file
            if not target.is_file():
                raise _not_found(_lang(request))
        try:
            subprocess.run(reveal_command(target), check=False, timeout=10,
                           capture_output=True)
        except (OSError, subprocess.SubprocessError):
            raise HTTPException(
                status_code=501,
                detail=_say(_lang(request),
                            "no file manager could be opened on this system",
                            "这个系统上打不开文件管理器")) from None
        return {"revealed": str(target)}

    @app.post("/api/records/session/{session_id}/open-folder")
    def api_open_folder(session_id: str, request: Request):
        """Open the conversation's own evidence folder (not its parent)."""
        folder = records.folder_for(home, session_id)
        if folder is None:
            raise _not_found(_lang(request))
        _open_target(folder, request)
        return {"opened": str(folder)}

    @app.post("/api/records/session/{session_id}/open-workspace")
    def api_open_workspace(session_id: str, request: Request):
        """Open the project directory this conversation belongs to.

        The path comes from the session row, not from the request: a
        double-click can reveal a known workspace, never an arbitrary path.
        A host that records no cwd (or only a leaf name) is a 409 with an
        explanation, not a silent no-op.
        """
        row = records.find(home, session_id)
        if row is None:
            raise _not_found(_lang(request))
        project = str(row.get("project") or "").strip()
        path = Path(project).expanduser()
        if not project or not path.is_absolute() or not path.is_dir():
            raise HTTPException(
                status_code=409,
                detail=_say(_lang(request),
                            "this workspace has no local folder to open",
                            "这个工作区没有可打开的本地文件夹"))
        _open_target(path, request)
        return {"opened": str(path)}

    @app.post("/api/records/agent/{agent}/open")
    def api_open_agent(agent: str, request: Request):
        """Launch the desktop app that owns this agent's local store."""
        try:
            return agent_apps.open_agent(agent)
        except agent_apps.AgentAppError as exc:
            raise HTTPException(
                status_code=exc.status,
                detail=_say(_lang(request), exc.en, exc.zh)) from None

    def _evidence(session_id: str):
        from . import exporter

        row = records.find(home, session_id)
        if row is None:
            return None
        groups = exporter.scan_store(home)
        return exporter.session_evidence(home, row["agent"], session_id,
                                         row.get("folder"), None, row["title"],
                                         groups=groups)

    @app.get("/api/records/session/{session_id}/export")
    def api_export(session_id: str, request: Request):
        """The session's self-contained evidence zip, as a browser download."""
        from . import exporter

        ev = _evidence(session_id)
        if ev is None:
            raise _not_found(_lang(request))
        if not ev.exportable:
            raise HTTPException(
                status_code=422,
                detail=_say(_lang(request), "this conversation has no exportable records",
                            "该会话没有可导出的记录"))
        data, rel, count = exporter.session_zip_bytes(home, ev, "all")
        name = rel.rsplit("/", 1)[-1]
        return Response(content=data, media_type="application/zip", headers={
            "content-disposition": f'attachment; filename="{name}"',
            "x-export-path": rel,
            "x-export-records": str(count),
        })

    @app.get("/api/records/session/{session_id}/export-status")
    def api_export_status(session_id: str, request: Request):
        """Is everything in this conversation covered by a third-party timestamp?

        The viewer asks before it hands out a pack: an export that silently lacks
        a stamp is exactly the artefact this product is supposed to make visible.
        """
        ev = _evidence(session_id)
        if ev is None:
            raise _not_found(_lang(request))
        total = len(ev.rows)
        stamped = min(int(ev.covers or 0), total)
                                                                              
        view = records.load(home, session_id, verify_integrity=False) or {}
        turns = int(view.get("turns") or 0)
                                                                          
                                                                           
                                                                           
        whole = (total > 0 and stamped >= total) or (
            turns > 0 and view.get("stamp") == "stamped"
            and not view.get("stamp_partial"))
        return {"records": total, "stamped": stamped,
                "turns": turns,
                "turns_stamped": view.get("turns_stamped"),
                "complete": whole}

    def scoped_rows(request):
        rows = records.sessions(home)
        for param in ("agent", "group"):
            value = request.query_params.get(param)
            if value is not None:
                rows = [r for r in rows if (r.get("agent") if param == "agent" else (r.get("project") or r.get("agent") or "")) == value]
        return rows

    @app.get("/api/records/export-status")
    def api_export_status_all(request: Request):
        """The same check across every conversation on this device."""
        rows = scoped_rows(request)
        total = sum(int(r.get("records") or 0) for r in rows)
        stamped = sum(min(int(r.get("stamp_covers") or 0), int(r.get("records") or 0))
                      for r in rows)
        whole = sum(1 for r in rows if records.conversation_stamped(home, r))
        return {"sessions": len(rows), "sessions_stamped": whole,
                "records": total, "stamped": stamped,
                "complete": total > 0 and whole == sum(
                    1 for r in rows if int(r.get("records") or 0) > 0)}

    @app.get("/api/records/export-all")
    def api_export_all(request: Request):
        """One zip with every recorded conversation on this device."""
        from . import exporter

        rows = scoped_rows(request)
        if not rows:
            raise HTTPException(
                status_code=422,
                detail=_say(_lang(request), "no recorded conversations to export",
                            "本机没有已记录的会话可导出"))
        groups = exporter.scan_store(home)
        evs = []
        for row in rows:
            ev = exporter.session_evidence(home, row["agent"], row["session_id"],
                                           row.get("folder"), None, row["title"],
                                           groups=groups)
            if ev.exportable:
                evs.append(ev)
        if not evs:
            raise HTTPException(
                status_code=422,
                detail=_say(_lang(request), "no recorded conversations to export",
                            "本机没有已记录的会话可导出"))
        data, name = exporter.export_all_bytes(home, evs)
        return Response(content=data, media_type="application/zip", headers={
            "content-disposition": f'attachment; filename="{name}"',
            "x-export-sessions": str(len(evs)),
        })

    @app.post("/api/records/export-selection")
    def export_selection(request: Request, payload: dict):
        """Check, then export, the chosen conversations: same rules as ``provelog export``.

        ``preview`` only reports. Like the CLI: a conversation whose records
        fail verification is never written (it is listed with the reason, and
        the rest can still be exported); missing attachments are a warning;
        missing or partial timestamps are a status the user decides on
        (wait, export anyway, or cancel), never a refusal.
        """
        import tempfile

        from ..cli import _export_prepare
        from ..collection import write_collection
        from ..export_flow import assess, blocked, reason, waiting_state
        ids = payload.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids):
            raise HTTPException(400, "Choose conversations to export")
        wanted = set(ids)
        rows = [r for r in records.sessions(home) if r['session_id'] in wanted]
        if {r['session_id'] for r in rows} != wanted:
            raise HTTPException(409, "Selection changed; refresh and select again")
        store = records._store(home)
        items = []
        failures = []
        states = {"complete": 0, "partial": 0, "none": 0, "unsupported": 0}
        missing_total = 0
        seen = set()
        try:
            for row in rows:
                own = []
                try:
                    manifest = records._manifest(Path(row['folder']))
                    for stream in manifest.get('streams', {}):
                        if stream in seen:
                            continue
                        seen.add(stream)
                        prepared = _export_prepare(store, stream)
                        if not prepared:
                            raise ValueError("No records found.")
                        bundle, files, missing, *_ = prepared
                        member_row = {**row, 'stream_id': stream}
                        starts = [r for r in bundle.get('records', [])
                                  if r.get('action_type') == 'lifecycle.session_start']
                        if starts:
                            meta = (starts[0].get('payload') or {}).get('meta') or {}
                            member_row['session_id'] = meta.get('host_session_id') or row['session_id']
                            member_row['title'] = meta.get('title') or ''
                        item = assess({'bundle': bundle, 'files': files, 'row': member_row})
                        if blocked(item):
                            raise ValueError(reason(item))
                        missing_total += len(missing or [])
                        own.append(item)
                    if not own:
                        raise ValueError("No records found.")
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    failures.append({'session_id': row['session_id'],
                                     'title': str(row.get('title') or row.get('title_hint') or ''),
                                     'reason': str(exc)})
                    continue
                items.extend(own)
                kinds = {i['state'] for i in own}
                state = ("complete" if kinds == {"complete"} else
                         "none" if kinds == {"none"} else
                         "unsupported" if kinds == {"unsupported"} else "partial")
                states[state] += 1
            complete = bool(items) and all(i['complete'] for i in items)
            if payload.get('preview'):
                return {'sessions': len(rows), 'exportable': len(rows) - len(failures),
                        'streams': len(items), 'complete': complete, 'states': states,
                        'waiting': '' if complete or not items else waiting_state(store, items),
                        'missing_attachments': missing_total, 'failures': failures}
            if failures:
                raise HTTPException(422, "Some selected conversations failed verification; "
                                         "check the selection again")
            if not items:
                raise HTTPException(422, "No evidence to export")
            if not complete and not payload.get('allow_unstamped'):
                raise HTTPException(409, "Confirm export without complete timestamps")
            if payload.get('save'):
                                                                               
                target_dir = export_dir.current(home)
                try:
                    target_dir.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise HTTPException(409, f"Cannot use the export folder: {exc}") from None
            members = [{'name':f'session-{n+1}', 'bundle':i['bundle'], 'files':i['files'],
                        'meta':{'session_id':i['row']['session_id'], 'title':i['row']['title'], 'agent':i['row']['agent'], 'stream_id':i['row']['stream_id']}}
                       for n,i in enumerate(items)]
            with tempfile.TemporaryDirectory(prefix='provelog-selection-') as temp:
                output = Path(temp) / 'provelog-selection.zip'
                write_collection(output, members)
                data = output.read_bytes()
            if payload.get('save'):
                                                                          
                                                                            
                                                                 
                from .. import export_names as names
                from ..export_flow import atomic_write
                base = (names.pack_basename({**rows[0], 'last_activity': rows[0].get('last_at')})
                        if len(rows) == 1 else names.collection_name(len(rows)))
                for _attempt in range(20):
                    out = names.next_free(target_dir / base)
                    try:
                        atomic_write(out, data, overwrite=False)
                        break
                    except FileExistsError:
                        continue
                    except OSError as exc:
                        raise HTTPException(409, f"Cannot write the export: {exc}") from None
                else:
                    raise HTTPException(409, "Cannot find a free file name in the export folder")
                return {'path': str(out), 'name': out.name, 'dir': str(target_dir),
                        'bytes': len(data), 'sessions': len(rows)}
            return Response(data, media_type='application/zip', headers={
                'content-disposition':'attachment; filename="provelog-selection.zip"'})
        finally:
            store.close()

    @app.get("/api/records/export-dir")
    def api_export_dir():
        chosen = export_dir.current(home)
        default = export_dir.default_dir(home)
        return {"dir": str(chosen), "default": str(default), "is_default": chosen == default}

    @app.post("/api/records/export-dir")
    def api_export_dir_set(request: Request, payload: dict):
        """Set the folder exports are saved to; an empty value restores the default."""
        try:
            chosen = export_dir.remember(home, str(payload.get("dir") or ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        default = export_dir.default_dir(home)
        return {"dir": str(chosen), "default": str(default), "is_default": chosen == default}

    @app.post("/api/records/export-dir/choose")
    def api_export_dir_choose(request: Request):
        """Open the operating system's folder chooser on this computer.

        The path comes from that dialog, never from the request, so nothing a
        page sends can point exports somewhere the user did not pick.
        """
        picked, supported = export_dir.choose_folder(export_dir.current(home))
        if not supported:
            return {"supported": False}
        if not picked:
            return {"supported": True, "cancelled": True}
        try:
            chosen = export_dir.remember(home, picked)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        default = export_dir.default_dir(home)
        return {"supported": True, "dir": str(chosen), "default": str(default),
                "is_default": chosen == default}

    @app.post("/api/records/export-reveal")
    def api_export_reveal(request: Request, payload: dict):
        """Show a file this viewer just exported; only inside the export folder."""
        import subprocess
        target = Path(str(payload.get("path") or ""))
        try:
            target = target.resolve()
            root = export_dir.current(home).resolve()
        except OSError:
            raise _not_found(_lang(request)) from None
        if not target.is_file() or not target.is_relative_to(root):
            raise _not_found(_lang(request))
        try:
            subprocess.run(reveal_command(target), check=False)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"revealed": str(target)}

    return GatedApp(app, token)
