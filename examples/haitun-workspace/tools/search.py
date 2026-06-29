from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _mcp import mcp
finally:
    sys.path.pop(0)


@mcp
def serper() -> dict[str, object]:
    config: dict[str, object] = {
        "type": "local",
        "command": ["uvx", "serper-mcp-server"],
    }
    if api_key := os.environ.get("SERPER_API_KEY"):
        config["env"] = {"SERPER_API_KEY": api_key}
    return config
