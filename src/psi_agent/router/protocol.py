"""Typed state and configuration models for serial routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast


class BranchStatus(StrEnum):
    """Lifecycle states for one private subtask branch."""

    PENDING = "pending"
    READY = "ready"
    WAITING_TOOLS = "waiting_tools"
    COMPLETED = "completed"
    FAILED = "failed"


class RoutingStatus(StrEnum):
    """Lifecycle states for a complete three-branch routing run."""

    PLANNING = "planning"
    RUNNING = "running"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class RouterConfig:
    """Validated, immutable socket-only configuration for one Router service."""

    session_socket: str
    router_socket: str
    default_socket: str
    upstream: tuple[tuple[str, str], ...] | list[tuple[str, str]]
    max_tool_rounds: int = 10
    router_timeout: float | None = 60.0
    branch_timeout: float | None = None
    aggregate_timeout: float | None = None
    run_ttl: float = 1_800.0

    def __post_init__(self) -> None:
        for name in ("session_socket", "router_socket", "default_socket"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.default_socket == self.session_socket:
            raise ValueError("default_socket must not equal session_socket")
        if not isinstance(self.upstream, (list, tuple)):
            raise ValueError("upstream must be a list or tuple of (socket, description) tuples")
        if not self.upstream:
            raise ValueError("upstream must contain at least one socket")

        validated_upstream: list[tuple[str, str]] = []
        for item in self.upstream:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("upstream entries must be (socket, description) tuples")
            socket, description = item
            if not isinstance(socket, str) or not socket.strip():
                raise ValueError("upstream socket must be a non-empty string")
            if not isinstance(description, str) or not description.strip():
                raise ValueError("upstream description must be a non-empty string")
            validated_upstream.append((socket, description))
        object.__setattr__(self, "upstream", tuple(validated_upstream))

        if not isinstance(self.max_tool_rounds, int) or isinstance(self.max_tool_rounds, bool) or self.max_tool_rounds <= 0:
            raise ValueError("max_tool_rounds must be a positive integer")
        for name in ("router_timeout", "branch_timeout", "aggregate_timeout"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0):
                raise ValueError(f"{name} must be a positive number or None")
        if not isinstance(self.run_ttl, (int, float)) or isinstance(self.run_ttl, bool) or self.run_ttl <= 0:
            raise ValueError("run_ttl must be a positive number")


@dataclass(frozen=True)
class PlannedTask:
    """One validated planner-assigned subtask."""

    subtask: str
    socket: str


@dataclass
class BranchState:
    """Mutable private state for a single serially executed subtask."""

    subtask: str
    socket: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    status: BranchStatus = BranchStatus.PENDING
    tool_rounds: int = 0
    final_answer: str = ""

    @classmethod
    def from_task(cls, task: PlannedTask) -> BranchState:
        """Create an inactive branch from a validated planned task."""

        return cls(subtask=task.subtask, socket=task.socket, messages=[])


@dataclass(frozen=True)
class ToolCallOrigin:
    """Maps a Router-visible tool-call ID to its branch-local upstream ID."""

    branch_index: int
    upstream_tool_call_id: str


@dataclass
class RoutingRun:
    """All temporary state for exactly three serial branches in one Session."""

    run_id: str
    session_id: str
    original_messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    branches: tuple[BranchState, BranchState, BranchState]
    current_branch: int = 0
    pending_tool_calls: dict[str, ToolCallOrigin] = field(default_factory=dict)
    last_active_at: float = 0.0
    status: RoutingStatus = RoutingStatus.RUNNING

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        session_id: str,
        original_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        branches: list[BranchState],
        last_active_at: float = 0.0,
    ) -> RoutingRun:
        """Validate three branches and make only the first one ready."""

        if len(branches) != 3:
            raise ValueError("RoutingRun requires exactly three branches")
        if len({id(branch) for branch in branches}) != 3:
            raise ValueError("RoutingRun requires three distinct BranchState instances")
        for index, branch in enumerate(branches):
            branch.status = BranchStatus.READY if index == 0 else BranchStatus.PENDING
        return cls(
            run_id=run_id,
            session_id=session_id,
            original_messages=original_messages,
            tools=tools,
            branches=cast(tuple[BranchState, BranchState, BranchState], tuple(branches)),
            last_active_at=last_active_at,
        )
