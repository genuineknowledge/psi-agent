from __future__ import annotations

from psi_agent.router.models import Upstream
from psi_agent.router.prompts import build_routing_messages


def test_build_routing_messages_keeps_prompt_in_dedicated_module() -> None:
    targets = (
        Upstream("secret-model", "http://secret:7001", "simple tasks"),
        Upstream("other-model", "http://other:7002", "complex reasoning"),
    )
    messages = build_routing_messages("[USER]\nsolve this", targets)
    rendered = "\n".join(message["content"] for message in messages)
    assert "Candidate 0: simple tasks" in rendered
    assert "Candidate 1: complex reasoning" in rendered
    assert "secret-model" not in rendered
    assert "http://secret" not in rendered
    assert messages[-1] == {"role": "user", "content": "[USER]\nsolve this"}
