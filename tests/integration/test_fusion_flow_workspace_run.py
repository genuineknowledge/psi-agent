from __future__ import annotations

from pathlib import Path

import pytest

from psi_agent.run import run_once
from tests.integration.conftest import MockAIServer

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.anyio
async def test_run_once_uses_fusion_flow_workspace_prompt(
    mock_ai_server: MockAIServer,
) -> None:
    mock_ai_server.set_responses(
        [
            '{"id":"test","object":"chat.completion.chunk","created":0,"model":"test","choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":"stop"}]}',
        ]
    )
    base_url = await mock_ai_server.start()

    workspace = REPO_ROOT / "examples" / "fusion-flow-workspace"
    result = await run_once(
        workspace=str(workspace),
        message="Build a workflow.",
        ai_socket=base_url,
        model="test-model",
    )

    assert result.text == "OK"
    assert mock_ai_server.request_bodies
    system_prompt = mock_ai_server.request_bodies[0]["messages"][0]["content"]
    assert "Fusion Flow Trigger" in system_prompt
    assert "skills/fusion-flow/SKILL.md" in system_prompt
    assert "FLOW_ENGINE=psi" in system_prompt
