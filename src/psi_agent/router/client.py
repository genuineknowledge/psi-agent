"""Socket-aware OpenAI Chat Completions SSE client for Router upstreams."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import anyio
from loguru import logger

from psi_agent._sockets import resolve_connector_and_endpoint


class RouterUpstreamError(Exception):
    """An upstream response cannot be safely used by the Router."""


@dataclass
class UpstreamResult:
    """The accumulated result from one single-choice upstream completion."""

    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""


class RouterClient:
    """Perform one buffered completion or proxy a raw upstream SSE stream."""

    async def complete(self, *, socket: str, body: dict[str, Any], **options: Any) -> UpstreamResult:
        """Return an accumulated, validated result from a single-choice SSE response."""

        timeout = self._timeout_from_options(options)
        connector, endpoint = resolve_connector_and_endpoint(socket)
        session = aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=timeout))
        response: aiohttp.ClientResponse | None = None
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        try:
            response = await session.post(endpoint, json=self._sanitize_body(body))
            logger.info(f"Router upstream response status: {response.status}")
            if response.status != 200:
                error_text = await response.text()
                raise RouterUpstreamError(f"Upstream {socket!r} returned HTTP {response.status}: {error_text[:1000]}")

            async for raw_line in response.content:
                logger.debug(f"Router upstream SSE chunk: {raw_line[:1000]!r}")
                line = raw_line.decode(errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].lstrip()
                if payload == "[DONE]":
                    break
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning(f"Skipping malformed router SSE JSON: {payload[:1000]!r}")
                    continue
                if not isinstance(data, dict):
                    logger.warning(f"Skipping non-object router SSE payload: {type(data).__name__}")
                    continue
                choices = data.get("choices", [])
                if not isinstance(choices, list):
                    logger.warning(f"Skipping router SSE with non-list choices: {type(choices).__name__}")
                    continue
                if not choices:
                    continue
                if len(choices) != 1:
                    raise RouterUpstreamError(f"Expected exactly 1 choice, got {len(choices)}")
                choice = choices[0]
                if not isinstance(choice, dict):
                    logger.warning(f"Skipping non-object router choice: {type(choice).__name__}")
                    continue
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    delta = {}
                part = delta.get("content")
                if isinstance(part, str):
                    content_parts.append(part)
                reasoning = delta.get("reasoning")
                if isinstance(reasoning, str):
                    reasoning_parts.append(reasoning)
                self._accumulate_tool_calls(tool_calls, delta.get("tool_calls"))
                current_finish_reason = choice.get("finish_reason")
                if current_finish_reason is not None:
                    if not isinstance(current_finish_reason, str):
                        raise RouterUpstreamError("Upstream finish reason must be a string")
                    if current_finish_reason == "error":
                        detail = "".join(content_parts) or "unknown error"
                        raise RouterUpstreamError(f"Upstream reported an error: {detail}")
                    finish_reason = current_finish_reason

            if finish_reason is None:
                raise RouterUpstreamError("Upstream stream ended without a finish reason")
            ordered_calls = [tool_calls[index] for index in sorted(tool_calls)]
            self._validate_tool_calls(ordered_calls, finish_reason)
            return UpstreamResult(
                content="".join(content_parts),
                reasoning="".join(reasoning_parts),
                tool_calls=ordered_calls,
                finish_reason=finish_reason,
            )
        finally:
            if response is not None:
                response.close()
            with anyio.CancelScope(shield=True):
                await session.close()

    async def stream_raw(self, *, socket: str, body: dict[str, Any], **options: Any) -> AsyncGenerator[bytes]:
        """Yield upstream bytes unchanged, validating its HTTP response first."""

        timeout = self._timeout_from_options(options)
        connector, endpoint = resolve_connector_and_endpoint(socket)
        session = aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=timeout))
        response: aiohttp.ClientResponse | None = None
        try:
            response = await session.post(endpoint, json=self._sanitize_body(body))
            logger.info(f"Router raw upstream response status: {response.status}")
            if response.status != 200:
                error_text = await response.text()
                raise RouterUpstreamError(f"Upstream {socket!r} returned HTTP {response.status}: {error_text[:1000]}")
            async for chunk in response.content.iter_any():
                logger.debug(f"Router raw upstream SSE chunk: {chunk[:1000]!r}")
                yield chunk
        finally:
            if response is not None:
                response.close()
            with anyio.CancelScope(shield=True):
                await session.close()

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
        if finish_reason != "tool_calls":
            return
        if not tool_calls:
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
    def _timeout_from_options(options: dict[str, Any]) -> float | None:
        """Read the public ``timeout`` keyword without exposing a lint-forbidden name."""

        unsupported = set(options) - {"timeout"}
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise TypeError(f"Unexpected RouterClient option(s): {names}")
        timeout = options.get("timeout")
        if timeout is not None and (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)):
            raise TypeError("timeout must be a number or None")
        return timeout

    @staticmethod
    def _sanitize_body(body: dict[str, Any]) -> dict[str, Any]:
        """Copy a request body while withholding Router-internal selection fields."""

        return {key: value for key, value in body.items() if key not in {"routing", "model"}}
