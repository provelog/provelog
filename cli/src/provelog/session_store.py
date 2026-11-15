"""The folder-canonical local store.

``~/ProveLog Records/<agent>/<title>-id-<id>/`` holds one session:

``trajectory.jsonl.zst``
    The only truth: append-only event stream, one JSON envelope per line,
    written as independently decompressible zstd frames (one frame per
    append batch). Falls back to plain ``trajectory.jsonl`` when the zstd
    binding is unavailable in this interpreter.
``transcript-preview.md``
    Derived, human-readable view. Rebuildable from the event stream with
    the internal store rebuild path.
``manifest.json``
    Small metadata: ids, counts, chain head, frame index, anchors.
``attachments/``
    Images and host-externalized large outputs.

The envelope keeps the signed record plus the local receipt chain
(``recv_seq`` / ``server_record_hash``), so anchoring leaves and the
existing verifier keep working unchanged. SQLite is not used here.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import GENESIS_PREV_HASH
from .canonical import canonicalize, sha256_hex
from .session_folder import TRANSCRIPT_NAME, SessionFolder, folder_name, sanitize_component, sessions_root


class SeqGap(Exception):
    pass

class SeqConflict(Exception):
    pass


TRAJECTORY = "trajectory.jsonl"
TRAJECTORY_ZST = "trajectory.jsonl.zst"
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_LOCK = threading.RLock()


def _frame_aligned(unused: bytes) -> bool:
    """Whether ``unused`` starts at a frame boundary (or EOF).

    ``unused`` is what a zstd decompressor leaves over after one frame. It is
    aligned when it is empty, when it starts with the next frame's magic, when
    it is a short (1-3 byte) torn tail that is a prefix of the magic, or when
    that same short residue precedes the next frame's magic. Anything else
    means the frame we just decoded bled into the frame after it.
    """
    if not unused:
        return True
    if unused.startswith(_ZSTD_MAGIC):
        return True
    if len(unused) < len(_ZSTD_MAGIC) and _ZSTD_MAGIC.startswith(unused):
        return True
    idx = unused.find(_ZSTD_MAGIC)
    return idx in (1, 2, 3) and _ZSTD_MAGIC.startswith(unused[:idx])


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _age_s(stamp: Any) -> float:
    """Seconds since a ``_now()`` timestamp; ``inf`` when it cannot be parsed."""
    try:
        parsed = datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return float("inf")
    return (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()


class _Codec:
    """The optional zstd framing layer.

    Frames are independent so appends never rewrite history and a truncated
    tail loses only the last batch. The codec is injectable for tests
    (``session_store._CODEC = Fake()``) and degrades to plain JSONL when the
    ``zstandard`` binding cannot be imported.
    """

    def __init__(self) -> None:
        try:
            import zstandard as zstd  # noqa: PLC0415 - optional at import time
        except Exception:                 # pragma: no cover - depends on env
            zstd = None
        self._zstd = zstd

    @property
    def available(self) -> bool:
        return self._zstd is not None

    def compress(self, raw: bytes) -> bytes:
        if self._zstd is None:
            raise RuntimeError("zstd codec is not available")
        return self._zstd.ZstdCompressor(level=9).compress(raw)

    def decompress_all(self, data: bytes) -> bytes:
        """Decode every concatenated frame, skipping any damaged frame.

        Kept as a bytes-only convenience for callers and for the injectable
        FakeCodec used by tests; ``decompress_frames`` is the same work with
        per-frame diagnostics.
        """
        return self.decompress_frames(data)[0]

    def decompress_frames(self, data: bytes) -> tuple[bytes, list[dict[str, Any]]]:
        """Decode each complete zstd frame; skip damaged ones and keep going.

        Returns ``(payload, issues)``. A truncated or corrupt frame is dropped
        (with an ``issues`` entry) and scanning resumes at the next frame
        magic, so a crash mid-write - or a torn middle frame - loses only that
        frame, never every frame after it. ``issues`` entries carry ``offset``
        and ``reason`` so `doctor` can report the damage instead of the reader
        silently returning nothing.
        """
        if self._zstd is None:
            raise RuntimeError("zstd codec is not available")
        chunks: list[bytes] = []
        issues: list[dict[str, Any]] = []
        n = len(data)
        i = 0
        while True:
            j = data.find(_ZSTD_MAGIC, i)
            if j < 0:
                break
            if j > i:
                issues.append({"offset": i, "bytes": j - i,
                               "reason": "non_frame_bytes"})
            frame = self._decompress_one_frame(data, j)
            if frame is None:
                issues.append({"offset": j, "reason": "damaged_frame"})
                i = j + 1
                continue
            out, consumed = frame
            chunks.append(out)
            i = j + consumed
        if i < n:
            issues.append({"offset": i, "bytes": n - i, "reason": "truncated_tail"})
        return b"".join(chunks), issues

    def _decompress_one_frame(self, data: bytes, start: int) -> tuple[bytes, int] | None:
        """Decompress exactly one frame beginning at ``start``.

        Returns ``(payload, consumed)``, or ``None`` when the bytes at ``start``
        are not a complete, intact frame. The critical guard is the alignment
        check: a *truncated* frame's block read bleeds into the frame after it,
        so the decompressor reports success with ``unused_data`` that does not
        start at a frame magic. That is a torn frame, not a real boundary, and
        it must be skipped rather than trusted.

        Fast path first: ``decompressobj().decompress()`` in python-zstandard's
        C backend (seen on 0.25.0) never releases its output buffer, so a
        long-lived reader (the watcher, the chat viewer) grew by the size of
        every trajectory it read. The one-shot ``decompress()`` does not leak
        but needs the frame's exact bytes, so the frame's compressed size is
        read from its block headers. The fast path is only trusted when it
        positively succeeds - exact frame, intact payload, aligned boundary;
        anything else (a torn or damaged frame, an unusual header) falls through
        to the streaming reader below, whose verdict is unchanged.
        """
        fast = self._decompress_exact_frame(data, start)
        if fast is not None:
            return fast
        obj = self._zstd.ZstdDecompressor().decompressobj()
        try:
            out = obj.decompress(data[start:])
        except Exception:
            return None
        if not getattr(obj, "eof", False):
            return None
        unused = obj.unused_data
                                                                           
                                                                           
                                                                             
                                                                               
                                                                             
                                                                              
                                               
        if unused and not _frame_aligned(unused):
            return None
        consumed = (len(data) - start) - len(unused)
        if consumed <= 0:
            return None
        return out, consumed

    def _decompress_exact_frame(self, data: bytes, start: int) -> tuple[bytes, int] | None:
        """One-shot decode of the frame at ``start``; ``None`` means "not sure".

        ``None`` is never a verdict on the frame: the caller then asks the
        streaming reader, exactly as before this path existed.
        """
        size = _zstd_frame_size(data, start)
        if not size:
            return None
        frame = data[start:start + size]
        try:
            declared = self._zstd.frame_content_size(frame)
                                                                               
                                                                         
            if not 0 <= declared <= _MAX_ONE_SHOT_FRAME:
                return None
            out = self._zstd.ZstdDecompressor().decompress(
                frame, allow_extra_data=False)
        except Exception:
            return None
        if len(out) != declared:
            return None
        end = start + size
                                                                              
                                                                              
        if not _frame_aligned(data[end:end + 2 * len(_ZSTD_MAGIC)]):
            return None
        return out, size


                                                                         
                                      
_MAX_ONE_SHOT_FRAME = 1 << 30


def _zstd_frame_size(data: bytes, start: int) -> int | None:
    """Compressed size of the zstd frame at ``start``, read from its headers.

    Walks the frame header and each block header (RFC 8878 section 3.1.1)
    without decompressing anything. Returns ``None`` when the bytes are not a
    complete, well-formed frame - the caller treats that as "unknown".
    """
    n = len(data)
    pos = start + len(_ZSTD_MAGIC)
    if pos >= n:
        return None
    descriptor = data[pos]
    pos += 1
    if descriptor & 0x08:                                                  
        return None
    single_segment = bool(descriptor & 0x20)
    if not single_segment:
        pos += 1                                                   
    pos += (0, 1, 2, 4)[descriptor & 0x03]                     
    pos += (1 if single_segment else 0, 2, 4, 8)[descriptor >> 6]                
    while True:
        if pos + 3 > n:
            return None
        header = data[pos] | (data[pos + 1] << 8) | (data[pos + 2] << 16)
        pos += 3
        block_type = (header >> 1) & 0x03
        if block_type == 3:                               
            return None
        pos += 1 if block_type == 1 else header >> 3                        
        if pos > n:
            return None
        if header & 0x01:                                   
            break
    if descriptor & 0x04:
        pos += 4                                                  
    return pos - start if pos <= n else None


_CODEC = _Codec()


def _pretty(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _content(payload: dict[str, Any], name: str) -> Any:
    v = payload.get(name)
    if isinstance(v, dict) and "content" in v:
        return v["content"]
    return v


def own_stream_id(manifest: dict[str, Any]) -> str:
    """The stream name of THIS conversation (never a subagent's).

    One conversation = one stream, so a folder that also holds a
    subagent's records still knows which stream is the conversation's own.
    Older manifests have no ``session_stream``; the stream naming convention
    (``local-<agent>-<id>``) is the fallback, and a folder with a single
    stream is right by construction.
    """
    streams = manifest.get("streams") or {}
    key = str(manifest.get("session_stream") or "")
    if key and key in streams:
        return key
    sid = str(manifest.get("session_id") or "")
    agent = str(manifest.get("agent") or "")
    bare = _bare_id(sid)
    for candidate in (f"local-{agent}-{bare}", f"local-{agent}-{sid}"):
        if candidate in streams:
            return candidate
    for name in streams:
        if bare and str(name).endswith(bare):
            return name
    if len(streams) == 1:
        return next(iter(streams))
    return ""


def own_stream(manifest: dict[str, Any]) -> dict[str, Any]:
    """This conversation's stream row (see own_stream_id)."""
    return (manifest.get("streams") or {}).get(own_stream_id(manifest)) or {}


                                                                           
                                                                               
                                                              
MAX_ANCHORS_IN_MANIFEST = 20


def _bare_id(session_id: str) -> str:
    """The id without the harness prefix (``session-`` / ``sess_``).

    DSH names a parent session ``session-<uuid>`` but a forked child just
    ``<uuid>``, and the folder name uses the bare form.
    """
    raw = str(session_id or "")
    for prefix in ("session-", "session_", "sess-", "sess_"):
        if raw.lower().startswith(prefix) and len(raw) > len(prefix) + 4:
            return raw[len(prefix):]
    return raw


def _trajectory_path(folder: Path) -> Path | None:
    """The event file that exists in this folder, preferring the framed one."""
    zst = folder / TRAJECTORY_ZST
    if zst.is_file():
        return zst
    plain = folder / TRAJECTORY
    if plain.is_file():
        return plain
    return None


def _read_trajectory_with_issues(folder: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Envelopes plus frame issues for a session folder.

    ``issues`` is non-empty only when the framed file has damaged bytes; a
    clean file returns ``([...], [])``. The reader keeps every intact frame:
    a torn tail or a corrupt middle frame drops only that frame, never the
    valid frames before and after it.
    """
    path = _trajectory_path(folder)
    if path is None:
        return [], []
    try:
        raw = path.read_bytes()
    except OSError:
        return [], []
    issues: list[dict[str, Any]] = []
    if path.suffix == ".zst":
        dec = getattr(_CODEC, "decompress_frames", None)
        try:
            if dec is not None:
                raw, issues = dec(raw)
            else:                                                             
                raw = _CODEC.decompress_all(raw)
        except Exception:
            return [], []
    out: list[dict[str, Any]] = []
    for line in _text_lines(raw):
        if not line.strip():
            continue
        try:
            env = json.loads(line)
        except ValueError:
                                                                              
                                                                              
                                                                            
            continue
        if isinstance(env, dict) and isinstance(env.get("record"), dict):
            out.append(env)
    return out, issues


def _text_lines(raw: bytes):
    """The trajectory's lines: split on ``\n`` only, one small string at a time.

    Each record is one JSON document followed by ``\n`` (``_append_frame``).
    The envelope is written with ``ensure_ascii=False``, so a message that
    contains U+2028, U+2029 or U+0085 carries that character raw inside its
    line. ``str.splitlines`` treats those as line breaks: it cut such a record
    into pieces that no longer parse, the reader dropped it, and the chain
    looked broken at that point. JSON escapes ``\n`` (and every other control
    character) inside strings, so ``\n`` is the only real separator.

    Splitting the bytes first also keeps every string small. Decoding a whole
    trajectory into one string needs a single allocation several times the
    file's size, and on macOS those freed blocks stay resident: a long-lived
    reader measured +40-80 MB per read of a 21 MB trajectory. ``\n`` is never
    part of a multi-byte UTF-8 sequence, so per-piece decoding is exact.
    """
    for chunk in raw.split(b"\n"):
        yield chunk.decode("utf-8", "replace")


def _read_envelopes(folder: Path) -> list[dict[str, Any]]:
    """Every envelope in a session, in append order (issues are discarded)."""
    return _read_trajectory_with_issues(folder)[0]


def _envelope_stream(env: dict[str, Any]) -> str:
    """The stream a stored event belongs to (envelope first, record fallback)."""
    return str(env.get("stream_id")
               or (env.get("record") or {}).get("stream_id") or "")


def _append_frame(folder: Path, lines: list[str]) -> tuple[int, int]:
    """Append one batch as ONE frame. Returns (byte offset, event count).

    The file is opened in append mode, so history is never rewritten. When
    the codec is unavailable (or the folder already holds plain JSONL), the
    batch is appended as plain lines - the same framing, just uncompressed.
    """
    if not lines:
        return 0, 0
    zst = folder / TRAJECTORY_ZST
    plain = folder / TRAJECTORY
    if zst.is_file() and plain.is_file():
        raise RuntimeError(
            f"{folder} holds both {TRAJECTORY_ZST} and {TRAJECTORY}; refusing to "
            "append where the read path would hide one of them")
    if zst.is_file() and not _CODEC.available:
        raise RuntimeError(
            f"{folder} is zstd-framed but this interpreter cannot load the zstd "
            "binding; install it instead of appending a second, plain event file")
    use_zstd = _CODEC.available
    path = zst if use_zstd else plain
    payload_text = "\n".join(lines) + "\n"
    raw = payload_text.encode("utf-8")
    data = _CODEC.compress(raw) if use_zstd else raw
    offset = path.stat().st_size if path.is_file() else 0
    with path.open("ab") as fh:
        fh.write(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return offset, len(lines)


TRANSCRIPT_NOTE = ("This is a preview of the conversation only. "
                   "The complete session record is `trajectory.jsonl.zst`")

from .capture import INJECTED_PREFIXES  # noqa: E402 - kept after the preview constant


def _text_of(value: Any) -> str:
    """Plain text from a content value (blocks -> their text, no JSON noise)."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        out = []
        for block in value:
            if isinstance(block, str):
                out.append(block)
            elif isinstance(block, dict):
                kind = block.get("type")
                if kind in ("text", "thinking", "reasoning"):
                    out.append(str(block.get("text") or ""))
                elif kind == "tool_use":
                    out.append(f"-> {block.get('name', '')}")
                elif kind == "tool_result":
                    out.append(_text_of(block.get("content")))
        return "\n".join(x for x in out if x)
    return _pretty(value)


def _is_injected(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in INJECTED_PREFIXES)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _render_transcript(envs: list[dict[str, Any]], title: str,
                       child_chains: frozenset[str] = frozenset()) -> str:
    """The human view: Input / Steps / Output, one block per turn.

    Records of a subagent/child session are left out: they are evidence in the
    trajectory, but in the preview they would read as the user's own turns
    (the parent already shows the subagent call that produced them).
    """
    head_omitted = False
    for env in envs:
        record = env.get("record") or {}
        if record.get("action_type") == "lifecycle.session_start":
            head_omitted = bool((record.get("payload") or {}).get("meta", {})
                                .get("head_omitted"))
            break
    groups: list[dict[str, Any]] = []
    for env in envs:
        record = env.get("record") or {}
        if child_chains and str(record.get("session_id") or "") in child_chains:
            continue
        action = record.get("action_type") or ""
        payload = record.get("payload") or {}
        if action == "user_message":
            body = _text_of(payload.get("content")).strip()
            if not body or _is_injected(body):
                continue
            groups.append({"input": body, "output": [], "calls": 0, "reasoning": 0})
            continue
        if action == "assistant_message":
            texts, reasoning = [], 0
            blocks = payload.get("content")
            if isinstance(blocks, str):
                if blocks.strip():
                    texts.append(blocks)
            elif isinstance(blocks, list):
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    kind = block.get("type")
                    if kind in ("thinking", "reasoning"):
                        reasoning += len(str(block.get("text") or ""))
                    elif kind == "text":
                        value = str(block.get("text") or "")
                        if value.strip():
                            texts.append(value)
            if not groups:
                groups.append({"input": "", "output": [], "calls": 0, "reasoning": 0})
            groups[-1]["reasoning"] += reasoning
            groups[-1]["output"].extend(texts)
            continue
        if action == "tool_call":
            if not groups:
                groups.append({"input": "", "output": [], "calls": 0, "reasoning": 0})
            groups[-1]["calls"] += 1

    lines = [TRANSCRIPT_NOTE, "", f"# {title}", ""]
    if head_omitted:
        lines += ["_This session started before ProveLog was installed. "
                  "The conversation below begins at the installation point; "
                  "earlier messages are not recorded._", ""]
    for group in groups:
        body = group["input"].strip()
        outputs = [x.strip() for x in group["output"] if x.strip()]
        if not body and not outputs and not group["calls"] and not group["reasoning"]:
            continue
        if body:
            lines += ["**Input**", "", body, ""]
        else:
            lines += ["**Input**", "",
                      "(not recorded - this part of the conversation happened "
                      "before ProveLog was installed)", ""]
        bits = []
        if group["calls"]:
            bits.append(_plural(group["calls"], "tool call"))
        if group["reasoning"]:
            bits.append(f"{group['reasoning']:,} chars of reasoning")
        if bits:
                                                                              
                                                             
            sep = " \u00b7 "
            lines += [f"**Steps** ({sep.join(bits)})", ""]
        if outputs:
            lines += ["**Output**", "", "\n\n".join(outputs), ""]
    return "\n".join(lines).rstrip() + "\n"


def _lock_path(root: Path, stream_id: str) -> Path:
    locks = root / ".internal" / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(locks, 0o700)
    except OSError:
        pass
    name = sanitize_component(stream_id, max_len=80, fallback="stream")
    return locks / (name + ".lock")


@contextmanager
def _exclusive(path: Path) -> Iterator[None]:
    f = path.open("a+")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


def capture_tier_for(capture_point: Any) -> str:
    """How close the recorder was to the event: live, imported, or unknown."""
    point = str(capture_point or "").strip().lower()
                                                                  
    if point in {"sidecar", "hook", "live", "wire", "sdk"}:
        return "captured"
    if point in {"host_import", "import"}:
        return "ingested"
    return "unknown"


def _host_title(host: str, host_session_id: str, home: Path) -> str:
    """A only: the host's own title. Never a first-user-message guess.

    Readers that expose a title may use a first-user fallback internally; we
    deliberately do not call them for this purpose. Only fields the host
    itself wrote count here.
    """
    if not host or not host_session_id:
        return ""
    try:
        if host == "claude-code":
            from .chatview.readers import claude_code as r
            projects = r._projects_dir(home)
            for _pd, path in r._iter_session_files(projects):
                if path.stem != host_session_id:
                    continue
                with open(path, encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh):
                        if i >= 400:
                            break
                        try:
                            o = json.loads(line)
                        except ValueError:
                            continue
                        if o.get("type") == "ai-title" and o.get("aiTitle"):
                            return str(o["aiTitle"])
                        if o.get("type") == "summary" and o.get("summary"):
                            return str(o["summary"])
                return ""
        if host == "dsh":
            from .dsh_title import dsh_title
            return dsh_title(home, host_session_id)
        if host == "codex":
                                                                   
                                                                       
                                            
            db = home / ".codex" / "state_5.sqlite"
            if db.is_file():
                import sqlite3
                try:
                    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
                    try:
                        row = con.execute(
                            "SELECT name, title FROM threads WHERE id = ?",
                            (host_session_id,)).fetchone()
                    finally:
                        con.close()
                    if row:
                        label = str(row[0] or "").strip() or str(row[1] or "").strip()
                        if label:
                            return label[:120]
                except sqlite3.Error:
                    pass
            idx = home / ".codex" / "session_index.jsonl"
            if idx.is_file():
                for line in idx.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    sid = str(o.get("id") or o.get("session_id") or "")
                    if sid == host_session_id:
                        return str(o.get("thread_name") or o.get("title") or "")
    except Exception:
        return ""
    return ""


class SessionStore:
    """Folder-canonical store used by the SDK, export and bundle code."""

    def __init__(self, root: str | os.PathLike[str] | None = None,
                 home: Path | None = None, read_only: bool = False):
        from pathlib import Path as _P
        self.root = (_P(root).expanduser() if root else sessions_root(home)).resolve()
        self.read_only = read_only
        if not read_only:
            self.root.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.root, 0o700)
            except OSError:
                pass
        self._folders: dict[tuple[str, str], Path] | None = None
        self._manifest_cache: dict[Path, dict[str, Any]] = {}
        self._home = home or Path.home()
        base = self.root
        readme = base / "README.md"
        if not read_only and not readme.exists():
            readme.write_text(
                "# ProveLog conversations\n\n"
                "One folder per conversation. Open `transcript-preview.md` to read it.\n\n"
                "- Delete a conversation by deleting its whole folder.\n"
                "- `manifest.json` is evidence metadata; do not edit it.\n"
                "- `attachments/` holds images/files; `index.md` lists them.\n"
                "- `index.md` / `index.jsonl` are rebuildable views, not evidence.\n",
                encoding="utf-8")
            try:
                os.chmod(readme, 0o600)
            except OSError:
                pass
        self._folders = None
        if not read_only:
            self._write_index()

                                                                                 
    def _scan(self) -> dict[tuple[str, str], Path]:
        out: dict[tuple[str, str], Path] = {}
        for mf in self.root.glob("*/*/manifest.json"):
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            sid = str(m.get("session_id") or "")
            agent = str(m.get("agent") or mf.parent.parent.name)
            if sid:
                out[(agent, sid)] = mf.parent
            self._manifest_cache[mf.parent] = m
        return out

    def _folder(self, agent: str, session_id: str) -> Path | None:
        if self._folders is None:
            self._folders = self._scan()
        folder = self._folders.get((agent, session_id))
        if folder is not None and not folder.is_dir():
                                                                                 
                                                                                  
                                                                                  
                                                               
            self._manifest_cache.pop(folder, None)
            self._folders = self._scan()
            folder = self._folders.get((agent, session_id))
        return folder

    def _manifest(self, folder: Path) -> dict[str, Any]:
        mf = folder / "manifest.json"
        try:
            return json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_index(self) -> None:
        """Derived, rebuildable views beside sessions/ - never the truth."""
        entries = []
        for folder, m in self._manifest_folders():
            title = str(m.get("display_title") or m.get("title") or "")
            sid = str(m.get("session_id") or folder.name)
            label = title or folder.name
            entries.append({
                "session_id": sid,
                "agent": m.get("agent", ""),
                "title": label,
                "path": folder.relative_to(self.root).as_posix(),
                "created_at": m.get("created_at", ""),
                "last_activity": m.get("last_activity", ""),
                "records": sum(int(v.get("count", 0))
                               for v in (m.get("streams") or {}).values()),
            })
        entries.sort(key=lambda e: (e["last_activity"], e["created_at"]), reverse=True)
        base = self.root
        lines = ["# Conversations", ""]
        for e in entries:
            lines.append(f"- {e['last_activity'] or e['created_at']} · "
                         f"{e['agent']} · [{e['title']}]({e['path']}/{TRANSCRIPT_NAME}) "
                         f"({e['records']} records)")
        (base / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (base / "index.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
            encoding="utf-8")
        for name in ("index.md", "index.jsonl"):
            try:
                os.chmod(base / name, 0o600)
            except OSError:
                pass

    def _find_by_chain(self, chain_session_id: str) -> tuple[Path, dict[str, Any]] | None:
        if not chain_session_id:
            return None
        for folder, m in self._manifest_folders():
            if chain_session_id in (m.get("chain_sessions") or {}):
                return folder, m
        return None

    def _write_manifest(self, folder: Path, manifest: dict[str, Any]) -> None:
        manifest["capture_tier"] = capture_tier_for(manifest.get("capture_point"))
                                                                             
                                                                  
        manifest["last_activity"] = str(
            manifest.get("last_activity") or manifest.get("created_at") or _now())
        tmp = folder / ".manifest.json.tmp"
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, folder / "manifest.json")

    def _all_records(self) -> list[tuple[str, dict[str, Any]]]:
        rows: list[tuple[str, dict[str, Any]]] = []
        for folder in sorted(self.root.glob("*/*")):
            if _trajectory_path(folder) is None:
                continue
            for env in _read_envelopes(folder):
                rows.append((_envelope_stream(env), env))
        return rows

    def _records_for_stream(self, stream_id: str) -> list[tuple[str, dict[str, Any]]]:
        rows: list[tuple[str, dict[str, Any]]] = []
        for mf in self.root.glob("*/*/manifest.json"):
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if stream_id not in (m.get("streams") or {}):
                continue
            for env in _read_envelopes(mf.parent):
                if _envelope_stream(env) == stream_id:
                    rows.append((stream_id, env))
        rows.sort(key=lambda x: x[1]["recv_seq"])
        return rows

                                                                                
    def _discarded_path(self) -> Path:
        return self.root / ".internal" / "discarded_chains.json"

    def _discarded(self) -> set[str]:
        cache = getattr(self, "_discarded_cache", None)
        if cache is not None:
            return cache
        try:
            rows = json.loads(self._discarded_path().read_text(encoding="utf-8"))
            cache = {str(r.get("chain")) for r in rows if isinstance(r, dict)}
        except (OSError, ValueError):
            cache = set()
        self._discarded_cache = cache
        return cache

    def _mark_discarded(self, chain: str, parent: str) -> None:
        if not chain:
            return
        path = self._discarded_path()
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                rows = []
        except (OSError, ValueError):
            rows = []
        rows.append({"chain": chain, "parent": parent, "at": _now()})
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows[-1000:], indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        self._discarded_cache = {str(r.get("chain")) for r in rows[-1000:]}

    @staticmethod
    def _skipped(chain: str, parent: str) -> dict[str, Any]:
        return {"skipped": True, "reason": "parent_missing",
                "chain": chain, "parent": parent,
                "recv_seq": -1, "server_record_hash": GENESIS_PREV_HASH}

                                                                                
    def _orphans_path(self) -> Path:
        return self.root / ".internal" / "orphans.json"

    def orphans(self) -> dict[str, str]:
        """{child session id: parent id} for children whose parent is absent."""
        try:
            rows = json.loads(self._orphans_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): str((v or {}).get("parent") or "")
                for k, v in rows.items() if isinstance(v, dict)}

    def mark_orphan(self, session_id: str, parent: str) -> None:
        """Remember a child whose parent is missing, so a poll stops re-reading it.

        Without this the collector decompressed the same 3.6 MB log every 30
        seconds only to discard every record again.
        """
        if not session_id or session_id in self.orphans():
            return
        rows = self.orphans()
        rows[session_id] = parent or ""
        path = self._orphans_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: {"parent": v, "at": _now()}
                                   for k, v in rows.items()}, indent=2) + "\n",
                       encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)

    def clear_orphan(self, session_id: str) -> None:
        rows = self.orphans()
        if session_id not in rows:
            return
        rows.pop(session_id, None)
        path = self._orphans_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: {"parent": v, "at": _now()}
                                   for k, v in rows.items()}, indent=2) + "\n",
                       encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)

    def _child_skip(self, meta: dict[str, Any], chain: str,
                    parent: str) -> dict[str, Any] | None:
        """A child session is kept only inside its parent's folder.

        Parent gone => discard the child. The whole chain is remembered, so the
        records that follow its genesis in the same batch are dropped too.
        """
        if chain and chain in self._discarded():
            return self._skipped(chain, parent)
        if not parent:
            return None
        host = str(meta.get("agent") or meta.get("host") or "")
        agent = host or "_unassigned"
        if self._find_parent(agent, parent) is not None:
            return None
        self._mark_discarded(chain, parent)
        return self._skipped(chain, parent)

    def _find_parent(self, agent: str, parent_session: str) -> tuple[Path, dict[str, Any]] | None:
        """The folder of a child session's parent, when it is already on disk."""
        if not parent_session:
            return None
        folder = self._folder(agent, parent_session)
        if folder is not None:
            return folder, self._manifest(folder)
        for candidate, m in self._manifest_folders():
            if str(m.get("agent") or "") != agent:
                continue
            if (str(m.get("session_id") or "") == parent_session
                    or parent_session in (m.get("child_sessions") or {})):
                return candidate, m
        return None

    def _rename_for_title(self, folder: Path, manifest: dict[str, Any],
                          title: str, session_id: str, agent: str) -> tuple[Path, dict[str, Any]]:
        """Give a session its human title while that is still safe.

        Renaming is allowed only before the evidence is fixed: no anchor receipt
        yet. After one, the folder name is part of what a third party may have
        seen, so it stays. The old name is kept in ``manifest.renamed_from``.
        """
        clean = sanitize_component(title, fallback="")
        if not clean or not session_id:
            return folder, manifest
        if manifest.get("anchors"):
            return folder, manifest
        desired = folder_name(clean, session_id)
        if folder.name == desired:
            return folder, manifest
        target = folder.parent / desired
        if target.exists() and target != folder:
            for n in range(2, 50):
                candidate = folder.parent / f"{desired}-{n}"
                if not candidate.exists():
                    target = candidate
                    break
            else:
                return folder, manifest
        try:
            os.replace(folder, target)
        except OSError:
            return folder, manifest
        manifest.setdefault("renamed_from", []).append(folder.name)
        manifest["title"] = clean
        manifest["display_title"] = clean
        self._write_manifest(target, manifest)
        self._folders = None
        self._manifest_cache.clear()
        return target, manifest

    def _manifest_folders(self) -> list[tuple[Path, dict[str, Any]]]:
        out = []
        for mf in self.root.glob("*/*/manifest.json"):
            try:
                manifest = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
                                                                                
                                                                                
                                                                            
                                                       
            if not manifest.get("session_stream") and (manifest.get("streams") or {}):
                sid = own_stream_id(manifest)
                if sid:
                    manifest["session_stream"] = sid
                    if not self.read_only:
                        self._write_manifest(mf.parent, manifest)
            out.append((mf.parent, manifest))
        return out

                                                                                 
    def create_stream(self, stream_id: str, label: str | None = None) -> None:
                                                                      
        return None

    def stream_exists(self, stream_id: str) -> bool:
        return any(stream_id in (m.get("streams") or {})
                   for _f, m in self._manifest_folders())

    def list_streams(self) -> list[dict[str, Any]]:
        agg: dict[str, dict[str, Any]] = {}
        for _folder, m in self._manifest_folders():
            for sid, st in (m.get("streams") or {}).items():
                item = agg.setdefault(sid, {"stream_id": sid,
                                            "created_at": m.get("created_at", ""),
                                            "label": "local", "records": 0,
                                            "last_activity": ""})
                item["records"] += int(st.get("count", 0))
                item["last_activity"] = max(item["last_activity"],
                                            m.get("last_activity", ""))
        return sorted(agg.values(), key=lambda r: r["created_at"], reverse=True)

    def append_record(self, stream_id: str, record: dict[str, Any]) -> dict[str, Any]:
        return self.append_records(stream_id, [record])[0]

    def append_records(self, stream_id: str, records: list[dict[str, Any]], *,
                       defer_index: bool = False) -> list[dict[str, Any]]:
        """Append a batch under ONE lock, ONE stream read and ONE zstd frame.

        A batch is one frame (TRAJECTORY §3): the manifest - which carries the
        frame index - is rewritten once per batch instead of once per record.
        Rewriting it per record made the frame list quadratic and a 15k-record
        import crawl.
        """
        if self.read_only:
            raise RuntimeError("SessionStore is read-only")
        if not records:
            return []
        lock = _lock_path(self.root, stream_id)
        out: list[dict[str, Any]] = []
        with _LOCK, _exclusive(lock):
            rows = self._records_for_stream(stream_id)
            pending: dict[Path, list[str]] = {}
            manifests: dict[Path, dict[str, Any]] = {}
            for record in records:
                receipt, folder, line = self._append_one(
                    stream_id, record, rows, manifests, pending)
                if line is None:
                    out.append(receipt)
                    continue
                rows.append((stream_id, {
                    "record": record,
                    "recv_seq": receipt["recv_seq"],
                    "recv_ts": receipt["recv_ts"],
                    "recv_hash": receipt["recv_hash"],
                    "server_prev_hash": receipt["server_prev_hash"],
                    "server_record_hash": receipt["server_record_hash"]}))
                pending.setdefault(folder, []).append(line)
                out.append(receipt)
            for folder, lines in pending.items():
                manifest = manifests.get(folder) or self._manifest(folder)
                self._flush_batch(folder, manifest, lines)
            if not defer_index:
                self._write_index()
        return out

    def _flush_batch(self, folder: Path, manifest: dict[str, Any],
                     lines: list[str]) -> None:
        offset, events = _append_frame(folder, lines)
        frames = manifest.setdefault("frames", [])
        frames.append({"n": len(frames), "offset": offset, "events": events})
        self._write_manifest(folder, manifest)

    @staticmethod
    def _chain_in_batch(chain_session_id: str,
                        manifests: dict[Path, dict[str, Any]]
                        ) -> tuple[Path, dict[str, Any]] | None:
        """Find a chain whose manifest is still only in memory (not on disk)."""
        if not chain_session_id:
            return None
        for folder, manifest in manifests.items():
            if chain_session_id in (manifest.get("chain_sessions") or {}):
                return folder, manifest
        return None

    @staticmethod
    def _parent_in_batch(parent_session: str,
                         manifests: dict[Path, dict[str, Any]]
                         ) -> tuple[Path, dict[str, Any]] | None:
        if not parent_session:
            return None
        for folder, manifest in manifests.items():
            if str(manifest.get("session_id") or "") == parent_session:
                return folder, manifest
        return None

    def _maybe_rename(self, folder: Path, manifest: dict[str, Any], title: str,
                      session_id: str, agent: str,
                      manifests: dict[Path, dict[str, Any]],
                      pending: dict[Path, list[str]]) -> tuple[Path, dict[str, Any]]:
        """Rename only when this batch has not queued a frame for the old path."""
        if pending.get(folder):
            return folder, manifest
        old = folder
        folder, manifest = self._rename_for_title(folder, manifest, title,
                                                  session_id, agent)
        if folder != old:
            manifests.pop(old, None)
            manifests[folder] = manifest
        return folder, manifest

    def _append_one(self, stream_id: str, record: dict[str, Any],
                    rows: list[tuple[str, dict[str, Any]]],
                    manifests: dict[Path, dict[str, Any]],
                    pending: dict[Path, list[str]]
                    ) -> tuple[dict[str, Any], Path | None, str | None]:
        """Prepare one record: (receipt, folder, envelope line).

        ``line`` is None for a duplicate, a refused gap or a discarded child -
        those write nothing.
        """
        recv_hash = sha256_hex(canonicalize(record))
        for _s, env in rows:
            if env.get("recv_hash") == recv_hash:
                dup = dict(env)
                dup["record"] = record
                dup["duplicate"] = True
                return dup, None, None
        meta = ((record.get("payload") or {}).get("meta") or {})
        chain_session_id = str(record.get("session_id") or "")
        fork_of = str(meta.get("fork_of") or "")
        parent_session = "" if fork_of else str(meta.get("parent_session") or "")
        skip = self._child_skip(meta, chain_session_id, parent_session)
        if skip is not None:
            return skip, None, None
        seq = record.get("seq")
        if seq is not None and chain_session_id:
            same = [e for _s, e in rows
                    if (e.get("record") or {}).get("session_id") == chain_session_id]
            top = max((e["record"].get("seq") for e in same
                       if isinstance(e["record"].get("seq"), int)), default=None)
            if (top is None and seq > 0) or (top is not None and seq > top + 1):
                raise SeqGap(f"session {chain_session_id} already ends at seq {top}; "
                             f"refusing seq {seq} (delivered out of order)")
            for e in same:
                if e["record"].get("seq") == seq:
                    raise SeqConflict(f"seq {seq} already exists in this session "
                                      f"with different content")
        prev = max(rows, key=lambda x: x[1]["recv_seq"], default=None)
        recv_seq = 0 if prev is None else int(prev[1]["recv_seq"]) + 1
        server_prev = GENESIS_PREV_HASH if prev is None else prev[1]["server_record_hash"]
        recv_ts = _now()
        server_record_hash = sha256_hex(canonicalize({
            "recv_seq": recv_seq, "recv_ts": recv_ts,
            "recv_hash": recv_hash, "server_prev_hash": server_prev}))
        host_session_id = str(meta.get("host_session_id")
                              or meta.get("conversation_id") or "")
                                                                               
                                                                            
        existing = self._chain_in_batch(chain_session_id, manifests) \
            or self._find_by_chain(chain_session_id)
        if existing is not None:
            folder, manifest = existing
            if folder not in manifests:
                manifests[folder] = manifest
            agent = str(manifest.get("agent") or meta.get("host") or stream_id)
            session_id = str(manifest.get("session_id") or chain_session_id)
        else:
            host = str(meta.get("agent") or meta.get("host") or "")
            agent = host or "_unassigned"
            session_id = str(host_session_id or chain_session_id or "unknown")
            title = str(meta.get("title") or "")
            if not title and host:
                title = _host_title(host, session_id, self._home)
            folder = None
            child_of_parent = False
                                                                        
                                                                        
            if parent_session:
                parent = self._parent_in_batch(parent_session, manifests) \
                    or self._find_parent(agent, parent_session)
                if parent is not None:
                    folder, manifest = parent
                    child_of_parent = True
            if folder is None:
                folder = self._folder(agent, session_id)
            if folder is None:
                sf = SessionFolder.create(root=self.root, agent=agent, title=title,
                                           session_id=session_id)
                folder = sf.path
                if self._folders is not None:
                    self._folders[(agent, session_id)] = folder
            manifest = manifests.get(folder) or self._manifest(folder)
            if title and not child_of_parent:
                folder, manifest = self._maybe_rename(
                    folder, manifest, title, session_id, agent, manifests, pending)
            manifests[folder] = manifest
        if folder not in manifests:
            manifests[folder] = manifest
        else:
            manifest = manifests[folder]
        if not manifest:
            manifest = {
                "v": 1, "session_id": session_id, "agent": agent,
                "title": str(meta.get("title") or ""),
                "display_title": str(meta.get("title") or ""),
                "created_at": recv_ts, "last_activity": recv_ts,
                "canonical": [TRANSCRIPT_NAME, "manifest.json"],
                "session_stream": stream_id,
                "fork_of": fork_of,
                "seeded_prefix": bool(meta.get("seeded_prefix")),
                "project": str(meta.get("project") or ""),
                "surface": str(meta.get("surface") or ""),
                "files": {}, "chain_sessions": {}, "streams": {},
                "anchors": [], "attachments": []}
            manifests[folder] = manifest
        manifest.setdefault("chain_sessions", {})
                                                                              
                                                                        
        chain_owner = str((manifest.get("chain_sessions") or {})
                          .get(chain_session_id) or "")
        owner = host_session_id or chain_owner
                                                                               
                                                                           
                                                        
        is_child = bool(owner) and bool(manifest.get("session_id")) \
            and owner != str(manifest.get("session_id"))
        late_title = str(meta.get("title") or "")
        if late_title and not is_child:
            folder, manifest = self._maybe_rename(
                folder, manifest, late_title, session_id, agent, manifests, pending)
            manifests[folder] = manifest
        if fork_of and not manifest.get("fork_of"):
            manifest["fork_of"] = fork_of
            manifest["seeded_prefix"] = bool(meta.get("seeded_prefix"))
        if meta.get("project") and not manifest.get("project"):
            manifest["project"] = str(meta.get("project"))
        if meta.get("surface") and not manifest.get("surface"):
            manifest["surface"] = str(meta.get("surface"))
        if chain_session_id and not manifest.get("chain_id"):
            manifest["chain_id"] = chain_session_id
        if chain_session_id and not chain_owner:
                                                                             
            manifest["chain_sessions"][chain_session_id] = \
                owner or str(manifest.get("session_id") or session_id)
        if meta.get("session_role") and not is_child:
            manifest["session_role"] = str(meta["session_role"])
        if parent_session and not is_child:
            manifest["parent_session"] = parent_session
        if is_child:
            children = manifest.setdefault("child_sessions", {})
            child = children.setdefault(owner, {"records": 0})
            child["records"] = int(child.get("records", 0)) + 1
            child["chain_id"] = chain_session_id
            child["stream_id"] = stream_id
            if parent_session:
                child["parent_session"] = parent_session
            if meta.get("imported_from"):
                child["source_path"] = str(meta["imported_from"])
        if agent == "_unassigned":
            manifest["unassigned"] = True
        action = str(record.get("action_type") or "")
        if action == "lifecycle.commitment":
                                                                                 
                                                                                  
            cpay = record.get("payload") or {}
            row = (manifest.setdefault("streams", {})
                   .setdefault(stream_id, {"count": 0}))
            marks = row.setdefault("checkpoints", [])
            marks.append({"covers_up_to": int(cpay.get("covers_up_to") or 0),
                          "at": recv_ts, "root": str(cpay.get("root") or "")})
            manifest["last_checkpoint_at"] = recv_ts
            manifest.pop("checkpoints", None)                            
            if len(marks) > 20:
                del marks[:-20]
        if not is_child and not manifest.get("display_title"):
            late = str(meta.get("title") or "") or \
                _host_title(str(meta.get("host") or ""), session_id, self._home)
            if late:
                manifest["title"] = late
                manifest["display_title"] = late
        streams = manifest.setdefault("streams", {})
        st = streams.setdefault(stream_id, {"count": 0, "head": GENESIS_PREV_HASH,
                                            "last_recv_seq": -1})
        st["count"] = int(st.get("count", 0)) + 1
        st["head"] = server_record_hash
        st["last_recv_seq"] = recv_seq
                                                                            
                                                                            
                        
        manifest["last_activity"] = str(record.get("ts_utc") or recv_ts)
                                                                          
                                                                             
                                                                              
        if action in ("user_message", "assistant_message"):
            event_ts = str(record.get("ts_utc") or recv_ts)
                                                                            
                                                                      
            if not manifest.get("first_ts") or event_ts < str(manifest["first_ts"]):
                manifest["first_ts"] = event_ts
        if not is_child and manifest.get("title") and not manifest.get("display_title"):
            manifest["display_title"] = manifest["title"]
        envelope = {"record": record, "stream_id": stream_id,
                    "recv_seq": recv_seq, "recv_ts": recv_ts,
                    "recv_hash": recv_hash, "server_prev_hash": server_prev,
                    "server_record_hash": server_record_hash}
        line = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        manifest["records"] = int(manifest.get("records", 0)) + 1
        manifest["chain"] = {"records": manifest["records"], "head": server_record_hash}
        self._bump_coverage(manifest, record, stream_id)
        manifest["canonical"] = [
            TRAJECTORY_ZST if _CODEC.available else TRAJECTORY,
            TRANSCRIPT_NAME, "manifest.json"]
        return ({"recv_seq": recv_seq, "recv_ts": recv_ts, "recv_hash": recv_hash,
                 "server_prev_hash": server_prev,
                 "server_record_hash": server_record_hash}, folder, line)
    @staticmethod
    def _bump_coverage(manifest: dict[str, Any], record: dict[str, Any],
                       stream_id: str = "") -> None:
        """Count what the session actually captured (the honest coverage record).

        Kept twice: for the folder (parent + subagents) and per stream, because
        a conversation's own count must not include a subagent's records.
        """
        stream_row = manifest.setdefault("streams", {}).setdefault(stream_id, {})
        per_stream = stream_row.setdefault("coverage", {"model_calls": 0,
                                                        "tool_calls": 0, "gaps": []})
        coverage = manifest.setdefault("coverage", {"model_calls": 0, "tool_calls": 0,
                                                    "gaps": []})
        action = str(record.get("action_type") or "")
        payload = record.get("payload") or {}
        key = {"model_call": "model_calls", "tool_call": "tool_calls",
               "tool_call_intent": "tool_call_intents", "tool_result": "tool_results",
               "user_message": "user_messages",
               "assistant_message": "assistant_messages"}.get(action)
        if action == "user_message" and str(payload.get("origin") or "user") not in ("", "user"):
                                                                                  
                                                                                
            key = "context_messages"
        if key:
            coverage[key] = int(coverage.get(key, 0)) + 1
            per_stream[key] = int(per_stream.get(key, 0)) + 1
        payload = record.get("payload") or {}
        meta = payload.get("meta") or {}
        sources = manifest.setdefault("sources", {})
                                                                             
        if meta.get("sidecar"):
            sources.setdefault("sidecar", "observed")
        elif meta.get("host_event"):
            sources.setdefault("hooks", "installed")

    def manifest_for(self, agent: str, session_id: str) -> dict[str, Any] | None:
        folder = self._folder(agent, session_id)
        if folder is None:
            return None
        m = self._manifest(folder)
        return m or None

                                                                                
    _CAPTURE_FIELDS = ("host_seq", "assignment_seq", "source_path",
                       "source_mtime", "source_size", "chain_id",
                       "capture_from_ms", "head_omitted", "capture_point",
                       "verification_level", "commit_at", "commit_count",
                       "source_offsets", "last_turn", "last_ms",
                       "legacy_multi", "deleted_at_ms")

    def session_capture(self, session_id: str) -> dict[str, Any]:
        """The ingestion watermark for ONE session, parent or child.

        ``host_seq`` used to live on the folder manifest, which is shared by
        the parent and every subagent stored inside it: a child then always
        read ``-1`` and re-imported its whole log every poll.
        """
        from . import capture_state

        journal = capture_state.get(session_id)
        if journal:
            return {k: v for k, v in journal.items() if k in self._CAPTURE_FIELDS}
        folder = self.locate(session_id)
        if folder is None:
            return {}
        m = self._manifest(folder)
        if str(m.get("session_id") or "") in {session_id, _bare_id(session_id)}:
            state = {k: m[k] for k in self._CAPTURE_FIELDS if k in m}
            if state:
                capture_state.update(session_id, **state)                           
            return state
        child = (m.get("child_sessions") or {}).get(session_id)
        if child is None:
            for key, value in (m.get("child_sessions") or {}).items():
                if _bare_id(key) == _bare_id(session_id):
                    child = value
                    break
        state = {k: v for k, v in dict(child or {}).items() if k in self._CAPTURE_FIELDS}
        if state:
            capture_state.update(session_id, **state)                           
        return state

    def set_session_capture(self, session_id: str, **fields: Any) -> None:
        """Persist the watermark for one session without touching the other's.

        Written to the user-level journal (which survives deleting the folder) and
        mirrored into the manifest for readers that only have the folder.
        """
        from . import capture_state
        capture_state.update(session_id, **{k: v for k, v in fields.items() if v is not None})
        folder = self.locate(session_id)
        if folder is None:
            return
        m = self._manifest(folder)
        wanted = {k: v for k, v in fields.items() if v is not None}
        if str(m.get("session_id") or "") in {session_id, _bare_id(session_id)}:
            m.update(wanted)
        else:
            child = (m.setdefault("child_sessions", {})).setdefault(session_id, {})
            child.update(wanted)
        self._write_manifest(folder, m)

    def child_session_ids(self) -> dict[str, list[str]]:
        """{folder path: [child session ids]} for every manifest that has them."""
        out: dict[str, list[str]] = {}
        for folder, m in self._manifest_folders():
            kids = list((m.get("child_sessions") or {}).keys())
            if kids:
                out[str(folder)] = kids
        return out

    def has_session(self, session_id: str) -> bool:
        """Does this id already have records here - as a parent OR as a child?"""
        folder = self.locate(session_id)
        if folder is None:
            return False
        m = self._manifest(folder)
        if str(m.get("session_id") or "") in {session_id, _bare_id(session_id)}:
            return bool(m.get("records"))
        for key, child in (m.get("child_sessions") or {}).items():
            if _bare_id(key) == _bare_id(session_id):
                return bool((child or {}).get("records"))
        return False

                                                                                
    def locate(self, session_id: str, agent: str | None = None) -> Path | None:
        """Find a session folder by id - parent ids AND child ids.

        A subagent's records live inside its parent's folder, so the child id
        is only in the manifest (``child_sessions`` / ``chain_sessions``).
        Without this a child session is "not found" on every poll and gets
        re-imported from seq 0 forever.
        """
        if agent is not None:
            return self._folder(agent, session_id)
        target = str(session_id or "")
        variants = {target, _bare_id(target)}
        for folder, m in self._manifest_folders():
            if str(m.get("session_id") or "") in variants:
                return folder
            if any(v in (m.get("child_sessions") or {}) for v in variants):
                return folder
            if any(v in (m.get("chain_sessions") or {}).values() for v in variants):
                return folder
                                                                  
        for folder in self.root.glob("*/*"):
            name = str(folder.name)
            if any(name.endswith(f"-id-{v}") for v in variants if v):
                return folder
        return None

    def folder_for_stream(self, stream_id: str) -> Path | None:
        """The session folder that owns this stream, or None."""
        for folder, m in self._manifest_folders():
            if stream_id in (m.get("streams") or {}):
                return folder
        return None

    def attachments_for_stream(self, stream_id: str) -> list[dict[str, Any]]:
        """The attachment index for a stream's folder (empty when there is none)."""
        folder = self.folder_for_stream(stream_id)
        if folder is None:
            return []
        manifest = self._manifest(folder)
        return [a for a in (manifest.get("attachments") or []) if isinstance(a, dict)]

    def read_attachment(self, stream_id: str, name: str) -> bytes | None:
        """Bytes of one attachment file, by bare name; None when missing/unsafe."""
        folder = self.folder_for_stream(stream_id)
        if folder is None:
            return None
        from . import attachments as attachments_mod
        path = attachments_mod.resolve(folder, name)
        if path is None:
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def session_envelopes(self, session_id: str, agent: str | None = None
                          ) -> list[dict[str, Any]]:
        folder = self.locate(session_id, agent)
        return _read_envelopes(folder) if folder else []

    def session_events(self, session_id: str, agent: str | None = None
                       ) -> list[dict[str, Any]]:
        return [env.get("record") or {} for env in self.session_envelopes(session_id, agent)]

    def sessions(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for folder, m in self._manifest_folders():
            own = own_stream(m)
            out.append({
                "agent": str(m.get("agent") or folder.parent.name),
                "session_id": str(m.get("session_id") or folder.name),
                "title": str(m.get("display_title") or m.get("title") or ""),
                "path": str(folder),
                "records": int(own.get("count") or m.get("records") or 0),
                "last_activity": str(m.get("last_activity") or ""),
            })
        return sorted(out, key=lambda s: s["last_activity"], reverse=True)

    def export_candidates(self) -> list[dict[str, Any]]:
        """Conversations `provelog export` can export, newest activity first.

        Read-only. One row per conversation folder, keyed by the conversation's
        OWN stream (a subagent's stream in the same folder is only counted).
        ``stamped`` is how many leading records carry a delivered receipt; it is
        a hint for the picker, not a verification result.
        """
        out: list[dict[str, Any]] = []
        for folder, m in self._manifest_folders():
            streams = m.get("streams") or {}
            sid = own_stream_id(m)
            if not sid or sid not in streams:
                continue
            count = int((streams.get(sid) or {}).get("count") or 0)
            if count <= 0:
                continue
            stamped = 0
            for a in m.get("anchors") or []:
                if not isinstance(a, dict) or not a.get("receipt"):
                    continue
                if a.get("pending") or a.get("failed"):
                    continue
                owner = str(a.get("stream_id") or "")
                if owner and owner != sid:
                    continue
                if not owner and len(streams) > 1:
                    continue
                try:
                    stamped = max(stamped, int(a.get("covers_up_to") or 0))
                except (TypeError, ValueError):
                    pass
            session_id = str(m.get("session_id") or folder.name)
            out.append({
                "session_id": session_id,
                "stream_id": sid,
                "agent": str(m.get("agent") or folder.parent.name),
                "title": str(m.get("display_title") or m.get("title") or ""),
                "records": count,
                "turns": ((streams.get(sid) or {}).get("coverage")
                          or (m.get("coverage") if len(streams) == 1 else {})
                          or {}).get("user_messages"),
                "subagent_streams": [key for key in streams if key != sid],
                "stamped": min(stamped, count),
                "last_activity": str(m.get("last_activity") or m.get("created_at") or ""),
                "project": str(m.get("project") or ""),
                "subagents": max(0, len(streams) - 1),
                "fork_of": str(m.get("fork_of") or ""),
                "folder": str(folder),
            })
        return sorted(out, key=lambda r: r["last_activity"], reverse=True)

    def rebuild_md(self, session_id: str, agent: str | None = None) -> Path | None:
        """Regenerate the human transcript from the event stream.

        Called when a session is finalized (or on demand): rendering on every
        appended record made a 15k-record import take minutes for a view nobody
        reads mid-import.
        """
        folder = self.locate(session_id, agent)
        if folder is None:
            return None
        envs = _read_envelopes(folder)
        manifest = self._manifest(folder)
        title = str(manifest.get("display_title") or manifest.get("title")
                    or session_id)
        parent_id = str(manifest.get("session_id") or "")
        child_chains = frozenset(
            chain for chain, who in (manifest.get("chain_sessions") or {}).items()
            if who and _bare_id(who) != _bare_id(parent_id))
        path = folder / TRANSCRIPT_NAME
        tmp = folder / (".tmp-" + TRANSCRIPT_NAME)
        tmp.write_text(_render_transcript(envs, title, child_chains), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        manifest["canonical"] = [
            TRAJECTORY_ZST if _CODEC.available else TRAJECTORY,
            TRANSCRIPT_NAME, "manifest.json"]
        self._write_manifest(folder, manifest)
                                                                        
        for stale in (folder / "session.md",):
            if stale.is_file():
                stale.unlink()
        parts = folder / "parts"
        if parts.is_dir():
            for old in parts.glob("*.md"):
                old.unlink()
            try:
                parts.rmdir()
            except OSError:
                pass
        return path

    def capture_state(self, session_id: str, agent: str | None = None) -> dict[str, Any]:
        folder = self.locate(session_id, agent)
        if folder is None:
            return {}
        return dict(self._manifest(folder).get("capture") or {})

    def set_capture_state(self, session_id: str, state: dict[str, Any],
                          agent: str | None = None) -> None:
        folder = self.locate(session_id, agent)
        if folder is None:
            return
        manifest = self._manifest(folder)
        manifest["capture"] = dict(state)
        self._write_manifest(folder, manifest)

    def update_title(self, session_id: str, title: str) -> Path | None:
        """Apply the host's current title (renames only while still safe).

        A child/subagent id resolves to its parent's folder; it may not retitle
        or rename it - the folder is the parent conversation's identity.

        Idempotent: a watch pass calls this on every session it sees, and a
        title that has not changed must not rewrite the manifest.
        """
        title = str(title or "").strip()
        folder = self.locate(session_id)
        if folder is None or not title:
            return None
        manifest = self._manifest(folder)
        real_id = str(manifest.get("session_id") or session_id)
        if _bare_id(real_id) != _bare_id(session_id):
            return folder
        current = str(manifest.get("display_title") or manifest.get("title") or "")
        if current == title:
            return folder
                                                                             
                                                       
        if title == real_id and current:
            return folder
        manifest["title"] = title
        manifest["display_title"] = title
        self._write_manifest(folder, manifest)
        if not manifest.get("anchors"):
            folder, _m = self._rename_for_title(
                folder, manifest, title, real_id, str(manifest.get("agent") or ""))
        return folder

    def add_attachments(self, session_id: str, entries: list[dict[str, Any]]) -> None:
        """Record copied attachment files in the manifest, keyed by path."""
        if not entries:
            return
        folder = self.locate(session_id)
        if folder is None:
            return
        manifest = self._manifest(folder)
        rows = {str(a.get("path")): a for a in (manifest.get("attachments") or [])
                if isinstance(a, dict)}
        for entry in entries:
            rows[str(entry.get("path"))] = entry
        manifest["attachments"] = sorted(rows.values(), key=lambda a: str(a.get("path")))
        self._write_manifest(folder, manifest)

    def set_session_fields(self, session_id: str, **fields: Any) -> None:
        """Update small manifest fields (watermark, capture point, progress)."""
        folder = self.locate(session_id)
        if folder is None:
            return
        manifest = self._manifest(folder)
        manifest.update({k: v for k, v in fields.items() if v is not None})
        self._write_manifest(folder, manifest)

    def touch_sources(self, session_id: str, source: str, status: str = "observed",
                      agent: str | None = None) -> None:
        """Record that a capture source was present for this session."""
        folder = self.locate(session_id, agent)
        if folder is None:
            return
        manifest = self._manifest(folder)
        sources = manifest.setdefault("sources", {})
        if sources.get(source) != status:
            sources[source] = status
            self._write_manifest(folder, manifest)

    def anchor_leaves(self, stream_id: str) -> list[str]:
        """The leaves an anchor commits to: server receipt-chain hashes, in order."""
        return [r["server_record_hash"] for r in self.get_records(stream_id)]

    def _write_anchor_file(self, folder: Path, entry: dict[str, Any]) -> None:
        """Keep every receipt as its own file: the manifest is a view, not the vault."""
        try:
            target = folder / "anchors"
            target.mkdir(exist_ok=True)
            (target / f"{entry['anchor_id']}.json").write_text(
                json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def anchors_for_stream(self, stream_id: str) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for _folder, m in self._manifest_folders():
            if stream_id not in (m.get("streams") or {}):
                continue
            multi = len(m.get("streams") or {}) > 1
            for a in m.get("anchors") or []:
                if not isinstance(a, dict) or not a.get("anchor_id"):
                    continue
                owner = str(a.get("stream_id") or "")
                                                                                  
                                                                                  
                                                                                
                                                                                   
                if owner and owner != stream_id:
                    continue
                if not owner and multi:
                    continue
                seen[str(a["anchor_id"])] = a
        return sorted(seen.values(), key=lambda a: (a.get("covers_up_to", 0),
                                                    a.get("created_at", "")))

    def streams_needing_stamp(self, created_after_ms: int | None = None
                              ) -> list[dict[str, Any]]:
        """Streams whose records are not fully covered by a third-party stamp.

        The collector uses this to catch up after the stamping service was
        unreachable: a conversation recorded while the service was down still
        deserves its stamp when the service returns. ``created_after_ms`` is the
        install baseline - conversations that already existed before ProveLog was
        installed are not retroactively stamped (the v1 policy).
        """
        from datetime import datetime

                                                                            
                                                                           
        from .anchoring import PENDING_TTL_S

        out: list[dict[str, Any]] = []
        for _folder, m in self._manifest_folders():
            created = None
            try:
                created = datetime.fromisoformat(
                    str(m.get("created_at") or "").replace("Z", "+00:00"))
            except (TypeError, ValueError):
                created = None
            if created_after_ms and created is not None:
                if created.timestamp() * 1000 < created_after_ms:
                    continue
            anchors = [a for a in (m.get("anchors") or []) if isinstance(a, dict)]
            streams = m.get("streams") or {}
            for stream, row in streams.items():
                count = int(row.get("count") or 0)
                if count <= 0:
                    continue
                covered = 0
                fresh_pending = False
                for a in anchors:
                    owner = str(a.get("stream_id") or "")
                    if owner and owner != stream:
                        continue
                    if not owner and len(streams) > 1:
                        continue
                                                                                
                                                                              
                    if a.get("failed"):
                        continue
                    if a.get("pending"):
                                                                               
                                                                            
                                                                                
                                                                         
                                                                              
                                                                               
                                                                            
                                   
                        if _age_s(a.get("created_at")) < PENDING_TTL_S:
                            fresh_pending = True
                        continue
                    covered = max(covered, int(a.get("covers_up_to") or 0))
                if fresh_pending:
                    continue
                if count > covered:
                    out.append({"stream_id": stream, "count": count,
                                "covered": covered,
                                "last_activity": str(m.get("last_activity") or "")})
        out.sort(key=lambda r: r["last_activity"])
        return out

    def pending_anchor(self, stream_id: str) -> dict[str, Any] | None:
        rows = [a for a in self.anchors_for_stream(stream_id) if a.get("pending")]
        return rows[-1] if rows else None

    def add_anchor(self, stream_id: str, receipt: dict[str, Any], *,
                   covers_up_to: int, anchor_id: str | None = None,
                   pending: bool = False, failed: bool = False,
                   attempts: int = 0, failed_at: str | None = None,
                   last_error: str | None = None) -> None:
        """Record one stamp in every session folder that belongs to this stream."""
        anchor_id = anchor_id or str(receipt.get("anchor_id") or "")
        if not anchor_id:
            raise ValueError("anchor receipt has no anchor_id")
        entry = {"anchor_id": anchor_id, "stream_id": stream_id,
                 "covers_up_to": int(covers_up_to), "created_at": _now(),
                 "receipt": receipt, "pending": bool(pending),
                 "failed": bool(failed), "attempts": int(attempts or 0),
                 "failed_at": failed_at, "last_error": last_error}
        touched = False
        for folder, m in self._manifest_folders():
            if stream_id not in (m.get("streams") or {}):
                continue
            anchors = [a for a in (m.get("anchors") or [])
                       if not (isinstance(a, dict) and a.get("anchor_id") == anchor_id)]
            if pending:
                                                                                
                                                                                 
                                                                             
                                                         
                multi = len(m.get("streams") or {}) > 1
                for a in anchors:
                    if not (isinstance(a, dict) and a.get("pending")):
                        continue
                    owner = str(a.get("stream_id") or "")
                    if owner and owner != stream_id:
                        continue
                    if not owner and multi:
                        continue
                    a["pending"] = False
                    a["failed"] = True
                    a["failed_at"] = _now()
                    a["last_error"] = f"superseded by {anchor_id}"
                    self._write_anchor_file(folder, a)
            anchors.append(entry)
                                                                                  
                                                                                  
                                             
            m["anchors"] = anchors[-MAX_ANCHORS_IN_MANIFEST:]
            self._write_manifest(folder, m)
            self._write_anchor_file(folder, entry)
            touched = True
        if not touched:
            raise KeyError(f"stream {stream_id} has no session folder")
        self._write_index()

    def resolve_pending_anchor(self, stream_id: str, receipt: dict[str, Any]) -> None:
        anchor_id = str(receipt.get("anchor_id") or "")
        if not anchor_id:
            return
        for folder, m in self._manifest_folders():
            if stream_id not in (m.get("streams") or {}):
                continue
            changed = False
            for a in m.get("anchors") or []:
                if isinstance(a, dict) and a.get("anchor_id") == anchor_id and a.get("pending"):
                    a["pending"] = False
                    a["failed"] = False
                    a["receipt"] = receipt
                    a["attempts"] = int(a.get("attempts") or 0) + 1
                    a["last_error"] = None
                    changed = True
                    self._write_anchor_file(folder, a)
            if changed:
                self._write_manifest(folder, m)
        self._write_index()

    def fail_pending_anchor(self, stream_id: str, anchor_id: str, reason: str) -> None:
        """Mark a queued claim as lost so a later pass can re-claim it.

        The relay is deliver-then-delete: a receipt that is fetched after the
        7-day TTL, or a client that dies between queueing and pickup, leaves a
        pending entry whose receipt no longer exists. Failing it here keeps the
        history honest while ensuring it stops counting as coverage.
        """
        if not anchor_id:
            return
        for folder, m in self._manifest_folders():
            if stream_id not in (m.get("streams") or {}):
                continue
            changed = False
            for a in m.get("anchors") or []:
                if isinstance(a, dict) and a.get("anchor_id") == anchor_id and a.get("pending"):
                    a["pending"] = False
                    a["failed"] = True
                    a["failed_at"] = _now()
                    a["last_error"] = reason
                    changed = True
                    self._write_anchor_file(folder, a)
            if changed:
                self._write_manifest(folder, m)
        self._write_index()

    def get_records(self, stream_id: str) -> list[dict[str, Any]]:
        rows = []
        for _sid, env in self._records_for_stream(stream_id):
            rec = env.get("record") or {}
            rows.append({"recv_seq": env["recv_seq"], "recv_ts": env["recv_ts"],
                         "recv_hash": env["recv_hash"],
                         "server_prev_hash": env["server_prev_hash"],
                         "server_record_hash": env["server_record_hash"],
                         "record": rec})
        rows.sort(key=lambda r: r["recv_seq"])
        return rows

    def head(self, stream_id: str) -> dict[str, Any]:
        rows = self.get_records(stream_id)
        if not rows:
            return {"recv_seq": -1, "server_record_hash": GENESIS_PREV_HASH}
        last = rows[-1]
        return {"recv_seq": last["recv_seq"], "server_record_hash": last["server_record_hash"]}

    def count_records(self, stream_id: str) -> int:
        return len(self.get_records(stream_id))

    def delete_stream(self, stream_id: str) -> dict[str, Any] | None:
        if not self.stream_exists(stream_id):
            return None
        count = self.count_records(stream_id)
        for folder in list(self.root.glob("*/*")):
            m = self._manifest(folder)
            if stream_id in (m.get("streams") or {}):
                import shutil
                shutil.rmtree(folder)
        self._folders = None
        if not self.read_only:
            self._write_index()
        return {"records": count}

    def close(self) -> None:
        return None
