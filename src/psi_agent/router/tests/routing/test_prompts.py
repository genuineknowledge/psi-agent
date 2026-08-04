from __future__ import annotations

import json

from psi_agent.router.routing.prompts import build_selector_messages


def _prompt_input() -> dict[str, list[dict[str, str]]]:
    return {
        "candidates": [
            {"candidate_id": "general", "description": "general conversation"},
            {"candidate_id": "code", "description": "coding and debugging"},
        ],
        "conversation": [{"role": "user", "content": "debug this program"}],
        "available_tools": [{"name": "read_file", "description": "read a file"}],
    }


def test_build_selector_messages_contains_structured_public_context() -> None:
    prompt_input = _prompt_input()

    messages = build_selector_messages(**prompt_input)

    assert [message["role"] for message in messages] == ["system", "user"]
    assert "candidate_id" in messages[0]["content"]
    payload = json.loads(messages[1]["content"])
    assert payload == prompt_input
    assert "socket" not in messages[1]["content"]
