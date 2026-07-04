"""MCP tool bridge — call tools on MCP servers with connection pooling.

Connection pooling keeps stateful servers (Playwright) alive across calls.
Use ``mcp_list`` first to discover available tools on a server, then
``mcp_call`` to execute them.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Same import pattern as search.py: inject this directory onto sys.path so
# the underscore-prefixed helper module can be imported at tool-load time.
sys.path.insert(0, str(Path(__file__).parent))
try:
    from _mcp_runtime import call_mcp, close_mcp, list_mcp_tools
finally:
    sys.path.pop(0)


async def mcp_call(server: str, tool: str, args_json: str = "{}") -> str:
    """Call a tool on an MCP server with connection pooling.

    Available MCP servers:
    - **PW**: Playwright browser automation (navigate, click, type, screenshot,
      snapshot, evaluate JS, and more). Stateful — browser session persists
      across calls.
    - **FEISHU**: Feishu/Lark messaging (send text, cards, images, files).
      Supports webhook and API auth modes.
    - **MEDIA**: Image generation, vision understanding, TTS, and STT via
      OpenAI-compatible APIs.

    Before calling, use ``mcp_list(server)`` to discover available tools and
    their exact names.

    Args:
        server: MCP server prefix — ``"PW"``, ``"FEISHU"``, or ``"MEDIA"``.
        tool: Name of the tool to call (case-sensitive).
        args_json: JSON string of arguments for the tool. Default ``"{}"``.

    Returns:
        Tool result as a string.

    Example:
        mcp_call("PW", "browser_navigate", '{"url": "https://example.com"}')
        mcp_call("FEISHU", "feishu_send_text", '{"content": "Hello from Haitun!"}')
        mcp_call("MEDIA", "generate_image", '{"prompt": "A dolphin jumping out of waves", "output_path": "dolphin.png"}')
    """
    return await call_mcp(server, tool, args_json)


async def mcp_list(server: str) -> str:
    """List all available tools on an MCP server.

    Call this first when exploring a server you haven't used yet.

    Args:
        server: MCP server prefix — ``"PW"``, ``"FEISHU"``, or ``"MEDIA"``.

    Returns:
        List of tool names and descriptions, one per line.

    Example:
        mcp_list("PW")
    """
    return await list_mcp_tools(server)


async def mcp_close(server: str = "*") -> str:
    """Close MCP connection(s).

    Use this to force a fresh browser session, or to clean up when done.

    Args:
        server: Server prefix to close, or ``"*"`` (default) to close all.

    Returns:
        Confirmation message.

    Example:
        mcp_close("PW")     # close Playwright only
        mcp_close("*")      # close all MCP connections
    """
    return await close_mcp(server)
