"""CLI wrapper for session export — for scripts and future Gateway UI integration.

Usage:
    python export_session.py --session-id ID --format markdown --output exports/chat.md
    python export_session.py --format json --output out/session.json
"""

from __future__ import annotations

# ruff: noqa: E402, T201
import argparse
import asyncio
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _session_helpers as _h


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a workspace session transcript to a file.")
    parser.add_argument("--session-id", default="", help="Session id (default: current process id if any)")
    parser.add_argument(
        "--format",
        default="markdown",
        choices=list(_h.EXPORT_FORMATS),
        help="Export format",
    )
    parser.add_argument("--output", required=True, help="Output file path")
    parser.add_argument("--workspace", default="", help="Workspace root")
    parser.add_argument(
        "--include-tool-messages",
        action="store_true",
        help="Include tool messages in markdown/json/text exports",
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    result = await _h.export_session(
        session_id=args.session_id,
        output_path=args.output,
        export_format=args.format,
        workspace_raw=args.workspace,
        include_tool_messages=args.include_tool_messages,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
