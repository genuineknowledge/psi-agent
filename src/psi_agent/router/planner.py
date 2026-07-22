"""Strict three-task planning with one format-repair request."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol, cast

from psi_agent.router.client import UpstreamResult
from psi_agent.router.prompts import build_planning_messages, build_repair_messages
from psi_agent.router.protocol import PlannedTask


class PlanValidationError(ValueError):
    """A planner response cannot safely select three configured backends."""


class _CompletionClient(Protocol):
    async def complete(self, *, socket: str, body: dict[str, Any], **options: Any) -> UpstreamResult: ...


_JSON_FENCE = re.compile(r"^```(?:json)?\s*\n(?P<content>[\s\S]*?)\n```$", re.IGNORECASE)


def parse_plan(content: str, *, allowed_sockets: set[str]) -> tuple[PlannedTask, PlannedTask, PlannedTask]:
    """Decode and validate a planner response without accepting invented sockets."""

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
    if not isinstance(raw_tasks, list) or len(raw_tasks) != 3:
        raise PlanValidationError("Planner output must contain exactly three tasks")

    tasks: list[PlannedTask] = []
    for index, raw_task in enumerate(raw_tasks, start=1):
        if not isinstance(raw_task, dict):
            raise PlanValidationError(f"Planner task {index} must be an object")
        if set(raw_task) != {"subtask", "socket"}:
            raise PlanValidationError(f"Planner task {index} must contain only subtask and socket keys")
        subtask = raw_task.get("subtask")
        socket = raw_task.get("socket")
        if not isinstance(subtask, str) or not subtask.strip():
            raise PlanValidationError(f"Planner task {index} subtask must be a non-empty string")
        if not isinstance(socket, str):
            raise PlanValidationError(f"Planner task {index} socket must be a string")
        subtask = subtask.strip()
        socket = socket.strip()
        if socket not in allowed_sockets:
            raise PlanValidationError(f"Planner task {index} selected an unconfigured socket")
        tasks.append(PlannedTask(subtask=subtask, socket=socket))
    return cast(tuple[PlannedTask, PlannedTask, PlannedTask], tuple(tasks))


@dataclass
class Planner:
    """Ask the Router planning backend for three validated serial subtasks."""

    client: _CompletionClient
    router_socket: str
    upstream: tuple[tuple[str, str], ...] | list[tuple[str, str]]
    timeout: float | None

    async def plan(self, *, messages: list[dict[str, Any]]) -> tuple[PlannedTask, PlannedTask, PlannedTask]:
        """Return a valid plan, allowing one request solely to repair its structure."""

        result = await self.client.complete(
            socket=self.router_socket,
            body={"messages": build_planning_messages(messages=messages, upstream=self.upstream), "stream": True},
            timeout=self.timeout,
        )
        allowed_sockets = {socket for socket, _ in self.upstream}
        try:
            return parse_plan(result.content, allowed_sockets=allowed_sockets)
        except PlanValidationError:
            repaired = await self.client.complete(
                socket=self.router_socket,
                body={
                    "messages": build_repair_messages(
                        original_messages=messages, invalid_plan=result.content[:2_000], upstream=self.upstream
                    ),
                    "stream": True,
                },
                timeout=self.timeout,
            )
            return parse_plan(repaired.content, allowed_sockets=allowed_sockets)


__all__ = ["PlanValidationError", "Planner", "parse_plan"]
