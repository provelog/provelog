"""Split a UTF-8 file into lines without first building one giant string.

``path.read_text(encoding="utf-8", errors="replace").splitlines()`` decodes the
whole file into a single ``str`` before splitting it. For a multi-megabyte
log that holds any non-ASCII text that one string is 2-4 bytes per character,
and on macOS the freed block stays resident: a long-lived process that re-reads
a growing 6 MB host log gained ~23 MB per read (the watcher reached 1.6 GB in a
day). Decoding one ``\\n``-delimited piece at a time keeps every string small.

The functions here return EXACTLY the list the expression above returns -
same strings, same order, same blank entries - so callers that count lines or
remember a line index are unaffected. The test suite pins that.
"""

from __future__ import annotations

from pathlib import Path

                                                                        
_OTHER_BREAKS = "\x0b\x0c\x1c\x1d\x1e\x85  "


def split_text_lines(raw: bytes) -> list[str]:
    """``raw.decode("utf-8", "replace").splitlines()``, piece by piece.

    ``\\n`` (0x0A) is never part of a multi-byte UTF-8 sequence, so splitting
    the bytes there first cannot cut a character, and ``replace`` substitutes
    the same damaged bytes either way.
    """
    out: list[str] = []
    pieces = raw.split(b"\n")
    last = len(pieces) - 1
    for index, piece in enumerate(pieces):
        text = piece.decode("utf-8", "replace")
        lines = text.splitlines()
                                                                           
                                                                             
                                                                         
                                                                           
                                                         
        if index < last and (not lines or text[-1] in _OTHER_BREAKS):
            lines.append("")
        out.extend(lines)
    return out


def split_jsonl_lines(raw: bytes) -> list[str]:
    """The lines of a JSON Lines file: split on ``\\n`` and nothing else.

    A host writes one JSON document per line and escapes ``\\n`` (and every
    other control character) inside strings, but not U+2028, U+2029 or U+0085:
    ``JSON.stringify`` and ``ensure_ascii=False`` leave those raw. Any message
    that contains one (text pasted from a web page or a PDF often does) is a
    single valid line that ``str.splitlines`` cuts into pieces that no longer
    parse. The row was lost, and the extra pieces inflated the line count the
    importers resume from, so the rows after it were skipped as well.

    For a file without those characters this returns exactly what
    ``decode().splitlines()`` returned, apart from a ``\\r`` left at the end
    of a ``\\r\\n`` line (``json.loads`` and ``str.strip`` ignore it).
    """
    pieces = raw.split(b"\n")
    if pieces and not pieces[-1]:
        pieces.pop()                                                               
    return [piece.decode("utf-8", "replace") for piece in pieces]


def read_jsonl_lines(path: Path | str) -> list[str]:
    """``split_jsonl_lines`` of a file. Raises ``OSError`` like ``read_text``."""
    return split_jsonl_lines(Path(path).read_bytes())


def read_text_lines(path: Path | str) -> list[str]:
    """``Path(path).read_text("utf-8", errors="replace").splitlines()``.

    Raises ``OSError`` exactly where ``read_text`` would. (Text mode's newline
    translation makes no difference to the result: ``splitlines`` already
    treats ``\\r\\n`` and ``\\r`` as breaks.)
    """
    return split_text_lines(Path(path).read_bytes())
