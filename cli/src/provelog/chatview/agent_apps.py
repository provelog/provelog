"""Desktop apps the local viewer can open for an agent.

Double-clicking an agent in the sidebar should do the obvious thing: open the
app that produced those sessions. The mapping is deliberately small and
explicit - it contains only agents whose desktop app is a different surface of
the *same* local store.

CLI-only agents are not mapped: ``claude-code`` is not Claude.app and ``codex``
is not ChatGPT.app, even though those apps exist on the same machine. Opening
the wrong product would be worse than saying "this one has no desktop app".

The viewer runs on macOS today. ``available_apps`` returns nothing on other
platforms, so the double-click affordance is never advertised where it cannot
work.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

                                                                 
AGENT_APPS: dict[str, dict[str, object]] = {
    "dsh": {"label": "DeepSeek Harness", "names": ("DeepSeek Harness",)},
    "doubao": {"label": "Doubao", "names": ("Doubao",)},
    "doubao-work": {"label": "Doubao Work", "names": ("DoubaoWork",)},
    "minimax": {"label": "MiniMax Code", "names": ("MiniMax Code",)},
    "qoder-cn": {"label": "Qoder CN", "names": ("Qoder CN",)},
    "qoder": {"label": "Qoder", "names": ("Qoder",)},
    "codebuddy": {"label": "CodeBuddy", "names": ("CodeBuddy CN", "CodeBuddy")},
    "workbuddy": {"label": "WorkBuddy", "names": ("WorkBuddy",)},
    "kimi-code": {"label": "Kimi Code", "names": ("Kimi Code",)},
    "cursor": {"label": "Cursor", "names": ("Cursor",)},
    "antigravity": {"label": "Antigravity", "names": ("Antigravity",)},
}


class AgentAppError(Exception):
    """An open request the viewer can explain to the user."""

    status = 409

    def __init__(self, en: str, zh: str) -> None:
        super().__init__(en)
        self.en = en
        self.zh = zh


class AgentHasNoApp(AgentAppError):
    pass


class AgentAppMissing(AgentAppError):
    pass


class AgentAppUnsupported(AgentAppError):
    status = 501


class AgentAppLaunchFailed(AgentAppError):
    status = 501


def _app_dirs() -> tuple[Path, ...]:
    """Where a normal macOS app install lives (never a user-supplied path)."""
    return (Path("/Applications"), Path.home() / "Applications")


def find_app(agent: str) -> Path | None:
    """The installed .app bundle for this agent, or None.

    Only the fixed names in ``AGENT_APPS`` are probed under the standard
    application directories; the request cannot name a path.
    """
    spec = AGENT_APPS.get(agent)
    if not spec:
        return None
    names = spec.get("names") or ()
    for base in _app_dirs():
        for name in names:
            app = base / f"{name}.app"
            if app.is_dir():
                return app
    return None


def available_apps() -> dict[str, str]:
    """agent key -> display name, for agents whose desktop app is installed."""
    if sys.platform != "darwin":
        return {}
    out: dict[str, str] = {}
    for agent, spec in AGENT_APPS.items():
        if find_app(agent) is not None:
            out[agent] = str(spec["label"])
    return out


def open_agent(agent: str) -> dict[str, str]:
    """Launch the agent's desktop app and return what was opened.

    Raises ``AgentAppError`` subclasses; the HTTP layer turns those into a
    user-facing message (no app / not installed / unsupported).
    """
    spec = AGENT_APPS.get(agent)
    if spec is None:
        raise AgentHasNoApp(
            "this agent has no desktop app; use its CLI",
            "这个工具只有 CLI，没有可打开的客户端")
    if sys.platform != "darwin":
        raise AgentAppUnsupported(
            "opening a desktop app is only implemented on macOS",
            "当前系统还不支持从查看器打开桌面应用")
    app = find_app(agent)
    if app is None:
        raise AgentAppMissing(
            f"{spec['label']} is not installed",
            f"没有找到 {spec['label']} 的应用")
    try:
        proc = subprocess.run(["open", str(app)], check=False, timeout=10,
                              capture_output=True)
    except (OSError, subprocess.SubprocessError):
        raise AgentAppLaunchFailed(
            "the app could not be launched",
            "应用启动失败") from None
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise AgentAppLaunchFailed(
            detail or "the app could not be launched",
            detail or "应用启动失败")
    return {"agent": agent, "app": str(spec["label"]), "path": str(app)}
