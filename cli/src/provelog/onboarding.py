"""One-command setup for recording.

The default path installs the background log collector (`watch`). It does not
touch model base URLs, shell profiles, or host settings, so it cannot break a
signed-in host.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path


                                                                            
                                                                               
                           
def root() -> Path:
    return Path.home() / ".provelog"


def logs_dir() -> Path:
    return root() / "logs"


def _provelog_command() -> str:
    import shutil
    exe = shutil.which("provelog")
    if exe:
        return exe
    return f'"{sys.executable}" -m provelog'


def _watch_command() -> str:
    """The internal collector process (not a `provelog` subcommand)."""
    if sys.platform == "win32":
        return f'"{_windows_pythonw()}" -m provelog.watch_runner'
    return f'"{sys.executable}" -m provelog.watch_runner'


WATCH_AGENT_LABEL = "ai.provelog.watch"


def watch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{WATCH_AGENT_LABEL}.plist"


def _linux_systemd_user_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _systemctl_user(*args: str) -> bool:
    try:
        return subprocess.run(["systemctl", "--user", *args],
                              capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _windows_launcher_command() -> str:
    """A windowless command for the Windows Startup entry."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.is_file() else exe
    if "provelog" in runner.name.lower():
        return f'"{runner}"'
    return f'"{runner}" -m provelog'


def _windows_startup_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Startup")


def _windows_pythonw() -> Path:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return pythonw if pythonw.is_file() else exe


                                                                              
                                                                              
                                                                             
                                                                    
_WINDOWS_WATCH_FILTER = (
    "$_.Name -like 'python*' -and "
    "$_.CommandLine -like '*-m provelog.watch_runner*' -and "
    "$_.ProcessId -ne $PID"
)


def _windows_watch_query(pid: int | None = None) -> str:
    scope = (f"Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'"
             if pid is not None else "Get-CimInstance Win32_Process")
    return (f"{scope} | Where-Object {{ {_WINDOWS_WATCH_FILTER} }} | "
            "Select-Object -ExpandProperty ProcessId")


def _windows_watch_pids() -> list[int] | None:
    """PIDs of running collectors, or None when PowerShell cannot tell us."""
    try:
        out = subprocess.run([
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            _windows_watch_query(),
        ], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [int(line.strip()) for line in out.stdout.splitlines()
            if line.strip().isdigit()]


def _windows_pid_is_watch_runner(pid: int) -> bool | None:
    """Confirm one PID is still a Python collector before killing it.

    None means the confirmation query failed (do not kill); False means the PID
    is gone or is some other process (do not kill).
    """
    try:
        out = subprocess.run([
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            _windows_watch_query(pid),
        ], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return any(line.strip() == str(pid) for line in out.stdout.splitlines())


def stop_watch_process() -> bool:
    """Stop the Windows collector now. False means none was running.

    uninstall() must not leave a recorder writing after the user removed the
    autostart: on Windows the Run key only controls the NEXT login.
    """
    if sys.platform != "win32":
        return False
    pids = _windows_watch_pids()
    if pids is None:
        raise OSError("could not enumerate the collector process")
    if not pids:
        return False
    stopped = False
    for pid in pids:
        valid = _windows_pid_is_watch_runner(pid)
        if valid is None:
            raise OSError(f"could not validate collector PID {pid}; not killing it")
        if not valid:
            continue
        try:
            out = subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                                 capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.SubprocessError) as exc:
            raise OSError(f"could not stop collector PID {pid}: {exc}") from exc
        if out.returncode != 0:
                                                                              
                                                              
            remaining = _windows_watch_pids()
            if remaining is not None and pid not in remaining:
                stopped = True                                                 
                continue
            raise OSError(f"taskkill failed for collector PID {pid}: "
                          f"{(out.stderr or out.stdout).strip()}")
        stopped = True
    return stopped


def watch_process_running() -> bool | None:
    """Whether the internal collector process is visible.

    None means this platform cannot tell us; callers must not turn that into
    "not running". A stale pre-0.2 install can still show the old command name.
    """
    if sys.platform == "win32":
        pids = _windows_watch_pids()
        return None if pids is None else bool(pids)
    if sys.platform not in ("darwin", "linux"):
        return None
    try:
        out = subprocess.run(["ps", "-ax", "-o", "command="],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    text = out.stdout
    return ("provelog.watch_runner" in text
            or "provelog watch --interval" in text)


def watch_agent_installed() -> bool:
    """Whether this platform's login entry for the internal collector exists.

    `watch_agent_path()` is the macOS LaunchAgent path; Linux and Windows use
    a systemd unit and a Startup script, so `status` must ask this helper
    instead of testing the macOS path on every platform.
    """
    if sys.platform == "darwin":
        return watch_agent_path().is_file()
    if sys.platform.startswith("linux"):
        return (_linux_systemd_user_dir() / f"{WATCH_AGENT_LABEL}.service").is_file()
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, WATCH_AGENT_LABEL)
                return True
        except (ImportError, OSError):
            pass
        return (_windows_startup_dir() / f"{WATCH_AGENT_LABEL}.vbs").is_file()
    return False


def _linux_watch_unit(interval: int) -> Path:
    unit = _linux_systemd_user_dir() / "ai.provelog.watch.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    env = ""
    if not shutil.which("provelog"):
        env = f'Environment=PYTHONPATH={Path(__file__).resolve().parents[1]}\n'
    unit.write_text(f"""[Unit]
Description=ProveLog watch collector
After=default.target

[Service]
Type=simple
ExecStart={_watch_command()} --interval {int(interval)}
Restart=always
RestartSec=5
{env}
[Install]
WantedBy=default.target
""", encoding="utf-8")
    return unit


def install_watch_agent(interval: int = 10) -> str:
    """Run the internal collector at login and keep it alive (read-only collector)."""
    if sys.platform == "darwin":
        root().mkdir(parents=True, exist_ok=True)
        logs_dir().mkdir(parents=True, exist_ok=True)
        script = root() / "watch.sh"
        lines = ["#!/bin/sh", "# Managed by provelog; remove with `provelog uninstall`."]
        if not shutil.which("provelog"):
            lines.append(f'export PYTHONPATH="{Path(__file__).resolve().parents[1]}"')
        lines.append(f'exec {_watch_command()} --interval {int(interval)}')
        script.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
        try:
            os.chmod(script, 0o700)
        except OSError:
            pass
        agent = watch_agent_path()
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{WATCH_AGENT_LABEL}</string>
  <key>ProgramArguments</key><array>
    <string>/bin/sh</string><string>{script}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{logs_dir() / 'watch.log'}</string>
  <key>StandardErrorPath</key><string>{logs_dir() / 'watch.log'}</string>
</dict></plist>
""", encoding="utf-8")
        _launchctl("bootout", f"gui/{os.getuid()}/{WATCH_AGENT_LABEL}")
                                                                              
                                                                         
        for _ in range(3):
            time.sleep(0.3)
            if _launchctl("bootstrap", f"gui/{os.getuid()}", str(agent)):
                return str(agent)
        return ""
    if sys.platform.startswith("linux"):
        unit = _linux_watch_unit(interval)
        _systemctl_user("daemon-reload")
        _systemctl_user("enable", "--now", "ai.provelog.watch.service")
        return str(unit)
    if sys.platform == "win32":
        logs_dir().mkdir(parents=True, exist_ok=True)
        _windows_set_run_value(f'{_watch_command()} --interval {int(interval)}')
                                                                         
        (_windows_startup_dir() / f"{WATCH_AGENT_LABEL}.vbs").unlink(missing_ok=True)
        _windows_install_launcher()
        _windows_start_watch(interval)
        return ("HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\"
                + WATCH_AGENT_LABEL)
    return ""


def _windows_run_key():
    import winreg
    return winreg, winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"


def _windows_set_run_value(value: str) -> bool:
    try:
        winreg, hive, path = _windows_run_key()
        with winreg.CreateKey(hive, path) as key:
            winreg.SetValueEx(key, WATCH_AGENT_LABEL, 0, winreg.REG_SZ, value)
        return True
    except (ImportError, OSError):
        return False


def _windows_launcher_path() -> Path:
    return _windows_startup_dir().parent / "ProveLog.cmd"


def _windows_install_launcher() -> None:
    """A Start Menu .cmd, not an executable: double-click opens the viewer."""
    _windows_startup_dir().parent.mkdir(parents=True, exist_ok=True)
    _windows_launcher_path().write_text(
        "@echo off\r\n"
        f'start "" "{_windows_pythonw()}" -m provelog browse\r\n',
        encoding="utf-8",
    )


def _windows_start_watch(interval: int) -> None:
    """Start the collector now; log its output, and try to leave a Job Object."""
    logs_dir().mkdir(parents=True, exist_ok=True)
    log = logs_dir() / "watch.log"
    args = [str(_windows_pythonw()), "-m", "provelog.watch_runner",
            "--interval", str(int(interval))]
                                                                     
    for flags in (0x08000000 | 0x00000008 | 0x01000000,
                  0x08000000 | 0x00000008):
        try:
            with open(log, "ab") as out:
                subprocess.Popen(args, creationflags=flags, close_fds=True,
                                 stdout=out, stderr=subprocess.STDOUT)
            return
        except OSError:
            continue


def remove_watch_agent() -> str:
    if sys.platform == "darwin":
        agent = watch_agent_path()
        _launchctl("bootout", f"gui/{os.getuid()}/{WATCH_AGENT_LABEL}")
        removed = ""
        if agent.is_file():
            agent.unlink()
            removed = str(agent)
        script = root() / "watch.sh"
        if script.is_file():
            script.unlink()
        return removed
    if sys.platform.startswith("linux"):
        _systemctl_user("disable", "--now", "ai.provelog.watch.service")
        unit = _linux_systemd_user_dir() / "ai.provelog.watch.service"
        removed = str(unit) if unit.is_file() else ""
        unit.unlink(missing_ok=True)
        _systemctl_user("daemon-reload")
        return removed
    if sys.platform == "win32":
        removed = ""
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run",
                                0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, WATCH_AGENT_LABEL)
                removed = "HKCU Run / " + WATCH_AGENT_LABEL
        except (ImportError, OSError):
            pass
        script = _windows_startup_dir() / "ai.provelog.watch.vbs"
        if script.is_file():
            script.unlink()
            removed = str(script)
        _windows_launcher_path().unlink(missing_ok=True)
        return removed
    return ""


                                                                         
                                                                          
LEGACY_VIEWER_AGENT_LABEL = "ai.provelog.viewer"


def legacy_viewer_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LEGACY_VIEWER_AGENT_LABEL}.plist"


def remove_legacy_viewer_agent() -> str:
    """Remove the on-demand viewer service an earlier build installed (macOS)."""
    agent = legacy_viewer_agent_path()
    if sys.platform == "darwin" and agent.is_file():
        _launchctl("bootout", f"gui/{os.getuid()}/{LEGACY_VIEWER_AGENT_LABEL}")
    removed = ""
    if agent.is_file():
        agent.unlink()
        removed = str(agent)
    (root() / "viewer.sh").unlink(missing_ok=True)
    (root() / "viewer-port").unlink(missing_ok=True)
    return removed


def _launchctl(*args: str) -> bool:
    try:
        return subprocess.run(["launchctl", *args], capture_output=True,
                              timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


def baseline_path() -> Path:
    return root() / "collector.json"


def install_baseline() -> dict:
    """Remember WHEN the product was installed.

    Collection is time-based: every event the host recorded after this moment
    is captured, including the remainder of a conversation that was already
    open. Anything earlier is never replayed.
    """
    if baseline_path().is_file():
        try:
            return json.loads(baseline_path().read_text(encoding="utf-8"))
        except ValueError:
            pass
    now_ms = int(time.time() * 1000)
    data = {"installed_at_ms": now_ms, "installed_at": _now()}
    root().mkdir(parents=True, exist_ok=True)
    baseline_path().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def baseline_ms() -> int | None:
    try:
        data = json.loads(baseline_path().read_text(encoding="utf-8"))
        value = data.get("installed_at_ms")
        return int(value) if value is not None else None
    except (OSError, ValueError):
        return None


def _has_rfc3161() -> bool:
    """Whether an RFC3161 decoder can run here (native or pure fallback)."""
    try:
        from .tsa_backend import probe
        return probe()[0] != "unavailable"
    except Exception:
        from importlib.util import find_spec
        try:
            return find_spec("rfc3161_client") is not None
        except (ImportError, ValueError):
            return False


def platform_support() -> dict[str, str]:
    """The setup-time architecture check.

    Windows ARM64 is not a supported native target because the Rust
    ``rfc3161_client`` extension has no win_arm64 wheel. An x64 Python running
    under Windows' x64 emulation reports AMD64 and passes this check; setup
    cannot switch interpreters after the fact.
    """
    if sys.platform.startswith("win") and platform.machine().lower() in (
            "arm64", "aarch64"):
        if not _has_rfc3161():
            return {
                "state": "unsupported",
                "message": (
                    "Native Windows ARM64 is not supported: rfc3161-client has no "
                    "win_arm64 wheel. Install the x64 Python build, then run "
                    "`pip install provelog` and `provelog setup` with that interpreter."
                ),
            }
        return {
            "state": "experimental",
            "message": (
                "Native Windows ARM64 detected. rfc3161-client was not installed "
                "from an official win_arm64 wheel; continuing experimentally. "
                "The supported path is x64 Python."
            ),
        }
    return {"state": "ok"}


def setup() -> dict:
    """Make recording work with one command.

    Installs the background collector that reads the hosts' own session logs.
    No proxy, no environment variables, no host settings - nothing that can
    break a logged-in host.
    """
    support = platform_support()
    if support["state"] == "unsupported":
        return {"error": "unsupported_platform",
                "platform": support, "message": support["message"]}
                                                                               
                                                                               
                                                                             
                                                                     
    try:
        stop_watch_process()
    except OSError:
        pass
                                                                           
                                             
    baseline = install_baseline()
    agent = install_watch_agent()
    try:
        remove_legacy_viewer_agent()
    except OSError:
        pass                                                    
    from . import (
        import_antigravity,
        import_claude_code,
        import_cline,
        import_codebuddy,
        import_codebuddy_code,
        import_codex,
        import_copilot,
        import_cursor,
        import_doubao,
        import_doubao_app,
        import_dsh,
        import_gemini,
        import_hermes,
        import_kimi,
        import_minimax,
        import_omp,
        import_openclaw,
        import_opencode,
        import_pi,
        import_qoder,
        import_vscode_agent,
        import_workbuddy,
    )
    files = import_dsh.session_files(import_dsh.dsh_home())
    workbuddy = import_workbuddy.session_files(
        import_workbuddy.workbuddy_home())
    kimi = [p for home in import_kimi.kimi_homes()
            for p in import_kimi.session_files(home)]
    qoder = 0
    for _host, db in import_qoder.qoder_databases():
        try:
            qoder += len(import_qoder._session_rows(db))
        except Exception:
            pass
    minimax = 0
    for db in import_minimax.minimax_databases():
        try:
            minimax += len(import_minimax._sessions(db))
        except Exception:
            pass
    doubao = len(import_doubao.session_files())
    doubao_app = import_doubao_app.conversation_count()
    codebuddy = len(import_codebuddy.conversation_indexes())
    codebuddy_code = len(import_codebuddy_code.session_files())
    vscode_counts = import_vscode_agent.counts_by_host()
    pi = len(import_pi.session_files())
    omp = len(import_omp.session_files())
    opencode = sum(len(import_opencode._sessions(db))
                   for _host, db in import_opencode.databases())
    cline = len(import_cline.session_dirs())
    hermes = sum(len(import_hermes._sessions(db))
                 for db in import_hermes.hermes_databases())
    openclaw = sum(len(import_openclaw._sessions(db))
                   for db in import_openclaw.databases())
    gemini = len(import_gemini.session_files())
    qwen = len(import_gemini.qwen_session_files())
    antigravity = len(import_antigravity.session_files())
    copilot = len(import_copilot.session_dirs())
    cursor_db = import_cursor.database()
    cursor = len(import_cursor._sessions(cursor_db)) if cursor_db.is_file() else 0
    claude_sessions = len(import_claude_code.session_files())
    codex_sessions = len(import_codex.session_files())
    claude = import_claude_code.claude_root().is_dir()
    codex = import_codex.codex_root().is_dir()

                                                                         
                                                                              
                                                                             
                                                                
    def _detected(value) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value > 0
        return bool(value)

    vscode = import_vscode_agent.counts_by_host()
    detected = (
        ("Claude Code", claude_sessions or claude),
        ("Codex", codex_sessions or codex),
        ("Hermes", hermes),
        ("DeepSeek Harness", files),
        ("Kilo", vscode.get("kilo", 0)),
        ("pi", pi),
        ("OpenClaw", openclaw),
        ("WorkBuddy", workbuddy),
        ("CodeBuddy", codebuddy or codebuddy_code),
        ("Qoder", qoder),
        ("Gemini CLI", gemini),
        ("GitHub Copilot CLI", copilot),
        ("Cline", cline or vscode.get("cline", 0)),
        ("Roo Code", vscode.get("roo", 0)),
        ("OpenCode", opencode),
        ("Qwen Code", qwen),
        ("Kimi Code", kimi),
        ("omp", omp),
        ("MiniMax Code", minimax),
        ("Doubao Work", doubao),
        ("Doubao", doubao_app),
    )
    supported_agents = [label for label, value in detected if _detected(value)]

                                                                            
                                                                              
                                                                             
    unsupported_agents: list[str] = []
    if _detected(cursor):
        unsupported_agents.append("Cursor")
    if _detected(antigravity):
        unsupported_agents.append("Antigravity")
    trae_markers = [
        Path.home() / ".trae",
        Path.home() / ".trae-cn",
        Path.home() / "Library" / "Application Support" / "Trae",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        trae_markers.append(Path(appdata) / "Trae")
    if any(marker.is_dir() for marker in trae_markers):
        unsupported_agents.append("Trae")

    return {"mode": "watch", "watch": agent,
            "supported_agents": supported_agents,
            "unsupported_agents": unsupported_agents,
            "platform": support,
            "dsh_sessions": len(files),
            "workbuddy_sessions": len(workbuddy),
            "kimi_sessions": len(kimi),
            "qoder_sessions": qoder,
            "minimax_sessions": minimax,
            "doubao_sessions": doubao,
            "doubao_app_sessions": doubao_app,
            "codebuddy_sessions": codebuddy,
            "pi_sessions": pi,
            "omp_sessions": omp,
            "opencode_sessions": opencode,
            "cline_sessions": cline,
            "hermes_sessions": hermes,
            "openclaw_sessions": openclaw,
            "gemini_sessions": gemini,
            "qwen_sessions": qwen,
            "antigravity_sessions": antigravity,
            "copilot_sessions": copilot,
            "cursor_sessions": cursor,
            "codebuddy_code_sessions": codebuddy_code,
            "cline_vscode_sessions": vscode_counts.get("cline", 0),
            "kilo_vscode_sessions": vscode_counts.get("kilo", 0),
            "roo_sessions": vscode_counts.get("roo", 0),
            "claude_code_sessions": claude_sessions,
            "codex_sessions": codex_sessions,
            "baseline_ms": baseline.get("installed_at_ms"),
            "claude_code": claude, "codex": codex}


def uninstall() -> dict:
    watch = ""
    viewer = ""
    stopped = False
    errors: list[dict[str, str]] = []
    try:
        stopped = stop_watch_process()
    except Exception as exc:                                    
        errors.append({"item": "collector process", "reason": str(exc)})
    try:
        watch = remove_watch_agent()
    except Exception as exc:                                       
        errors.append({"item": "collector service file", "reason": str(exc)})
    try:
        viewer = remove_legacy_viewer_agent()
    except Exception as exc:
        errors.append({"item": "viewer service file", "reason": str(exc)})
    return {"watch": watch, "viewer": viewer,
            "stopped": stopped, "errors": errors}
