from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).parents[3] / "agents" / "desktop" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
