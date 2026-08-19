from __future__ import annotations

import sys
from pathlib import Path

_WORKFLOW_SKILL = Path(__file__).parents[3] / "examples" / "haitun-workspace" / "skills" / "workflow"
if str(_WORKFLOW_SKILL) not in sys.path:
    sys.path.insert(0, str(_WORKFLOW_SKILL))
