"""Strictly serial fallback with commit-after-validation replay."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Protocol

import anyio
from loguru import logger

from ..errors import InvalidRouterRequestError, RouterUpstreamError
from ..models import BufferedCompletion, CompletionResult, RoutingScopeKey
from ..privacy import redact_private_sockets
from ..request import copy_target_request_body, routing_scope_from_body
from .errors import FallbackError
from .models import FallbackConfig


class _BufferedClient(Protocol):
    async def buffered_complete(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> BufferedCompletion: ...


class FallbackStrategy:
    """Try candidates in order and replay only the first complete success."""

    def __init__(self, *, config: FallbackConfig, client: _BufferedClient) -> None:
        self.config = config
        self.client = client
        self._sticky_targets: dict[RoutingScopeKey, int] = {}

    async def stream(self, *, body: dict[str, Any]) -> AsyncGenerator[dict[str, Any]]:
        """Buffer serial attempts and yield only one validated candidate stream."""

        messages = self._validate_request(body)
        scope = routing_scope_from_body(body=body)
        is_tool_iteration = bool(messages) and messages[-1].get("role") == "tool"
        start_index = 0
        if scope is not None:
            if is_tool_iteration:
                start_index = self._sticky_targets.get(scope, 0)
            else:
                self._sticky_targets.pop(scope, None)

        failures: list[str] = []
        private_sockets = tuple(target.socket for target in self.config.targets)
        selected: tuple[int, BufferedCompletion] | None = None
        cancelled_error = anyio.get_cancelled_exc_class()
        for index in range(start_index, len(self.config.targets)):
            target = self.config.targets[index]
            logger.info(
                f"Fallback candidate attempt: candidate_id={target.candidate_id!r}, "
                f"description={target.description!r}, status='started'"
            )
            try:
                result = await self.client.buffered_complete(
                    socket=target.socket,
                    body=copy_target_request_body(body=body, target=target),
                    timeout=target.timeout if target.timeout is not None else self.config.target_timeout,
                )
                if not self._is_usable(result.completion):
                    raise RouterUpstreamError("upstream returned no usable content or tool calls")
            except cancelled_error:
                if scope is not None:
                    self._sticky_targets.pop(scope, None)
                logger.info(
                    f"Fallback candidate attempt: candidate_id={target.candidate_id!r}, "
                    f"description={target.description!r}, status='cancelled'"
                )
                raise
            except Exception as error:
                summary = redact_private_sockets(text=str(error), sockets=private_sockets)
                failures.append(f"{target.candidate_id} ({type(error).__name__}): {summary}")
                logger.info(
                    f"Fallback candidate attempt: candidate_id={target.candidate_id!r}, "
                    f"description={target.description!r}, status='failed'"
                )
                continue

            selected = index, result
            logger.info(
                f"Fallback candidate attempt: candidate_id={target.candidate_id!r}, "
                f"description={target.description!r}, status='success'"
            )
            break

        if selected is None:
            if scope is not None:
                self._sticky_targets.pop(scope, None)
            detail = "; ".join(failures) or "no eligible candidates remained"
            raise FallbackError(f"All fallback upstreams failed: {detail}")

        selected_index, result = selected
        keeps_sticky = result.completion.finish_reason == "tool_calls"
        if scope is not None:
            if keeps_sticky:
                self._sticky_targets[scope] = selected_index
            else:
                self._sticky_targets.pop(scope, None)

        replayed_completely = False
        try:
            for event in result.events:
                yield event
            replayed_completely = True
        finally:
            if scope is not None and (not replayed_completely or not keeps_sticky):
                self._sticky_targets.pop(scope, None)

    def discard(self, session_id: str) -> None:
        """Forget every path-scoped sticky candidate for one Session."""

        normalized = session_id.strip()
        if normalized:
            stale_scopes = [scope for scope in self._sticky_targets if scope[0] == normalized]
            for scope in stale_scopes:
                self._sticky_targets.pop(scope, None)

    def clear(self) -> None:
        """Forget every sticky candidate, normally during Router shutdown."""

        self._sticky_targets.clear()

    @staticmethod
    def _validate_request(body: dict[str, Any]) -> list[dict[str, Any]]:
        messages = body.get("messages")
        if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
            raise InvalidRouterRequestError("messages must be a list of objects")
        tools = body.get("tools", [])
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise InvalidRouterRequestError("tools must be a list of objects")
        if body.get("stream", True) is not True:
            raise InvalidRouterRequestError("fallback service requires stream=true")
        return messages

    @staticmethod
    def _is_usable(completion: CompletionResult) -> bool:
        finish_reason = completion.finish_reason
        if not finish_reason or finish_reason in {"error", "compaction_needed"}:
            return False
        for call in completion.tool_calls:
            function = call.get("function")
            if (
                not isinstance(call.get("id"), str)
                or call.get("type") != "function"
                or not isinstance(function, dict)
                or not isinstance(function.get("name"), str)
                or not isinstance(function.get("arguments"), str)
            ):
                return False
        return bool(completion.content.strip() or completion.tool_calls)


__all__ = ["FallbackStrategy"]
