"""Where the viewer saves exported packs.

A web page cannot open a "save to..." folder dialog in every browser (Firefox
and Safari have no such API), so the local viewer writes the pack itself: a
fixed default folder next to the records, which the user can change through
the operating system's own folder chooser. The choice is remembered.

Nothing here touches the records: exports are written outside the store.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_NAME = "ProveLog Exports"
_CHOOSE_TIMEOUT_S = 300


def default_dir(home: Path) -> Path:
    return home / DEFAULT_NAME


def _setting(home: Path) -> Path:
    return home / ".provelog" / "export-dir"


def current(home: Path) -> Path:
    """The remembered folder if it still exists, otherwise the default."""
    try:
        text = _setting(home).read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    if text:
        chosen = Path(text)
        if chosen.is_absolute() and chosen.is_dir():
            return chosen
    return default_dir(home)


def remember(home: Path, value: str) -> Path:
    """Set the export folder. An empty value goes back to the default.

    Raises ValueError with a user-facing reason when the folder cannot be used.
    """
    setting = _setting(home)
    if not str(value or "").strip():
        try:
            setting.unlink()
        except OSError:
            pass
        return default_dir(home)
    chosen = Path(str(value).strip()).expanduser()
    if not chosen.is_absolute():
        raise ValueError("Use a full path.")
    try:
        chosen = chosen.resolve()
    except OSError as exc:
        raise ValueError(str(exc)) from None
    if not chosen.is_dir():
        raise ValueError("That folder does not exist.")
    if not os.access(chosen, os.W_OK):
        raise ValueError("That folder is not writable.")
    setting.parent.mkdir(parents=True, exist_ok=True)
    setting.write_text(str(chosen) + "\n", encoding="utf-8")
    return chosen


def choose_folder(start: Path, prompt: str = "Choose where ProveLog saves exports"
                  ) -> tuple[str | None, bool]:
    """Open the system folder chooser. Returns (path or None, supported).

    ``(None, True)`` means the user cancelled; ``(None, False)`` means this
    system has no chooser we can call, and the page falls back to typing a path.
    """
    begin = start if start.is_dir() else start.parent if start.parent.is_dir() else Path.home()
    try:
        if sys.platform == "darwin":
                                                                             
                                                                     
                                                                             
            script = (
                "function run(argv){"
                "var a=Application.currentApplication();a.includeStandardAdditions=true;"
                "a.activate();"
                "return a.chooseFolder({withPrompt:argv[0],defaultLocation:Path(argv[1])}).toString();}"
            )
            run = subprocess.run(["osascript", "-l", "JavaScript", "-e", script, prompt, str(begin)],
                                 capture_output=True, text=True, timeout=_CHOOSE_TIMEOUT_S)
            if run.returncode != 0:
                                                                                 
                return None, "-128" in (run.stderr or "") or "canceled" in (run.stderr or "").lower()
            return (run.stdout.strip() or None), True
        if sys.platform.startswith("win"):
            script = (  # noqa: UP031
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
                "$d.Description = '%s'; $d.SelectedPath = '%s';"
                "if ($d.ShowDialog() -eq 'OK') { [Console]::Out.Write($d.SelectedPath) }"
            ) % (prompt.replace("'", "''"), str(begin).replace("'", "''"))
            run = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                                 capture_output=True, text=True, timeout=_CHOOSE_TIMEOUT_S)
            if run.returncode != 0:
                return None, False
            return (run.stdout.strip() or None), True
        for tool, args in (("zenity", ["--file-selection", "--directory", "--title", prompt,
                                       "--filename", str(begin) + "/"]),
                           ("kdialog", ["--getexistingdirectory", str(begin), "--title", prompt])):
            if shutil.which(tool):
                run = subprocess.run([tool, *args], capture_output=True, text=True,
                                     timeout=_CHOOSE_TIMEOUT_S)
                return (run.stdout.strip() or None) if run.returncode == 0 else None, True
    except (OSError, subprocess.SubprocessError):
        return None, False
    return None, False
