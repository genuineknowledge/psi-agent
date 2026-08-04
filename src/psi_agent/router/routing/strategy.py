"""Select one target, then stream the original request through that target."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any, Protocol

from loguru import logger

from ..errors import InvalidRouterRequestError
from ..request import copy_public_request_body
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
        self._sticky_targets: dict[str, SelectionResult] = {}

    async def stream(self, *, body: dict[str, Any]) -> AsyncGenerator[dict[str, Any]]:
        """Select one target and yield its validated SSE events."""

        messages = self._validate_request(body)
        routing = body.get("routing")
        if routing is not None and not isinstance(routing, dict):
            raise InvalidRouterRequestError("routing must be an object when present")
        raw_session_id = routing.get("session_id") if isinstance(routing, dict) else None
        if raw_session_id is not None and (not isinstance(raw_session_id, str) or not raw_session_id.strip()):
            raise InvalidRouterRequestError("routing.session_id must be a non-empty string")
        session_id = raw_session_id.strip() if isinstance(raw_session_id, str) else None
        is_tool_iteration = bool(messages) and messages[-1].get("role") == "tool"

        selection = self._sticky_targets.get(session_id) if session_id and is_tool_iteration else None
        if selection is None:
            if session_id and not is_tool_iteration:
                self.discard(session_id)
            selection = await self.selector.select(request_body=body)
            if session_id:
                self._sticky_targets[session_id] = selection
        else:
            logger.info(
                f"Reusing sticky routing candidate {selection.candidate_id!r} "
                f"for tool iteration in session {session_id!r}"
            )

        target_body = copy_public_request_body(body=body)
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
        finally:
            if session_id and (not completed or finish_reason != "tool_calls"):
                self.discard(session_id)

    def discard(self, session_id: str) -> None:
        """Forget the sticky target for one Session."""

        normalized = session_id.strip()
        if normalized:
            self._sticky_targets.pop(normalized, None)

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
