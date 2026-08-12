"""Socket-aware OpenAI Chat Completions client used by the routing module."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any

import aiohttp
import anyio
from loguru import logger

from psi_agent._router_status import router_status_from_event
from psi_agent._sockets import resolve_connector_and_endpoint
from psi_agent._trace import TRACE_ID_HEADER, normalize_trace_id
from psi_agent.protocol import (
    FINISH_REASON_ERROR,
    FINISH_REASON_TOOL_CALLS,
    SSE_DONE,
    is_auxiliary_finish,
    is_terminal_finish,
    parse_sse_data,
)

from .errors import RouterUpstreamError
from .models import BufferedCompletion, CompletionResult


class RouterHttpClient:
    """Perform buffered and streaming calls for any Router strategy."""

    async def complete(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> CompletionResult:
        """Accumulate one single-choice SSE completion."""

        result = await self.buffered_complete(socket=socket, body=body, **options)
        return result.completion

    async def buffered_complete(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> BufferedCompletion:
        """Accumulate a completion while retaining its validated SSE events."""

        request_timeout, trace_id = self._request_options(options)
        buffered_events: list[dict[str, Any]] = []
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        stream = self.stream(socket=socket, body=body, timeout=request_timeout, trace_id=trace_id)
        async with aclosing(stream) as events:
            async for event in events:
                buffered_events.append(event)
                choice = event["choices"][0]
                delta = choice.get("delta", {})
                part = delta.get("content")
                if isinstance(part, str):
                    content_parts.append(part)
                reasoning = delta.get("reasoning")
                if isinstance(reasoning, str):
                    reasoning_parts.append(reasoning)
                self._accumulate_tool_calls(tool_calls, delta.get("tool_calls"))
                current_finish = choice.get("finish_reason")
                # Compaction is an auxiliary signal sent after the model's
                # actual terminal frame. It must not replace stop/tool_calls.
                if is_auxiliary_finish(current_finish):
                    continue
                if isinstance(current_finish, str):
                    if current_finish == FINISH_REASON_ERROR:
                        detail = "".join(content_parts) or "unknown upstream error"
                        raise RouterUpstreamError(f"Upstream {socket!r} reported an error: {detail}")
                    finish_reason = current_finish

        if finish_reason is None:
            raise RouterUpstreamError(f"Upstream {socket!r} ended without a finish reason")
        ordered_calls = [tool_calls[index] for index in sorted(tool_calls)]
        self._validate_tool_calls(ordered_calls, finish_reason)
        return BufferedCompletion(
            events=tuple(buffered_events),
            completion=CompletionResult(
                content="".join(content_parts),
                reasoning="".join(reasoning_parts),
                tool_calls=ordered_calls,
                finish_reason=finish_reason,
            ),
        )

    async def stream(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> AsyncGenerator[dict[str, Any]]:
        """Yield validated single-choice SSE events from one upstream."""

        request_timeout, trace_id = self._request_options(options)
        connector, endpoint = resolve_connector_and_endpoint(socket)
        session = aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=request_timeout))
        response: aiohttp.ClientResponse | None = None
        saw_completion_finish = False
        try:
            headers = {TRACE_ID_HEADER: trace_id} if trace_id is not None else None
            response = await session.post(endpoint, json=body, headers=headers)
            response_trace_id = response.headers.get(TRACE_ID_HEADER)
            if trace_id is not None and response_trace_id is not None:
                try:
                    normalized_response_trace_id = normalize_trace_id(response_trace_id)
                except ValueError as error:
                    raise RouterUpstreamError("Upstream returned an invalid trace ID header") from error
                if normalized_response_trace_id != trace_id:
                    raise RouterUpstreamError("Upstream returned a mismatched trace ID header")
            logger.info(f"Router upstream response trace_id={trace_id or '-'} status={response.status}")
            if response.status != 200:
                error_text = await response.text()
                raise RouterUpstreamError(f"Upstream {socket!r} returned HTTP {response.status}: {error_text[:1000]}")

            data_lines: list[str] = []
            while True:
                raw_line = await response.content.readline()
                if not raw_line:
                    if data_lines:
                        payload = "\n".join(data_lines)
                        if payload != SSE_DONE:
                            event = self._decode_event(payload)
                            if event is not None:
                                finish = event["choices"][0].get("finish_reason")
                                saw_completion_finish = saw_completion_finish or self._is_completion_finish(finish)
                                yield event
                    break

                logger.debug(f"Router upstream SSE line trace_id={trace_id or '-'}: {raw_line[:1000]!r}")
                line = raw_line.decode(errors="replace").rstrip("\r\n")
                if line:
                    payload_part = parse_sse_data(line)
                    if payload_part is not None:
                        data_lines.append(payload_part)
                    continue
                if not data_lines:
                    continue
                payload = "\n".join(data_lines)
                data_lines.clear()
                if payload == SSE_DONE:
                    break
                event = self._decode_event(payload)
                if event is None:
                    continue
                finish = event["choices"][0].get("finish_reason")
                saw_completion_finish = saw_completion_finish or self._is_completion_finish(finish)
                yield event

            if not saw_completion_finish:
                raise RouterUpstreamError(f"Upstream {socket!r} ended without a completion finish reason")
        except RouterUpstreamError:
            raise
        except (aiohttp.ClientError, TimeoutError) as error:
            raise RouterUpstreamError(f"Upstream {socket!r} request failed: {error}") from error
        finally:
            if response is not None:
                response.close()
            with anyio.CancelScope(shield=True):
                await session.close()

    @staticmethod
    def _decode_event(payload: str) -> dict[str, Any] | None:
        try:
            raw_event = json.loads(payload)
        except json.JSONDecodeError as error:
            raise RouterUpstreamError(f"Upstream returned malformed SSE JSON: {error.msg}") from error
        if not isinstance(raw_event, dict):
            raise RouterUpstreamError("Upstream SSE payload must be an object")
        choices = raw_event.get("choices")
        if not isinstance(choices, list):
            raise RouterUpstreamError("Upstream SSE choices must be a list")
        if not choices:
            return None
        if len(choices) != 1:
            raise RouterUpstreamError(f"Expected exactly one upstream choice, got {len(choices)}")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise RouterUpstreamError("Upstream choice must be an object")
        delta = choice.get("delta")
        if delta is None:
            choice["delta"] = {}
        elif not isinstance(delta, dict):
            raise RouterUpstreamError("Upstream choice delta must be an object")
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise RouterUpstreamError("Upstream finish reason must be a string or null")
        try:
            router_status_from_event(raw_event)
        except ValueError as error:
            raise RouterUpstreamError(f"Upstream returned invalid router_status: {error}") from error
        return raw_event

    @staticmethod
    def _is_completion_finish(value: object) -> bool:
        return isinstance(value, str) and is_terminal_finish(value)

    @staticmethod
    def _accumulate_tool_calls(accumulated: dict[int, dict[str, Any]], raw_calls: object) -> None:
        if raw_calls is None:
            return
        if not isinstance(raw_calls, list):
            raise RouterUpstreamError("Upstream tool_calls must be a list")
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                raise RouterUpstreamError("Upstream tool call must be an object")
            index = raw_call.get("index")
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                raise RouterUpstreamError("Upstream tool call has an invalid index")
            call = accumulated.setdefault(index, {"function": {"arguments": ""}})
            for key in ("id", "type"):
                value = raw_call.get(key)
                if value is not None:
                    if not isinstance(value, str):
                        raise RouterUpstreamError(f"Upstream tool call {key} must be a string")
                    call[key] = value
            function = raw_call.get("function")
            if function is None:
                continue
            if not isinstance(function, dict):
                raise RouterUpstreamError("Upstream tool call function must be an object")
            stored_function = call["function"]
            name = function.get("name")
            if name is not None:
                if not isinstance(name, str):
                    raise RouterUpstreamError("Upstream tool function name must be a string")
                stored_function["name"] = name
            arguments = function.get("arguments")
            if arguments is not None:
                if not isinstance(arguments, str):
                    raise RouterUpstreamError("Upstream tool function arguments must be a string")
                stored_function["arguments"] += arguments

    @staticmethod
    def _validate_tool_calls(tool_calls: list[dict[str, Any]], finish_reason: str) -> None:
        if finish_reason == FINISH_REASON_TOOL_CALLS and not tool_calls:
            raise RouterUpstreamError("Upstream finished with tool_calls but supplied none")
        for call in tool_calls:
            function = call.get("function")
            if (
                not isinstance(call.get("id"), str)
                or call.get("type") != "function"
                or not isinstance(function, dict)
                or not isinstance(function.get("name"), str)
                or not isinstance(function.get("arguments"), str)
            ):
                raise RouterUpstreamError("Upstream returned an incomplete tool call")

    @staticmethod
    def _request_options(options: dict[str, Any]) -> tuple[float | None, str | None]:
        unsupported = set(options) - {"timeout", "trace_id"}
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise TypeError(f"Unexpected RouterHttpClient option(s): {names}")
        value = options.get("timeout")
        if value is not None and (not isinstance(value, int | float) or isinstance(value, bool)):
            raise TypeError("timeout must be a number or None")
        raw_trace_id = options.get("trace_id")
        if raw_trace_id is None:
            return value, None
        try:
            trace_id = normalize_trace_id(raw_trace_id)
        except ValueError as error:
            raise TypeError(str(error)) from error
        return value, trace_id


__all__ = ["RouterHttpClient"]
