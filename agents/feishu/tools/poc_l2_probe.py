"""POC L2 probe tool — operable playbook runner (capability self-check)."""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _poc_l2_impl as _impl
import _runtime_paths as _rp


async def poc_l2_probe(
    playbook_path: str = "",
    playbook_json: str = "",
    method_text: str = "",
) -> str:
    """Run a POC L2 (reproduce / capability) probe against a structured playbook.

    Use when the user asks to reproduce an experiment, verify data authenticity
    by re-running steps, or self-test a Haitun capability playbook (e.g. 方案
    SOP 闭环). Prefer ``playbook_path`` under the agent package.

    Args:
        playbook_path: Relative path to a playbook JSON (agent or workspace root).
            Example: ``skills/poc-l2-reproduce/playbooks/proposal-sop-closed-loop.json``.
        playbook_json: Inline playbook JSON string (alternative to path).
        method_text: Free-text method section. Alone is **not** enough in v1 —
            returns ``l2_not_applicable`` (fail closed; do not claim verified).

    Returns:
        JSON with ``verdict`` (``pass`` / ``fail`` / ``l2_not_applicable``),
        ``operable``, ``steps``, ``gaps``, and a fixed ``disclaimer``.
    """
    agent = Path(str(_rp.agent_dir()))
    workspace = Path(str(_rp.workspace_dir()))
    result = _impl.run_probe(
        playbook_json=playbook_json,
        playbook_path=playbook_path,
        method_text=method_text,
        agent_dir=agent,
        workspace_dir=workspace,
        tools_dir=TOOLS_DIR,
    )
    return json.dumps(result, ensure_ascii=False)
