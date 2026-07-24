"""OpenAI-compatible HTTP/SSE boundary for concurrent Router fan-out."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from copy import deepcopy
from typing import Any, Protocol, cast

import aiohttp
import anyio
from aiohttp import web
from loguru import logger

from psi_agent._sockets import create_site
from psi_agent.router.client import RouterClient, RouterUpstreamError, UpstreamResult
from psi_agent.router.orchestrator import OrchestrationError, Orchestrator
from psi_agent.router.planner import PlanValidationError
from psi_agent.router.protocol import RouterConfig


class _RawStreamingClient(Protocol):
    def stream_raw(self, *, socket: str, body: dict[str, Any], **options: Any) -> AsyncGenerator[bytes]: ...


_SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
_ROUTER_CONFIG_KEY: web.AppKey[RouterConfig] = web.AppKey("router_config", RouterConfig)
_ROUTER_ORCHESTRATOR_KEY: web.AppKey[object] = web.AppKey("router_orchestrator", object)
_ROUTER_CLIENT_KEY: web.AppKey[object] = web.AppKey("router_client", object)
_ORCHESTRATION_FAILURES = (
    OrchestrationError,
    PlanValidationError,
    RouterUpstreamError,
    aiohttp.ClientError,
    TimeoutError,
)


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    """Route one completion, falling back once without exposing private state."""

    logger.info("Received Router chat completion request")
    try:
        raw_body = await request.json()
    except Exception as error:
        logger.warning(f"Router request JSON parsing failed: {error!r}")
        return _http_error(status=400, message=str(error), error_type="invalid_request_error")
    if not isinstance(raw_body, dict):
        logger.warning(f"Router request body must be an object, got {type(raw_body).__name__}")
        return _http_error(status=400, message="Request body must be a JSON object", error_type="invalid_request_error")
    body: dict[str, Any] = raw_body
    logger.debug(f"Router request body: {json.dumps(body, ensure_ascii=False)[:1000]}")

    config = request.app[_ROUTER_CONFIG_KEY]
    orchestrator = cast(Orchestrator, request.app[_ROUTER_ORCHESTRATOR_KEY])
    client = cast(_RawStreamingClient, request.app[_ROUTER_CLIENT_KEY])
    try:
        result = await orchestrator.process(body=body)
    except _ORCHESTRATION_FAILURES as error:
        logger.warning(f"Router orchestration failed; using default socket once: {error!r}")
        _discard_session_run(orchestrator=orchestrator, body=body)
        return await _stream_fallback(request=request, client=client, config=config, body=body)

    try:
        chunk = _result_chunk(result)
    except OrchestrationError as error:
        logger.warning(f"Router produced an invalid orchestration result; using default socket once: {error!r}")
        _discard_session_run(orchestrator=orchestrator, body=body)
        return await _stream_fallback(request=request, client=client, config=config, body=body)

    logger.info(
        f"Router result ready: finish_reason={result.finish_reason}, "
        f"content={result.content!r}, tool_calls={len(result.tool_calls)}"
    )

    response = web.StreamResponse(status=200, reason="OK", headers=_SSE_HEADERS)
    try:
        await response.prepare(request)
        await _write_sse(response=response, chunk=chunk)
    except ConnectionResetError:
        logger.info("Router client disconnected while receiving orchestration result")
    except Exception as error:
        logger.error(f"Router failed after preparing orchestration response: {error!r}")
        await _write_sse_error(response=response, error=error)
    return response


async def serve_router(
    *,
    config: RouterConfig,
    orchestrator: Orchestrator | None = None,
    client: RouterClient | None = None,
) -> None:
    """Serve Router requests until cancelled, with shielded startup and shutdown cleanup."""

    actual_client = client if client is not None else RouterClient()
    actual_orchestrator = (
        orchestrator if orchestrator is not None else Orchestrator(config=config, client=actual_client)
    )
    logger.info(f"Starting Router server on {config.session_socket}")
    app = web.Application(client_max_size=100 * 1024 * 1024)
    app[_ROUTER_CONFIG_KEY] = config
    app[_ROUTER_ORCHESTRATOR_KEY] = actual_orchestrator
    app[_ROUTER_CLIENT_KEY] = actual_client
    app.router.add_post("/chat/completions", handle_chat_completions)
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = create_site(runner, config.session_socket)
        await site.start()
    except Exception as error:
        logger.error(f"Failed to start Router server on {config.session_socket}: {error}")
        actual_orchestrator.clear()
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        raise

    logger.info(f"Router server listening on {config.session_socket}")
    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down Router server on {config.session_socket}")
        actual_orchestrator.clear()
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        logger.info(f"Router server shutdown complete on {config.session_socket}")


async def _stream_fallback(
    *,
    request: web.Request,
    client: _RawStreamingClient,
    config: RouterConfig,
    body: dict[str, Any],
) -> web.StreamResponse:
    """Begin a single raw default-backend stream only after it has accepted the request."""

    sanitized_body = {key: deepcopy(value) for key, value in body.items() if key not in {"routing", "model"}}
    stream = client.stream_raw(socket=config.default_socket, body=sanitized_body, timeout=config.router_timeout)
    try:
        async with aclosing(stream):
            try:
                first_chunk = await anext(stream)
            except StopAsyncIteration:
                first_chunk = None
            response = web.StreamResponse(status=200, reason="OK", headers=_SSE_HEADERS)
            try:
                await response.prepare(request)
                if first_chunk is not None:
                    await _write_raw(response=response, chunk=first_chunk)
                async for chunk in stream:
                    await _write_raw(response=response, chunk=chunk)
            except ConnectionResetError:
                logger.info("Router client disconnected while receiving default fallback stream")
            except Exception as error:
                logger.error(f"Router default fallback failed after response preparation: {error!r}")
                await _write_sse_error(response=response, error=error)
            return response
    except _ORCHESTRATION_FAILURES as error:
        logger.error(f"Router default fallback failed before response preparation: {error!r}")
        return _http_error(status=502, message=str(error), error_type="upstream_error")


def _result_chunk(result: UpstreamResult) -> dict[str, Any]:
    """Encode a buffered orchestration result as one OpenAI-compatible choice."""

    if result.finish_reason not in {"stop", "tool_calls"}:
        raise OrchestrationError(f"Router returned unsupported finish reason {result.finish_reason!r}")
    delta: dict[str, Any] = {}
    if result.content:
        delta["content"] = result.content
    if result.reasoning:
        delta["reasoning"] = result.reasoning
    if result.tool_calls:
        delta["tool_calls"] = result.tool_calls
    if result.finish_reason == "tool_calls" and not result.tool_calls:
        raise OrchestrationError("Router returned tool_calls without tool call data")
    return {"id": "router", "choices": [{"index": 0, "delta": delta, "finish_reason": result.finish_reason}]}


def _discard_session_run(*, orchestrator: Orchestrator, body: dict[str, Any]) -> None:
    """Drop a partial run when a routable Session identity is present."""

    routing = body.get("routing")
    session_id = routing.get("session_id") if isinstance(routing, dict) else None
    if isinstance(session_id, str) and session_id.strip():
        orchestrator.discard(session_id.strip())


async def _write_raw(*, response: web.StreamResponse, chunk: bytes) -> None:
    logger.debug(f"Router outgoing raw SSE chunk: {chunk[:1000]!r}")
    await response.write(chunk)


async def _write_sse(*, response: web.StreamResponse, chunk: dict[str, Any]) -> None:
    encoded = json.dumps(chunk, ensure_ascii=False)
    logger.debug(f"Router outgoing SSE chunk: {encoded[:1000]}")
    await response.write(f"data: {encoded}\n\n".encode())


async def _write_sse_error(*, response: web.StreamResponse, error: Exception) -> None:
    chunk = {
        "id": "error",
        "choices": [
            {
                "index": 0,
                "delta": {"content": f"[Router Error]: {error}"},
                "finish_reason": "error",
            }
        ],
    }
    try:
        await _write_sse(response=response, chunk=chunk)
    except Exception as write_error:
        logger.warning(f"Router failed to send SSE error chunk: {write_error!r}")


def _http_error(*, status: int, message: str, error_type: str) -> web.Response:
    return web.json_response(
        {"error": {"message": message, "type": error_type, "param": None, "code": status}}, status=status
    )


__all__ = ["handle_chat_completions", "serve_router"]
