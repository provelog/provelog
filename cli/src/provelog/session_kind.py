"""Host-declared session roles; never infer these from user-visible titles."""
from __future__ import annotations

import json
from pathlib import Path


def codex_kind(meta: dict) -> tuple[str, str]:
    source = meta.get('source')
    sub = source.get('subagent') if isinstance(source, dict) else None
    if meta.get('thread_source') == 'guardian_review' or (
            isinstance(sub, dict) and sub.get('other') == 'guardian'):
        return 'internal', str(meta.get('parent_thread_id') or '')
    if sub is not None:
        spawn = sub.get('thread_spawn', {}) if isinstance(sub, dict) else {}
        parent = meta.get('parent_thread_id') or (
            spawn.get('parent_thread_id') if isinstance(spawn, dict) else '')
        return 'subagent', str(parent or '')
    return 'main', ''


def codex_meta(path: Path) -> dict:
    try:
        with path.open(encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                return row.get('payload', {}) if row.get('type') == 'session_meta' else {}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def claude_identity(path: Path) -> tuple[str, str]:
    if path.parent.name == 'subagents':
        parent = path.parent.parent.name
        return parent + ':subagent:' + path.stem, parent
    return path.stem, ''
