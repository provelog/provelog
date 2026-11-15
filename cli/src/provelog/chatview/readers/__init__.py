"""Reader registry: one module per host, imported only on use.

A reader module exposes:

    discover(home: Path) -> list[SessionSummary]   # cheap, for the list
    load(home: Path, session_id: str) -> SessionView | None
    install_dir(home: Path) -> Path   # the CLI's install marker; honors the
                                      # host's official config-dir env var
                                      # (CLAUDE_CONFIG_DIR, CODEX_HOME, ...)
                                      # where one exists
    data_dir(home: Path) -> Path      # where its history lives

`home` is the user's home directory (tests point it at a fixture tree).
None of these may write anything, anywhere - readers are eyes, and the
red line is that host files are never touched in any other mode.

Adding a host: drop a module named after the host key next to this file and
add it to _READER_MODULES. The registry stays lazy - `provelog --help`
never imports a reader.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..model import SessionSummary, SessionView

                                                                       
                                                                       
                             
_READER_MODULES: dict[str, str] = {
    "claude-code": "claude_code",
    "hermes": "hermes",
    "codex": "codex",
    "gemini": "gemini",
    "kimi": "kimi",
    "opencode": "opencode",
    "kilo": "kilo",
    "zcode": "zcode",
    "openclaw": "openclaw",
}

                                                                        
                                 
_UNSUPPORTED: dict[str, str] = {
    "cursor": ("Cursor is closed source and its on-disk session format has "
               "changed more than once without documentation; guessing at it "
               "risks showing wrong data, so it is not read."),
    "codebuddy": ("CodeBuddy keeps session .jsonl files, but the line format "
                  "is undocumented (closed source); nothing is guessed. A "
                  "sample session file would let a reader be built against "
                  "reality."),
    "qoder": ("Qoder keeps session .jsonl files, but the line format is "
              "deliberately undocumented; nothing is guessed. A sample "
              "session file would let a reader be built against reality."),
    "dsh": ("dsh compresses sessions with zstd (session.*.jsonl.zstd); this "
            "build has no zstd reader (the standard library gains one in "
            "Python 3.14; this package supports 3.11+)."),
}

                                                                    
                                                                   
                                                                      
                                                                 
_UNSUPPORTED_ZH: dict[str, str] = {
    "cursor": ("Cursor is closed source; its on-disk session format has changed "
               "repeatedly and is undocumented. Guessing risks showing wrong data, "
               "so it is not read."),
    "codebuddy": ("CodeBuddy stores sessions as .jsonl files, but the line format is "
                  "undocumented (closed source); we do not guess. One sample session "
                  "file would let a reader follow reality."),
    "qoder": ("Qoder stores sessions as .jsonl files, but the line format is "
              "deliberately not public; we do not guess. One sample session file "
              "would let a reader follow reality."),
    "dsh": ("dsh stores sessions zstd-compressed (session.*.jsonl.zstd); this build "
            "has no zstd reader (the standard library only gains one in Python 3.14, "
            "and the package supports 3.11+)."),
}

                                                                            
                                                                       
                                                   
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$")

                                                                         
                                                                          
                                                                        
                                                                      
                                                                   
                                                                       
                                                                  
_INSTALL_MARKERS: dict[str, str] = {
    "cursor": ".cursor",
    "dsh": ".dsh",
    "codebuddy": ".codebuddy",
    "qoder": ".qoder",
}


def install_dir(host: str, home: Path) -> Path:
    """The directory whose existence marks the CLI as installed."""
    if is_implemented(host):
        return _module(host).install_dir(home)
    return home.joinpath(*_INSTALL_MARKERS[host].split("/"))


def is_installed(host: str, home: Path) -> bool:
    """True when the CLI's own home directory exists - installed, whether
    or not it ever recorded a conversation."""
    return install_dir(host, home).exists()


def safe_session_id(session_id: str) -> bool:
    """True when the id is safe to use as a path component. Rejects
    traversal ('..', '/'), prefixes, and overlong strings."""
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        return False
    return bool(_SAFE_ID.match(session_id))


def is_receipt_file(path: Path) -> bool:
    """True for provelog's own receipt chains, which co-locate beside the
    host's transcripts and so match the same *.jsonl globs - they are
    evidence about a session, never a session to browse."""
    return path.name.endswith(".provelog.jsonl")


def _module(host: str):
    import importlib
    name = _READER_MODULES.get(host)
    if name is None:
        raise KeyError(host)
    return importlib.import_module(f"{__name__}.{name}")


def implemented_hosts() -> list[str]:
    """Host keys with a reader wired in (sorted for stable UI)."""
    return sorted(_READER_MODULES)


def is_implemented(host: str) -> bool:
    return host in _READER_MODULES


def data_dir(host: str, home: Path) -> Path:
    """The root a host keeps its history under (display + existence
    probing; the reader itself never writes there)."""
    return _module(host).data_dir(home)


def unsupported_hosts(lang: str = "en") -> dict[str, str]:
    """Hosts this build cannot read yet -> the honest reason, shown
    instead of pretending they have no history. lang picks the reason's
    language ("en"/"zh"); anything but "zh" is English."""
    if lang == "zh":
        missing = _UNSUPPORTED.keys() - _UNSUPPORTED_ZH.keys()
        if missing:                                    
            raise KeyError(f"untranslated unsupported-host reasons: {sorted(missing)}")
        return dict(_UNSUPPORTED_ZH)
    return dict(_UNSUPPORTED)


def discover(host: str, home: Path) -> list[SessionSummary]:
    """List a host's sessions (cheap). Never raises for a missing data
    directory: an installed-but-unused host is an empty list, not an error."""
    return _module(host).discover(home)


def load(host: str, home: Path, session_id: str) -> SessionView | None:
    """Fully parse one session. None when the id is unsafe or unknown."""
    if not safe_session_id(session_id):
        return None
    return _module(host).load(home, session_id)
