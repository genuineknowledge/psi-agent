"""Pure prompt builders used by the serial Router roles."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def merge_upstream_descriptions(upstream: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> list[tuple[str, str]]:
    """Preserve socket order while combining their human-readable capabilities."""

    merged: dict[str, list[str]] = {}
    for socket, description in upstream:
        merged.setdefault(socket, []).append(description)
    return [(socket, "; ".join(descriptions)) for socket, descriptions in merged.items()]


def _copy_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy only valid Chat Completions message objects from untrusted input."""

    return [dict(message) for message in messages if isinstance(message, dict)]


def _socket_catalog(upstream: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> str:
    return "\n".join(
        f'- socket "{socket}": {description}' for socket, description in merge_upstream_descriptions(upstream)
    )


def build_planning_messages(
    *,
    messages: list[dict[str, Any]],
    upstream: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    max_context_length: int | None = None,
) -> list[dict[str, Any]]:
    """Ask the planning model to choose an appropriate number of socket-selected subtasks."""

    result = _copy_messages(messages)
    result.append(
        {
            "role": "user",
            "content": (
                "Classify the request type (for example: chat, coding, research, data-analysis, tool-operation, "
                "or writing), then decompose it into the smallest useful set of complementary subtasks. "
                "Configured backend sockets and capabilities are:\n"
                f"{_socket_catalog(upstream)}\n\n"
                "Return JSON only in this exact shape: "
                '{"tasks":[{"task_type":"...","subtask":"...","socket":"..."}]}. '
                "Use one or more tasks as appropriate. Each socket must exactly match one configured socket."
                + (
                    f" Maximum context length for this request: {max_context_length} tokens."
                    if max_context_length is not None
                    else ""
                )
            ),
        }
    )
    return result


def build_repair_messages(
    *,
    original_messages: list[dict[str, Any]],
    invalid_plan: str,
    upstream: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    max_context_length: int | None = None,
) -> list[dict[str, Any]]:
    """Ask once for a strictly formatted replacement plan."""

    result = _copy_messages(original_messages)
    result.append(
        {
            "role": "user",
            "content": (
                "Your prior routing plan was invalid:\n"
                f"{invalid_plan}\n\nConfigured backend sockets and capabilities are:\n{_socket_catalog(upstream)}\n\n"
                "Repair it. Return JSON only in this exact shape: "
                '{"tasks":[{"task_type":"...","subtask":"...","socket":"..."}]}. '
                "Use one or more tasks as appropriate. Each socket must exactly match one configured socket."
                + (
                    f" Maximum context length for this request: {max_context_length} tokens."
                    if max_context_length is not None
                    else ""
                )
            ),
        }
    )
    return result


def build_branch_messages(
    *, original_messages: list[dict[str, Any]], subtask: str, prior_answers: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    """Build isolated branch context from the original request and completed answers."""

    result = _copy_messages(original_messages)
    prior_text = "\n".join(f"Completed subtask ({name}):\n{answer}" for name, answer in prior_answers)
    result.append(
        {
            "role": "user",
            "content": (
                f"Your assigned subtask is: {subtask}\n\n"
                f"Prior final answers, if any:\n{prior_text or '(none)'}\n\n"
                "Complete only your assigned subtask. Return a concise final answer for the next specialist."
            ),
        }
    )
    return result


def build_aggregation_messages(
    *, original_messages: list[dict[str, Any]], answers: Sequence[tuple[str, str] | dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ask the routing model to synthesize completed branch answers."""

    result = _copy_messages(original_messages)
    answer_lines: list[str] = []
    for answer in answers:
        if isinstance(answer, tuple) and len(answer) == 2:
            subtask, content = answer
            answer_lines.append(f"Subtask ({subtask}) final answer:\n{content}")
            continue
        if isinstance(answer, dict):
            subtask = answer.get("subtask")
            content = answer.get("content")
            tool_calls = answer.get("tool_calls")
            if isinstance(subtask, str) and isinstance(content, str):
                answer_lines.append(
                    f"Subtask ({subtask}) final answer:\n{content}\n"
                    + (
                        f"Tool calls observed for this subtask:\n{tool_calls}"
                        if tool_calls
                        else "Tool calls observed for this subtask:\n(none)"
                    )
                )
    answer_text = "\n\n".join(answer_lines)
    result.append(
        {
            "role": "user",
            "content": (
                "Synthesize the following specialist results into one accurate, self-contained final answer. "
                "The child tool calls shown below are observations/material for this final synthesis, not calls that "
                "the Router should execute. Do not mention routing or the specialists. Do not output JSON, Markdown "
                "fences, thoughts, task lists, backend sockets, or planning instructions. Return only the answer "
                "intended for the end user.\n\n"
                f"{answer_text}"
            ),
        }
    )
    return result
