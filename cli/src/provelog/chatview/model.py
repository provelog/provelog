"""The unified internal format: every host's transcript normalizes to this,
and the page knows nothing else.

A reader turns its host's on-disk format into SessionView/MessageView
objects. The API layer then serializes them (with size caps - a tool output
can be megabytes, and the page does not need it all) into the JSON the
page renders. Nothing here touches provelog's recording, receipts or
verification: the viewer reads what hosts already wrote, nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass, field

                                                                          
                                                                          
                                                                       
                         
MAX_THINKING_CHARS = 10_000
MAX_OUTPUT_CHARS = 4_000
MAX_TEXT_CHARS = 100_000


@dataclass
class ToolCallView:
    """One tool call, input and (when it came back) its result joined.

    Hosts record the call and its result as separate records (Claude Code:
    an assistant tool_use block, then a user tool_result block); the reader
    stitches them by id so the page can show one collapsible card.
    """

    name: str                                                        
    summary: str = ""                                                          
    input_text: str | None = None                                               
    output: str | None = None                                        
    is_error: bool = False
    images: list[dict] = field(default_factory=list)                         


                                                                          
                                                                    
                                                                         
                                                           
_USAGE_KEYS = ("input", "output", "cache_read", "cache_write", "total")


def usage_dict(d: dict | None) -> dict | None:
    """Normalize a canonical-keyed dict: keep the keys the source really
    provided (numeric, non-bool), drop the rest. None when nothing did."""
    if not isinstance(d, dict):
        return None
    out = {}
    for k in _USAGE_KEYS:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = v
    return out or None


@dataclass
class MessageView:
    """One conversational turn. A single host line can carry several parts
    (thinking + text + tool calls in one assistant message), so all three
    live on one object and the page renders whichever are present."""

    role: str                                            
    ts: str | None                                                        
    turn: int | None = None                                         
    text: str = ""                                
    thinking: str | None = None                                         
    tool_calls: list[ToolCallView] = field(default_factory=list)
    model: str | None = None                                                 
    provider: str | None = None                                                
    effort: str | None = None                                                  
    images: list[dict] = field(default_factory=list)                     
    files: list[dict] = field(default_factory=list)                                      
    usage: dict | None = None                                                


@dataclass
class SessionView:
    """One conversation, fully parsed."""

    host: str                                                    
    session_id: str                                                            
    title: str = ""                                         
    project: str | None = None                                        
    started_at: str | None = None                      
    last_at: str | None = None                        
    messages: list[MessageView] = field(default_factory=list)
    bad_lines: int = 0                                                        
    source_path: str | None = None                                             
    usage_total: dict | None = None                                          
                                                                             
                                                                             
                                                                             
    models: list[str] | None = None                                           
                                                                           
                                                                            
                                                                              
                                                                              
                                                                           
                                                                              


@dataclass
class SessionSummary:
    """The cheap half of a session: enough for the list, parsed without
    reading every message. `load()` later returns the full SessionView."""

    host: str
    session_id: str
    title: str = ""
    project: str | None = None
    started_at: str | None = None
    last_at: str | None = None
    message_count: int = 0                                                 
    source_path: str | None = None
    models: list[str] | None = None                                       
                                                                           
                                                                           
                                                                     
    usage_total: dict | None = None                                         


def _cap(text: str | None, limit: int) -> str | None:
    """Truncate for transport, marking the cut so the page can say so."""
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + f"\n…(前 {limit} 字之后截断 / truncated at {limit} chars)"


def _derived_models(messages: list[MessageView]) -> list[str]:
    """Distinct model ids in the order the session used them."""
    seen: list[str] = []
    for m in messages:
        if m.model and m.model not in seen:
            seen.append(m.model)
    return seen


def _summed_usage(messages: list[MessageView]) -> dict | None:
    """Session totals as plain arithmetic over the per-message numbers the
    host recorded (claude-code, hermes, kimi have no session-level row).
    Used only when the reader did not find an authoritative total."""
    tot: dict[str, float] = {}
    for m in messages:
        if m.usage:
            for k, v in m.usage.items():
                tot[k] = tot.get(k, 0) + v
    if tot:
        return {k: int(v) if float(v).is_integer() else v for k, v in tot.items()}
    return None


def message_to_api(m: MessageView) -> dict:
    return {
        "role": m.role,
        "ts": m.ts,
        "text": _cap(m.text, MAX_TEXT_CHARS),
        "thinking": _cap(m.thinking, MAX_THINKING_CHARS),
        "model": m.model,
        "usage": m.usage,
        "tool_calls": [{
            "name": t.name,
            "summary": t.summary,
            "input": t.input_text,
            "output": _cap(t.output, MAX_OUTPUT_CHARS),
            "is_error": t.is_error,
        } for t in m.tool_calls],
    }


def session_to_api(s: SessionView) -> dict:
    return {
        "host": s.host,
        "session_id": s.session_id,
        "title": s.title,
        "project": s.project,
        "started_at": s.started_at,
        "last_at": s.last_at,
        "bad_lines": s.bad_lines,
        "source_path": s.source_path,
        "message_count": len(s.messages),
        "models": s.models if s.models is not None
        else _derived_models(s.messages),
        "usage_total": s.usage_total if s.usage_total is not None
        else _summed_usage(s.messages),
        "messages": [message_to_api(m) for m in s.messages],
    }


def summary_to_api(s: SessionSummary) -> dict:
    return {
        "host": s.host,
        "session_id": s.session_id,
        "title": s.title,
        "project": s.project,
        "started_at": s.started_at,
        "last_at": s.last_at,
        "message_count": s.message_count,
        "source_path": s.source_path,
        "models": s.models,
        "usage_total": s.usage_total,
    }
