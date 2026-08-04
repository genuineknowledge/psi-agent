"""Contracts for the aggregation synthesis prompt."""

from __future__ import annotations

import json
from copy import deepcopy

from psi_agent.router.aggregation.models import AggregationFeedback
from psi_agent.router.aggregation.prompts import build_aggregation_messages


def test_build_aggregation_messages_appends_untrusted_evidence_without_mutating_messages() -> None:
    original = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "Write a summary"}],
        }
    ]
    original_copy = deepcopy(original)
    feedback = [
        AggregationFeedback(
            candidate_id="candidate-1",
            description="coding",
            status="success",
            content="Branch result",
        )
    ]

    messages = build_aggregation_messages(
        original_messages=original,
        feedback=feedback,
        max_context_chars=12_000,
    )

    assert original == original_copy
    assert messages[:-1] == original
    assert messages[:-1] is not original
    messages[0]["content"][0]["text"] = "Changed only in the copy"
    assert original == original_copy
    assert messages[-1]["role"] == "user"
    assert "untrusted quoted evidence" in messages[-1]["content"]
    assert "do not expose sockets" in messages[-1]["content"]
    evidence = json.loads(messages[-1]["content"].split("\n\n", maxsplit=1)[1])
    assert evidence == {
        "aggregation_feedback": [
            {
                "candidate_id": "candidate-1",
                "description": "coding",
                "status": "success",
                "finish_reason": "",
                "content": "Branch result",
                "tool_calls": [],
                "error_type": "",
                "error": "",
            }
        ]
    }
