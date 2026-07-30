"""Strict socket-aware task planning with one format-repair request."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from psi_agent.router.aggregation.prompts import build_planning_messages
from psi_agent.router.client import UpstreamResult
from psi_agent.router.protocol import PlannedTask


class PlanValidationError(ValueError):
    """A planner response cannot safely select three configured backends."""


class _CompletionClient(Protocol):
    async def complete(self, *, socket: str, body: dict[str, Any], **options: Any) -> UpstreamResult: ...


_JSON_FENCE = re.compile(r"^```(?:json)?\s*\n(?P<content>[\s\S]*?)\n```$", re.IGNORECASE)


def parse_plan(content: str, *, allowed_sockets: set[str]) -> tuple[PlannedTask, ...]:
    """Decode a dynamically sized plan without accepting invented sockets."""

    candidate = content.strip()
    match = _JSON_FENCE.match(candidate)
    if match is not None:
        candidate = match.group("content")
    try:
        decoded = json.loads(candidate)
    except json.JSONDecodeError as error:
        raise PlanValidationError(f"Planner output is not valid JSON: {error.msg[:200]}") from error
    if not isinstance(decoded, dict):
        raise PlanValidationError("Planner output root must be an object")
    if set(decoded) != {"tasks"}:
        raise PlanValidationError("Planner output root must contain only the tasks key")
    raw_tasks = decoded.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlanValidationError("Planner output must contain at least one task")

    tasks: list[PlannedTask] = []
    selected_sockets: set[str] = set()
    for index, raw_task in enumerate(raw_tasks, start=1):
        if not isinstance(raw_task, dict):
            raise PlanValidationError(f"Planner task {index} must be an object")
        if set(raw_task) - {"task_type", "subtask", "socket"} or not {"subtask", "socket"} <= set(raw_task):
            raise PlanValidationError(f"Planner task {index} must contain subtask and socket keys")
        subtask = raw_task.get("subtask")
        socket = raw_task.get("socket")
        task_type = raw_task.get("task_type", "general")
        if not isinstance(subtask, str) or not subtask.strip():
            raise PlanValidationError(f"Planner task {index} subtask must be a non-empty string")
        if not isinstance(socket, str):
            raise PlanValidationError(f"Planner task {index} socket must be a string")
        if not isinstance(task_type, str) or not task_type.strip():
            raise PlanValidationError(f"Planner task {index} task_type must be a non-empty string")
        subtask = subtask.strip()
        socket = socket.strip()
        if socket not in allowed_sockets:
            raise PlanValidationError(f"Planner task {index} selected an unconfigured socket")
        if socket in selected_sockets:
            raise PlanValidationError(
                f"Planner selected socket {socket!r} more than once; each socket may handle only one subtask"
            )
        selected_sockets.add(socket)
        tasks.append(PlannedTask(subtask=subtask, socket=socket, task_type=task_type.strip()))
    return tuple(tasks)


@dataclass
class Planner:
    """Ask the Router planning backend for validated socket-selected subtasks."""

    client: _CompletionClient
    router_socket: str
    upstream: tuple[tuple[str, str], ...] | list[tuple[str, str]]
    timeout: float | None

    async def plan(
        self, *, messages: list[dict[str, Any]], max_context_length: int | None = None
    ) -> tuple[PlannedTask, ...]:
        """Return a valid plan, allowing one request solely to repair its structure."""

        result = await self.client.complete(
            socket=self.router_socket,
            body={
                "messages": build_planning_messages(
                    messages=messages, upstream=self.upstream, max_context_length=max_context_length
                ),
                "stream": True,
            },
            timeout=self.timeout,
        )
        allowed_sockets = {socket for socket, _ in self.upstream}
        # A malformed plan is handled by the HTTP boundary, which immediately
        # falls back to default_socket.  Do not spend another upstream round
        # attempting to repair a response that may be empty or truncated.
        return parse_plan(result.content, allowed_sockets=allowed_sockets)


__all__ = ["PlanValidationError", "Planner", "parse_plan"]
