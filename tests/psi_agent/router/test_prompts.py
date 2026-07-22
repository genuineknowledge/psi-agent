from __future__ import annotations

from psi_agent.router.prompts import (
    build_aggregation_messages,
    build_branch_messages,
    build_planning_messages,
    build_repair_messages,
    merge_upstream_descriptions,
)


def test_merge_upstream_descriptions_combines_duplicate_socket_entries() -> None:
    descriptions = merge_upstream_descriptions([("sock-a", "research"), ("sock-a", "writing"), ("sock-b", "coding")])

    assert descriptions == [("sock-a", "research; writing"), ("sock-b", "coding")]


def test_planning_prompt_exposes_configured_socket_description_catalog_and_strict_schema() -> None:
    messages = build_planning_messages(
        messages=[{"role": "user", "content": "Investigate the incident"}],
        upstream=[("sock-a", "research"), ("sock-a", "writing")],
    )

    assert messages[:-1] == [{"role": "user", "content": "Investigate the incident"}]
    assert "research" in messages[-1]["content"]
    assert "writing" in messages[-1]["content"]
    assert "sock-a" in messages[-1]["content"]
    assert '"tasks"' in messages[-1]["content"]
    assert '"socket"' in messages[-1]["content"]
    assert '"description"' not in messages[-1]["content"]
    assert "exactly three" in messages[-1]["content"].lower()


def test_repair_prompt_includes_invalid_answer_and_configured_socket_schema() -> None:
    messages = build_repair_messages(
        original_messages=[{"role": "user", "content": "Investigate"}],
        invalid_plan="not JSON",
        upstream=[("sock-a", "research")],
    )

    content = messages[-1]["content"]
    assert "not JSON" in content
    assert "research" in content
    assert "sock-a" in content
    assert '"tasks"' in content
    assert '"socket"' in content
    assert '"description"' not in content
    assert "JSON" in content


def test_branch_prompt_only_carries_prior_final_answers() -> None:
    messages = build_branch_messages(
        original_messages=[{"role": "user", "content": "main"}],
        subtask="third",
        prior_answers=[("first", "answer one"), ("second", "answer two")],
    )

    content = messages[-1]["content"]
    assert "third" in content
    assert "answer one" in content
    assert "answer two" in content
    assert "tool_call" not in content
    assert "reasoning" not in content


def test_aggregation_prompt_receives_answers_but_no_socket_addresses() -> None:
    messages = build_aggregation_messages(
        original_messages=[{"role": "user", "content": "main"}],
        answers=[("first", "answer one"), ("second", "answer two"), ("third", "answer three")],
    )

    content = messages[-1]["content"]
    assert "answer one" in content
    assert "answer two" in content
    assert "answer three" in content
    assert "sock-" not in content
    assert "final answer" in content.lower()
