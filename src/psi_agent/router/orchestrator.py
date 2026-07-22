"""Serial branch orchestration and Session-owned tool round trips."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol
from uuid import uuid4

import anyio
from loguru import logger

from psi_agent.router.client import RouterClient, UpstreamResult
from psi_agent.router.planner import Planner
from psi_agent.router.prompts import build_aggregation_messages, build_branch_messages
from psi_agent.router.protocol import (
    BranchState,
    BranchStatus,
    PlannedTask,
    RouterConfig,
    RoutingRun,
    RoutingStatus,
    ToolCallOrigin,
)


class OrchestrationError(Exception):
    """A routing run cannot safely continue."""


class _CompletionClient(Protocol):
    async def complete(self, *, socket: str, body: dict[str, Any], **options: Any) -> UpstreamResult: ...


class _TaskPlanner(Protocol):
    async def plan(self, *, messages: list[dict[str, Any]]) -> tuple[PlannedTask, PlannedTask, PlannedTask]: ...


class Orchestrator:
    """Own private runs and advance exactly one branch at a time."""

    def __init__(
        self,
        *,
        config: RouterConfig,
        client: _CompletionClient | None = None,
        planner: _TaskPlanner | None = None,
    ) -> None:
        self.config = config
        self.client = client if client is not None else RouterClient()
        self.planner = (
            planner
            if planner is not None
            else Planner(
                client=self.client,
                router_socket=config.router_socket,
                upstream=config.upstream,
                timeout=config.router_timeout,
            )
        )
        self.runs: dict[str, RoutingRun] = {}

    async def process(self, *, body: dict[str, Any]) -> UpstreamResult:
        """Start or continue the run selected by ``routing.session_id``."""

        session_id = self._session_id(body)
        messages = self._messages(body)
        tools = self._tools(body)
        now = anyio.current_time()
        run = self.runs.get(session_id)
        if run is not None and now - run.last_active_at > self.config.run_ttl:
            logger.warning(f"Router run expired for Session {session_id!r}")
            self.runs.pop(session_id, None)
            raise OrchestrationError("Routing run expired while waiting for continuation")

        keep_run = False
        try:
            if run is None:
                if messages and messages[-1].get("role") == "tool":
                    raise OrchestrationError("No active routing run exists for this tool continuation")
                tasks = await self.planner.plan(messages=messages)
                branches = [BranchState.from_task(task) for task in tasks]
                run = RoutingRun.create(
                    run_id=uuid4().hex,
                    session_id=session_id,
                    original_messages=deepcopy(messages),
                    tools=deepcopy(tools),
                    branches=branches,
                    last_active_at=now,
                )
                self.runs[session_id] = run
                logger.info(f"Created Router run {run.run_id} for Session {session_id!r}")
            else:
                self._accept_tool_results(run=run, messages=messages)
                run.last_active_at = now

            result = await self._advance(run=run, request_body=body)
            keep_run = result.finish_reason == "tool_calls"
            return result
        finally:
            if run is not None and not keep_run and self.runs.get(session_id) is run:
                if run.status is not RoutingStatus.COMPLETED:
                    run.status = RoutingStatus.FAILED
                self.runs.pop(session_id, None)

    def discard(self, session_id: str) -> None:
        """Forget one run before a server-side default fallback."""

        self.runs.pop(session_id, None)

    def clear(self) -> None:
        """Forget all private runs during Router shutdown."""

        self.runs.clear()

    async def _advance(self, *, run: RoutingRun, request_body: dict[str, Any]) -> UpstreamResult:
        while run.current_branch < len(run.branches):
            branch = run.branches[run.current_branch]
            if branch.status is not BranchStatus.READY:
                raise OrchestrationError(
                    f"Branch {run.current_branch + 1} cannot run from state {branch.status.value!r}"
                )
            if not branch.messages:
                prior_answers = [(prior.subtask, prior.final_answer) for prior in run.branches[: run.current_branch]]
                branch.messages = build_branch_messages(
                    original_messages=run.original_messages,
                    subtask=branch.subtask,
                    prior_answers=prior_answers,
                )

            logger.info(
                f"Running Router branch {run.current_branch + 1} on socket {branch.socket!r} for run {run.run_id}"
            )
            result = await self.client.complete(
                socket=branch.socket,
                body=self._completion_body(
                    request_body=request_body,
                    messages=branch.messages,
                    tools=run.tools,
                ),
                timeout=self.config.branch_timeout,
            )
            if result.finish_reason == "tool_calls":
                return self._publish_tool_calls(run=run, branch=branch, result=result)
            if result.finish_reason != "stop":
                raise OrchestrationError(f"Branch returned unsupported finish reason {result.finish_reason!r}")
            if not result.content.strip():
                raise OrchestrationError("Branch returned no usable final answer")

            branch.messages.append({"role": "assistant", "content": result.content})
            branch.final_answer = result.content
            branch.status = BranchStatus.COMPLETED
            logger.info(f"Completed Router branch {run.current_branch + 1} for run {run.run_id}")
            run.current_branch += 1
            if run.current_branch < len(run.branches):
                run.branches[run.current_branch].status = BranchStatus.READY

        run.status = RoutingStatus.AGGREGATING
        answers = [(branch.subtask, branch.final_answer) for branch in run.branches]
        aggregate = await self.client.complete(
            socket=self.config.router_socket,
            body=self._completion_body(
                request_body=request_body,
                messages=build_aggregation_messages(
                    original_messages=run.original_messages,
                    answers=answers,
                ),
                tools=None,
            ),
            timeout=self.config.aggregate_timeout,
        )
        if aggregate.finish_reason != "stop" or not aggregate.content.strip():
            raise OrchestrationError("Aggregation returned no usable final answer")
        run.status = RoutingStatus.COMPLETED
        logger.info(f"Completed Router run {run.run_id}")
        return UpstreamResult(content=aggregate.content, finish_reason="stop")

    def _publish_tool_calls(self, *, run: RoutingRun, branch: BranchState, result: UpstreamResult) -> UpstreamResult:
        if not result.tool_calls:
            raise OrchestrationError("Branch requested tools without complete tool calls")
        if branch.tool_rounds >= self.config.max_tool_rounds:
            raise OrchestrationError("Branch exceeded the maximum tool rounds")
        if run.pending_tool_calls:
            raise OrchestrationError("A branch requested tools while earlier calls are still pending")

        branch.tool_rounds += 1
        branch.messages.append(
            {
                "role": "assistant",
                "content": result.content or None,
                "tool_calls": deepcopy(result.tool_calls),
            }
        )
        published_calls: list[dict[str, Any]] = []
        for call_index, upstream_call in enumerate(result.tool_calls):
            upstream_id = upstream_call.get("id")
            if not isinstance(upstream_id, str) or not upstream_id:
                raise OrchestrationError("Branch returned a tool call without a valid ID")
            global_id = f"router_{run.run_id}_{run.current_branch}_{branch.tool_rounds}_{call_index}"
            published_call = deepcopy(upstream_call)
            published_call["id"] = global_id
            published_calls.append(published_call)
            run.pending_tool_calls[global_id] = ToolCallOrigin(
                branch_index=run.current_branch,
                upstream_tool_call_id=upstream_id,
            )
            logger.debug(
                f"Mapped Router tool call {global_id!r} to branch {run.current_branch + 1} for run {run.run_id}"
            )

        branch.status = BranchStatus.WAITING_TOOLS
        run.last_active_at = anyio.current_time()
        return UpstreamResult(
            content=f"Processing subtask {run.current_branch + 1}: {branch.subtask}",
            tool_calls=published_calls,
            finish_reason="tool_calls",
        )

    @staticmethod
    def _accept_tool_results(*, run: RoutingRun, messages: list[dict[str, Any]]) -> None:
        branch = run.branches[run.current_branch]
        if branch.status is not BranchStatus.WAITING_TOOLS or not run.pending_tool_calls:
            raise OrchestrationError("Active routing run is not waiting for tool results")

        trailing_results: list[dict[str, Any]] = []
        for message in reversed(messages):
            if message.get("role") != "tool":
                break
            trailing_results.append(message)
        trailing_results.reverse()
        returned_ids = [message.get("tool_call_id") for message in trailing_results]
        if (
            not trailing_results
            or any(not isinstance(call_id, str) for call_id in returned_ids)
            or len(set(returned_ids)) != len(returned_ids)
            or set(returned_ids) != set(run.pending_tool_calls)
        ):
            raise OrchestrationError("Returned tool results do not match the pending tool calls")

        for message in trailing_results:
            global_id = message["tool_call_id"]
            origin = run.pending_tool_calls[global_id]
            if origin.branch_index != run.current_branch:
                raise OrchestrationError("Returned tool result belongs to a different branch")
            restored = deepcopy(message)
            restored["tool_call_id"] = origin.upstream_tool_call_id
            branch.messages.append(restored)
            logger.debug(
                f"Restored Router tool call {global_id!r} for branch {run.current_branch + 1} in run {run.run_id}"
            )

        run.pending_tool_calls.clear()
        branch.status = BranchStatus.READY

    @staticmethod
    def _completion_body(
        *,
        request_body: dict[str, Any],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        excluded = {"messages", "tools", "routing", "model"}
        if tools is None:
            excluded.update({"parallel_tool_calls", "tool_choice"})
        body = {key: deepcopy(value) for key, value in request_body.items() if key not in excluded}
        body["messages"] = deepcopy(messages)
        body["stream"] = True
        if tools is not None:
            body["tools"] = deepcopy(tools)
        return body

    @staticmethod
    def _session_id(body: dict[str, Any]) -> str:
        routing = body.get("routing")
        if not isinstance(routing, dict):
            raise OrchestrationError("Request routing metadata must be an object")
        session_id = routing.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise OrchestrationError("Request routing metadata must contain a non-empty session_id")
        return session_id.strip()

    @staticmethod
    def _messages(body: dict[str, Any]) -> list[dict[str, Any]]:
        messages = body.get("messages")
        if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
            raise OrchestrationError("Request messages must be a list of objects")
        return messages

    @staticmethod
    def _tools(body: dict[str, Any]) -> list[dict[str, Any]]:
        tools = body.get("tools", [])
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise OrchestrationError("Request tools must be a list of objects")
        return tools


__all__ = ["OrchestrationError", "Orchestrator"]
