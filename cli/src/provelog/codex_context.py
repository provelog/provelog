"""Codex app context that must never be shown as a user message.

The Codex desktop/client writes selected-page and environment snapshots into
the rollout as `role: "user"` messages. They are model context, not something
the person typed. The CLI importer filters them at capture time; the viewer
filters them again so already-captured sessions (append-only evidence) display
cleanly without rewriting a single record.
"""
from __future__ import annotations

_INTERNAL_PREFIXES = (
    "<environment_context>",
    "<external_codex_apps_",
    "<codex_apps_",
    "<app-context>",
    "<multi_agent_",
    "<developer",
                                                                              
    "# Files mentioned by the user:",
    "Files mentioned by the user:",
                                                                        
    "The following is the Codex agent history",
                                                                          
    "[Request interrupted by user]",
                                                                            
                                     
    "(not recorded - this part of the conversation happened before",
    "This is a preview of the conversation only.",
)


def is_internal_user_text(text: str) -> bool:
    return text.lstrip().startswith(_INTERNAL_PREFIXES)
