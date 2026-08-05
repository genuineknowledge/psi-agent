"""Parallel broadcast collection followed by dedicated synthesis."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing
from copy import deepcopy
from typing import Any, Protocol, cast

import anyio
from loguru import logger

from ..errors import RouterUpstreamError
from ..models import CompletionResult, RouterTarget
from ..privacy import redact_private_sockets
from ..request import copy_public_request_body, copy_target_request_body
from .errors import AggregationError
from .models import AggregationConfig, AggregationFeedback
from .prompts import build_aggregation_messages


class _AggregationClient(Protocol):
    async def complete(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> CompletionResult: ...

    def stream(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> AsyncGenerator[dict[str, Any]]: ...


class AggregationStrategy:
    """Broadcast to every target and stream one dedicated Aggregator response."""

    def __init__(self, *, config: AggregationConfig, client: _AggregationClient) -> None:
        self.config = config
        self.client = client

    async def stream(self, *, body: dict[str, Any]) -> AsyncGenerator[dict[str, Any]]:
        """Collect ordered branch evidence, then yield validated synthesis events."""

        slots: list[AggregationFeedback | None] = [None] * len(self.config.targets)
        private_sockets = (
            self.config.aggregator_socket,
            *(item.socket for item in self.config.targets),
        )

        async def collect(index: int, target: RouterTarget) -> None:
            try:
                result = await self.client.complete(
                    socket=target.socket,
                    body=copy_target_request_body(body=body, target=target),
                    timeout=self.config.target_timeout,
                )
                if not result.content.strip() and not result.tool_calls:
                    raise RouterUpstreamError("upstream returned no usable content or tool calls")
                slots[index] = AggregationFeedback(
                    candidate_id=target.candidate_id,
                    description=target.description,
                    status="success",
                    finish_reason=result.finish_reason,
                    content=result.content,
                    tool_calls=tuple(deepcopy(result.tool_calls)),
                )
            except Exception as error:
                slots[index] = AggregationFeedback(
                    candidate_id=target.candidate_id,
                    description=target.description,
                    status="error",
                    error_type=type(error).__name__,
                    error=redact_private_sockets(text=str(error), sockets=private_sockets),
                )

        async with anyio.create_task_group() as task_group:
            for index, target in enumerate(self.config.targets):
                task_group.start_soon(collect, index, target)

        if any(item is None for item in slots):
            raise AggregationError("Aggregation feedback collection ended unexpectedly")
        feedback = cast(list[AggregationFeedback], slots)
        for item in feedback:
            logger.info(
                f"Aggregation candidate status: candidate_id={item.candidate_id!r}, "
                f"description={item.description!r}, status={item.status!r}"
            )
        if not any(item.status == "success" for item in feedback):
            raise AggregationError("All aggregation upstreams failed")

        aggregator_body = copy_public_request_body(body=body)
        aggregator_body["messages"] = build_aggregation_messages(
            original_messages=cast(list[dict[str, Any]], body["messages"]),
            feedback=feedback,
            max_context_chars=self.config.max_context_chars,
        )
        aggregator_stream = self.client.stream(
            socket=self.config.aggregator_socket,
            body=aggregator_body,
            timeout=self.config.aggregator_timeout,
        )
        saw_usable = False
        finish_reason: str | None = None
        try:
            async with aclosing(aggregator_stream) as events:
                async for event in events:
                    choice = event["choices"][0]
                    current_finish = choice.get("finish_reason")
                    if current_finish == "error":
                        raise AggregationError("Aggregator reported an error")
                    delta = choice.get("delta", {})
                    content = delta.get("content") if isinstance(delta, dict) else None
                    tool_calls = delta.get("tool_calls") if isinstance(delta, dict) else None
                    if (isinstance(content, str) and content) or (isinstance(tool_calls, list) and tool_calls):
                        saw_usable = True
                    if isinstance(current_finish, str) and current_finish != "compaction_needed":
                        finish_reason = current_finish
                    yield event
        except AggregationError:
            raise
        except Exception as error:
            raise AggregationError("Aggregator request failed") from error

        if not saw_usable:
            raise AggregationError("Aggregator returned no usable content or tool calls")
        if finish_reason is None:
            raise AggregationError("Aggregator ended without a completion finish reason")

    def discard(self, session_id: str) -> None:
        """Aggregation keeps no per-session state."""

        del session_id
        return None

    def clear(self) -> None:
        """Aggregation keeps no process state."""

        return None


__all__ = ["AggregationStrategy"]
