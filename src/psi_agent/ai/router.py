from __future__ import annotations

import json
import math
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import anyio
from aiohttp import ClientTimeout, web
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent._sockets import create_site, resolve_connector_and_endpoint

from .llmrouter_adapter import (
    LLMRouterAdapter,
    RouteDecision,
    RouteTarget,
    parse_upstreams,
    serialize_context,
)

_ROUTE_TARGETS_KEY = web.AppKey("route_targets", list[RouteTarget])
_LLMROUTER_KEY = web.AppKey("llmrouter", Any)
_FALLBACK_KEY = web.AppKey("fallback", RouteDecision)
_ROUTER_TIMEOUT_KEY = web.AppKey("router_timeout", object)
_CONTEXT_CHARS_KEY = web.AppKey("context_chars", int)
_LOG_DETAILS_KEY = web.AppKey("log_details", bool)


def _error_payload(message: str, code: int) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": "router_error",
            "param": None,
            "code": code,
        }
    }


def _error_chunk_bytes(message: str) -> bytes:
    payload = {
        "id": "error",
        "choices": [
            {
                "index": 0,
                "delta": {"content": message},
                "finish_reason": "error",
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _is_done_line(raw: bytes) -> bool:
    return raw.decode().strip() == "data: [DONE]"


def _fallback_decision(default_target: RouteTarget, *, explicit: bool) -> RouteDecision:
    return RouteDecision(
        target=default_target,
        routes=(),
        votes={},
        source="fallback_default" if explicit else "fallback_first",
    )


async def _select_destination(app: web.Application, body: dict[str, Any]) -> RouteDecision:
    requested = body.get("model")
    if isinstance(requested, str):
        for target in app[_ROUTE_TARGETS_KEY]:
            if target.model == requested:
                return RouteDecision(target=target, routes=(), votes={}, source="request_model")

    context = serialize_context(body.get("messages"), max_chars=app[_CONTEXT_CHARS_KEY])
    if not context:
        return app[_FALLBACK_KEY]

    try:
        timeout_value = app[_ROUTER_TIMEOUT_KEY]
        if timeout_value is None:
            return await app[_LLMROUTER_KEY].route(context)
        if not isinstance(timeout_value, (int, float)):
            raise TypeError("router timeout application state must be numeric or None")
        timeout = float(timeout_value)
        with anyio.move_on_after(timeout) as scope:
            decision = await app[_LLMROUTER_KEY].route(context)
        if scope.cancel_called:
            logger.warning(f"LLMRouter timed out after {timeout}s; using fallback")
            return app[_FALLBACK_KEY]
        return decision
    except Exception as exc:
        logger.warning(f"LLMRouter failed; using fallback: {exc!r}")
        return app[_FALLBACK_KEY]


async def handle_router_chat_completions(request: web.Request) -> web.StreamResponse:
    logger.info("Received router chat completion request")
    try:
        payload = await request.json()
    except Exception as exc:
        return web.json_response(_error_payload(str(exc), 400), status=400)
    if not isinstance(payload, dict):
        return web.json_response(_error_payload("Request body must be a JSON object", 400), status=400)

    body: dict[str, Any] = dict(payload)
    decision = await _select_destination(request.app, body)
    body.pop("routing", None)
    body["model"] = decision.target.model
    if request.app[_LOG_DETAILS_KEY]:
        logger.debug(f"LLMRouter routes={decision.routes!r}, votes={decision.votes!r}")
    else:
        logger.debug(f"LLMRouter votes={decision.votes!r}")
    logger.info(
        f"Router selected model={decision.target.model!r}, addr={decision.target.addr!r}, source={decision.source!r}"
    )

    connector, endpoint = resolve_connector_and_endpoint(decision.target.addr)
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    try:
        async with (
            aiohttp.ClientSession(connector=connector, timeout=ClientTimeout(total=None)) as session,
            session.post(endpoint, json=body) as upstream_response,
        ):
            if upstream_response.status != 200:
                error_text = await upstream_response.text()
                logger.warning(
                    f"Router upstream {decision.target.addr!r} returned HTTP "
                    f"{upstream_response.status}: {error_text[:1000]!r}"
                )
                return web.json_response(
                    _error_payload(
                        error_text or f"Upstream returned HTTP {upstream_response.status}",
                        502,
                    ),
                    status=502,
                )
            await response.prepare(request)
            async for raw in upstream_response.content:
                await response.write(raw)
                logger.debug(f"Router proxied SSE chunk: {raw!r}")
                if _is_done_line(raw):
                    logger.debug(f"Router received [DONE] from {decision.target.addr!r}")
                    break
            await response.write_eof()
    except Exception as exc:
        logger.error(f"Router proxy error for upstream {decision.target.addr!r}: {exc!r}")
        if response.prepared:
            with suppress(Exception):
                await response.write(_error_chunk_bytes(f"[Router Error]: {exc}"))
            with suppress(Exception):
                await response.write_eof()
            return response
        return web.json_response(_error_payload(str(exc), 500), status=500)
    return response


async def serve_router(
    *,
    socket_path: str,
    targets: list[RouteTarget],
    adapter: LLMRouterAdapter,
    fallback: RouteDecision,
    router_timeout: float | None,
    context_chars: int,
    log_details: bool,
) -> None:
    logger.info(f"Starting AI router on {socket_path} with {len(targets)} upstreams")
    app = web.Application()
    app[_ROUTE_TARGETS_KEY] = targets
    app[_LLMROUTER_KEY] = adapter
    app[_FALLBACK_KEY] = fallback
    app[_ROUTER_TIMEOUT_KEY] = router_timeout
    app[_CONTEXT_CHARS_KEY] = context_chars
    app[_LOG_DETAILS_KEY] = log_details
    app.router.add_post("/chat/completions", handle_router_chat_completions)
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        await create_site(runner, socket_path).start()
    except Exception as exc:
        logger.error(f"Failed to start AI router on {socket_path}: {exc}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        raise
    logger.info(f"AI router listening on {socket_path}")
    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down AI router on {socket_path}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        logger.info(f"AI router shutdown complete on {socket_path}")


@dataclass
class AiRouter:
    """Route requests to JSON-described upstreams with a remote LLMRouter model."""

    session_socket: str
    """Path/URL of the router socket to listen on."""

    router_model: str = ""
    """Remote model used only to make routing decisions."""

    router_base_url: str = ""
    """OpenAI-compatible base URL for the routing model."""

    router_api_key: str = ""
    """API key for the routing model; an empty key is allowed."""

    upstream: list[str] = field(default_factory=list)
    """Candidate JSON objects supplied as one or more values after --upstream."""

    default_model: str = ""
    """Fallback candidate model; defaults to the first upstream."""

    router_timeout: float | None = None
    """Routing timeout in seconds; omit the value to wait indefinitely."""

    router_context_chars: int = 12_000
    """Maximum serialized conversation characters sent to the router model."""

    log_router_details: bool = False
    """Log raw subqueries and routes at DEBUG level."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        router_model = self.router_model or os.environ.get("PSI_ROUTER_MODEL", "")
        router_base_url = self.router_base_url or os.environ.get("PSI_ROUTER_BASE_URL", "")
        router_api_key = self.router_api_key or os.environ.get("PSI_ROUTER_API_KEY", "")
        if not router_model.strip():
            raise ValueError("--router-model or PSI_ROUTER_MODEL must be provided")
        if not router_base_url.strip():
            raise ValueError("--router-base-url or PSI_ROUTER_BASE_URL must be provided")
        if self.router_timeout is not None and (not math.isfinite(self.router_timeout) or self.router_timeout <= 0):
            raise ValueError("--router-timeout must be a finite positive number or empty")
        if self.router_context_chars <= 0:
            raise ValueError("--router-context-chars must be positive")
        targets = parse_upstreams(self.upstream)
        target_by_model = {target.model: target for target in targets}
        if self.default_model and self.default_model not in target_by_model:
            raise ValueError(f"--default-model {self.default_model!r} is not present in --upstream")
        explicit_default = bool(self.default_model)
        default_target = target_by_model.get(self.default_model, targets[0])
        fallback = _fallback_decision(default_target, explicit=explicit_default)

        adapter = LLMRouterAdapter(
            router_model=router_model.strip(),
            router_base_url=router_base_url.strip(),
            router_api_key=router_api_key,
            targets=targets,
            runtime_root=tempfile.gettempdir(),
        )
        try:
            await adapter.start()
            await serve_router(
                socket_path=self.session_socket,
                targets=targets,
                adapter=adapter,
                fallback=fallback,
                router_timeout=self.router_timeout,
                context_chars=self.router_context_chars,
                log_details=self.log_router_details,
            )
        finally:
            with anyio.CancelScope(shield=True):
                await adapter.close()
