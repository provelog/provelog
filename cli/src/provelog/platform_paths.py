"""Platform-specific application data roots.

Every host importer used to spell macOS ``~/Library/Application Support`` paths
inline.  That silently found nothing on Windows and Linux.  Keep the platform
mapping in one place so a path bug is fixed for every importer at once, while
still allowing an explicit environment override for portable installs and
tests.

The rules are deliberately conservative:

* macOS: ``~/Library/Application Support/<name>``
* Windows: ``%APPDATA%/<name>`` (Roaming for Electron user data)
* Linux: ``$XDG_CONFIG_HOME/<name>`` or ``~/.config/<name>``

OpenCode-family CLI data lives in an XDG *data* directory (``~/.local/share``
on macOS/Linux, ``%LOCALAPPDATA%`` on Windows), so it uses ``data_dir`` instead.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_windows() -> bool:
    return sys.platform == "win32"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def _home() -> Path:
    return Path.home()


def roaming_appdata() -> Path:
    """Windows ``%APPDATA%`` (Roaming), with a portable fallback."""
    return Path(os.environ.get("APPDATA") or (_home() / "AppData" / "Roaming"))


def local_appdata() -> Path:
    """Windows ``%LOCALAPPDATA%``, with a portable fallback."""
    return Path(os.environ.get("LOCALAPPDATA") or (_home() / "AppData" / "Local"))


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (_home() / ".config"))


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (_home() / ".local" / "share"))


def app_config_dir(app: str, *, mac: str | None = None,
                   windows: str | None = None,
                   linux: str | None = None) -> Path:
    """Per-user application data directory (Electron ``userData`` shape)."""
    if is_macos():
        return _home() / "Library" / "Application Support" / (mac or app)
    if is_windows():
        return roaming_appdata() / (windows or app)
    return xdg_config_home() / (linux or app)


def app_data_dir(app: str, *, mac: str | None = None,
                 windows: str | None = None,
                 linux: str | None = None) -> Path:
    """Per-user *data* directory for XDG-style CLI stores."""
    if is_macos():
        return _home() / "Library" / "Application Support" / (mac or app)
    if is_windows():
        return local_appdata() / (windows or app)
    return xdg_data_home() / (linux or app)


def vscode_user_dir(app: str) -> Path:
    """VS Code-family ``User`` directory for Code / Cursor / VSCodium."""
    return app_config_dir(app) / "User"


def vscode_global_storage_dir(app: str) -> Path:
    return vscode_user_dir(app) / "globalStorage"


def unique_paths(paths: list[Path]) -> list[Path]:
    """De-duplicate candidates while preserving order (Windows paths are
    case-insensitive, so compare casefolded strings there)."""
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).casefold() if is_windows() else str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out
