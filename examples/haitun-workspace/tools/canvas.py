"""Canvas toolset — a live Excalidraw canvas the agent can draw on and inspect.

Exposes ``mcp-excalidraw-server``'s native tools as first-class workspace tools
under a ``canvas_`` prefix (``canvas_create_element``, ``canvas_update_element``,
``canvas_query_elements``, ``canvas_describe_scene``, ``canvas_get_canvas_screenshot``,
``canvas_export_scene``, ``canvas_create_from_mermaid``, ``canvas_align_elements`` and
more — 26 tools covering element CRUD, layout, scene awareness, file I/O, and
snapshots).

How it wires together:

- :mod:`_canvas_impl` builds the ``npx -y mcp-excalidraw-server`` launch command
  and environment. The server speaks MCP over **stdio**; it auto-starts a
  separate long-lived **canvas web server** on ``http://127.0.0.1:3000`` that
  actually holds the drawing, so canvas state persists across the short-lived
  stdio connections :mod:`_mcp` opens per call.
- The :func:`_mcp.mcp` decorator launches the stdio server at import time,
  enumerates its tools, and generates an async function per tool. The default
  ``canvas_`` prefix namespaces Excalidraw's un-prefixed names (``create_element``
  -> ``canvas_create_element``).

Prerequisites: Node.js / ``npx`` on PATH. The first run downloads
``mcp-excalidraw-server`` and boots the canvas server. If Node is missing the
tools are skipped at load time (logged) rather than crashing tool loading.

To *see* the canvas — and to use ``canvas_get_canvas_screenshot``,
``canvas_export_to_image``, or ``canvas_create_from_mermaid`` (all render in the
frontend) — open ``http://127.0.0.1:3000`` in a browser (the ``browser`` tool
can do this).

Env knobs (all optional): ``CANVAS_MCP_PACKAGE``, ``EXPRESS_SERVER_URL``,
``PORT`` / ``HOST``, ``ENABLE_CANVAS_SYNC``, ``EXCALIDRAW_NO_AUTOSTART``,
``EXCALIDRAW_EXPORT_DIR`` (defaults to the workspace dir).
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _canvas_impl as _c
from _mcp import mcp


@mcp
def canvas() -> dict[str, object]:
    """Draw on and inspect a live Excalidraw canvas — a visual surface you and the user
    share. Reach for these tools when the task calls for a diagram or spatial layout
    (architecture diagrams, flowcharts, mind maps, wireframes) rather than prose or code:
    create shapes/text/arrows with ``canvas_create_element`` (or ``canvas_batch_create_elements``),
    connect them with arrows (``startElementId``/``endElementId``), then arrange with the
    layout tools. Inspect your work with ``canvas_describe_scene`` (structured text) or
    ``canvas_get_canvas_screenshot`` (an image), fix overlaps/truncation, and look again —
    draw, look, adjust. Persist results with ``canvas_export_scene`` to a ``.excalidraw``
    file. State lives in a canvas server that stays open for the whole session, so elements
    persist across calls. Ask the user to open http://127.0.0.1:3000 to watch — screenshots
    and image/mermaid rendering need an open browser tab there."""
    npx = _c._find_npx()
    return {"transport": "stdio", "command": npx, "args": _c.build_command(npx)[1:], "env": _c.build_env()}
