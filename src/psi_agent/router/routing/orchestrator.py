"""Single-backend routing strategy for the Router service."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Protocol

from loguru import logger

from psi_agent.router.aggregation import OrchestrationError, Planner, PlanValidationError, parse_plan
from psi_agent.router.client import RouterClient, UpstreamResult
from psi_agent.router.protocol import PlannedTask, RouterConfig

from .prompts import build_routing_messages


class _CompletionClient(Protocol):
    async def complete(self, *, socket: str, body: dict[str, Any], **options: Any) -> UpstreamResult: ...


class RoutingOrchestrator:
    """Select one configured upstream socket and forward the full request to it."""

    def __init__(self, *, config: RouterConfig, client: _CompletionClient | None = None) -> None:
        self.config = config
        self.client = client if client is not None else RouterClient()

    async def process(self, *, body: dict[str, Any]) -> UpstreamResult:
        """Choose one upstream through router_socket, then execute the full request there."""

        messages = self._messages(body)
        tools = self._tools(body)
        route_result = await self.client.complete(
            socket=self.config.router_socket,
            body={
                "messages": build_routing_messages(messages=messages, upstream=self.config.upstream),
                "stream": True,
            },
            timeout=self.config.router_timeout,
        )
        selected_socket = self._selected_socket(route_result.content)
        logger.info(f"Router selected socket {selected_socket!r} in routing mode")
        return await self.client.complete(
            socket=selected_socket,
            body=self._completion_body(request_body=body, messages=messages, tools=tools),
            timeout=self.config.branch_timeout,
        )

    def discard(self, session_id: str) -> None:
        """Routing mode keeps no per-session state."""

        del session_id

    def clear(self) -> None:
        """Routing mode keeps no temporary state."""

    def _selected_socket(self, content: str) -> str:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Routing selector returned malformed JSON; using default socket")
            return self.config.default_socket
        if not isinstance(parsed, dict) or set(parsed) != {"socket"}:
            logger.warning("Routing selector returned an invalid object; using default socket")
            return self.config.default_socket
        socket = parsed.get("socket")
        configured = {configured_socket for configured_socket, _ in self.config.upstream}
        if not isinstance(socket, str) or socket not in configured:
            logger.warning(f"Routing selector returned unknown socket {socket!r}; using default socket")
            return self.config.default_socket
        return socket

    @staticmethod
    def _completion_body(
        *, request_body: dict[str, Any], messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        excluded = {"messages", "tools", "routing", "model"}
        body = {key: deepcopy(value) for key, value in request_body.items() if key not in excluded}
        body["messages"] = deepcopy(messages)
        body["stream"] = True
        if tools:
            body["tools"] = deepcopy(tools)
        return body

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


Orchestrator = RoutingOrchestrator


__all__ = [
    "OrchestrationError",
    "Orchestrator",
    "PlanValidationError",
    "PlannedTask",
    "Planner",
    "RouterClient",
    "RouterConfig",
    "RoutingOrchestrator",
    "UpstreamResult",
    "parse_plan",
]
