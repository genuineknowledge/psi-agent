from __future__ import annotations

from pathlib import Path


def test_fusion_flow_skill_uses_workspace_flows_directory() -> None:
    skill = Path("examples/fusion-flow-workspace/skills/fusion-flow/SKILL.md").read_text(encoding="utf-8")

    assert "flows/<task-slug>/<task-slug>.flow.ts" in skill
    assert "flows/<task-slug>/runs/<runId>" in skill
    assert "../../skills/fusion-flow/runtime/agent-flow-core.bundle.mjs" in skill
    assert "corePath" not in skill
    assert "core/runs" not in skill
    assert "../runtime/agent-flow-core.bundle.mjs" not in skill
