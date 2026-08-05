"""Select one target, then stream the original request through that target."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any, Protocol

import anyio
from loguru import logger

from ..errors import InvalidRouterRequestError, RouterUpstreamError
from ..models import RoutingScopeKey
from ..privacy import redact_private_sockets
from ..request import copy_target_request_body, routing_scope_from_body
from .errors import RouteSelectionError
from .models import RoutingConfig, SelectionResult


class _Selector(Protocol):
    async def select(self, *, request_body: dict[str, Any]) -> SelectionResult: ...


class _StreamingClient(Protocol):
    def stream(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> AsyncGenerator[dict[str, Any]]: ...


class RoutingStrategy:
    """Route requests with a target sticky only across one tool-call run."""

    def __init__(self, *, config: RoutingConfig, selector: _Selector, client: _StreamingClient) -> None:
        self.config = config
        self.selector = selector
        self.client = client
        self._sticky_targets: dict[RoutingScopeKey, SelectionResult] = {}

    async def stream(self, *, body: dict[str, Any]) -> AsyncGenerator[dict[str, Any]]:
        """Select one target and yield its validated SSE events."""

        messages = self._validate_request(body)
        scope = routing_scope_from_body(body=body)
        scope_session_id = scope[0] if scope is not None else None
        is_tool_iteration = bool(messages) and messages[-1].get("role") == "tool"
        private_sockets = (self.config.selector_socket, *(target.socket for target in self.config.targets))
        cancelled_error = anyio.get_cancelled_exc_class()

        selection = self._sticky_targets.get(scope) if scope is not None and is_tool_iteration else None
        if selection is None:
            if scope is not None and not is_tool_iteration:
                self._sticky_targets.pop(scope, None)
            try:
                selection = await self.selector.select(request_body=body)
            except cancelled_error:
                raise
            except Exception as error:
                summary = redact_private_sockets(text=str(error), sockets=private_sockets)
                raise RouteSelectionError(f"Selector request failed: {summary}") from error
            if scope is not None:
                self._sticky_targets[scope] = selection
        else:
            logger.info(
                f"Reusing sticky routing candidate {selection.candidate_id!r} "
                f"for tool iteration in session {scope_session_id!r}"
            )

        target_body = copy_target_request_body(body=body, target=selection.target)
        logger.info(f"Routing request to candidate {selection.candidate_id!r}")
        target_stream = self.client.stream(
            socket=selection.target.socket,
            body=target_body,
            timeout=self.config.target_timeout,
        )
        completed = False
        finish_reason: str | None = None
        try:
            async with aclosing(target_stream) as events:
                async for event in events:
                    current_finish = event["choices"][0].get("finish_reason")
                    if isinstance(current_finish, str) and current_finish != "compaction_needed":
                        finish_reason = current_finish
                    yield event
                completed = True
        except cancelled_error:
            raise
        except Exception as error:
            summary = redact_private_sockets(text=str(error), sockets=private_sockets)
            raise RouterUpstreamError(f"Routing candidate {selection.candidate_id!r} failed: {summary}") from error
        finally:
            if scope is not None and (not completed or finish_reason != "tool_calls"):
                self._sticky_targets.pop(scope, None)

    def discard(self, session_id: str) -> None:
        """Forget the sticky target for one Session."""

        normalized = session_id.strip()
        if normalized:
            stale_scopes = [scope for scope in self._sticky_targets if scope[0] == normalized]
            for scope in stale_scopes:
                self._sticky_targets.pop(scope, None)

    def clear(self) -> None:
        """Forget every sticky target, normally during Router shutdown."""

        self._sticky_targets.clear()

    @staticmethod
    def _validate_request(body: dict[str, Any]) -> list[dict[str, Any]]:
        messages = body.get("messages")
        if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
            raise InvalidRouterRequestError("messages must be a list of objects")
        tools = body.get("tools", [])
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise InvalidRouterRequestError("tools must be a list of objects")
        stream = body.get("stream", True)
        if stream is not True:
            raise InvalidRouterRequestError("routing service requires stream=true")
        return messages


__all__ = ["RoutingStrategy"]
