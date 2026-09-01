"""Build the system prompt for the Serper MCP workspace."""

from __future__ import annotations

import inspect

import anyio

# Context compaction is engine behavior, not product expression: this block was
# byte-for-byte identical in 11 of the 12 example workspaces, so a fix had to be
# made eleven times.  It now lives in the kernel.  Re-exported rather than
# imported inline because the hook lookup is `getattr(module, "compact_history")`
# -- the name has to be resolvable as a module attribute here.  A workspace that
# needs different behavior defines `compact_history` itself further down; the
# later binding wins.
from psi_agent.session._compaction import (  # noqa: F401
    RECENT_TURNS_KEPT_VERBATIM,
    SUMMARIZE_TASK,
    SUMMARY_MAX_CHARS,
    TRANSCRIPT_IS_DATA,
    _cap_summary,
    _escape_transcript,
    compact_history,
)


async def system_prompt_builder() -> str:
    current_file = anyio.Path(inspect.getfile(system_prompt_builder))
    workspace_root = current_file.parent.parent

    return f"""You are a helpful AI assistant with web search capabilities.

You have access to a `serper` tool that searches Google via the Serper API.
Use it to look up current information, facts, and web content.

## Workspace
Location: {workspace_root}

## Tools
- serper: search the web via Google Serper API"""
