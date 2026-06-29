from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _mcp import mcp
finally:
    sys.path.pop(0)


@mcp
def serper() -> dict[str, object]:
    return {
        "type": "local",
        "command": ["uvx", "serper-mcp-server"],
        "env": {"SERPER_API_KEY": "6807655101160dad0d00311d01ee1a1471517ba7"},
    }
