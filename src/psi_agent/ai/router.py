from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import aiohttp
import anyio
from aiohttp import ClientTimeout, web
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent._sockets import create_site, resolve_connector_and_endpoint

_ROUTER_POLICIES = {"difficulty", "first", "last", "round_robin"}
_HARD_KEYWORDS = (
    "analy",
    "analysis",
    "bug",
    "code",
    "debug",
    "design",
    "prove",
    "reason",
    "traceback",
    "优化",
    "分析",
    "报错",
    "推理",
    "证明",
    "设计",
    "调试",
)


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


def _extract_latest_user_text(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    return ""


def _should_use_first_upstream_for_demo(text: str) -> bool:
    return text.strip().lower() == "hello"


def _resolve_policy(app: web.Application, body: dict[str, Any]) -> str:
    routing = body.pop("routing", None)
    if not isinstance(routing, dict):
        return app["policy"]
    policy = routing.get("policy")
    if not isinstance(policy, str) or not policy:
        return app["policy"]
    return policy


def _select_upstream(app: web.Application, body: dict[str, Any]) -> str:
    upstreams = app["upstreams"]
    policy = _resolve_policy(app, body)

    if policy == "first":
        return upstreams[0]
    if policy == "last":
        return upstreams[-1]
    if policy == "round_robin":
        route_state = app["route_state"]
        route_index = route_state["index"]
        selected = upstreams[route_index % len(upstreams)]
        route_state["index"] = (route_index + 1) % len(upstreams)
        return selected
    if policy == "difficulty":
        prompt = _extract_latest_user_text(body.get("messages", []))
        return upstreams[0] if _should_use_first_upstream_for_demo(prompt) else upstreams[-1]

    raise ValueError(f"Unsupported router policy: {policy!r}")


async def handle_router_chat_completions(request: web.Request) -> web.StreamResponse:
    logger.info("Received router chat completion request")
    try:
        body = await request.json()
    except Exception as e:
        return web.json_response(_error_payload(str(e), 400), status=400)

    if not isinstance(body, dict):
        return web.json_response(_error_payload("Request body must be a JSON object", 400), status=400)

    try:
        upstream = _select_upstream(request.app, body)
    except ValueError as e:
        return web.json_response(_error_payload(str(e), 400), status=400)

    logger.info(f"Router selected upstream {upstream!r}")
    connector, endpoint = resolve_connector_and_endpoint(upstream)
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
                    f"Router upstream {upstream!r} returned HTTP {upstream_response.status}: {error_text[:1000]!r}"
                )
                return web.json_response(
                    _error_payload(error_text or f"Upstream returned HTTP {upstream_response.status}", 502),
                    status=502,
                )

            await response.prepare(request)
            async for raw in upstream_response.content:
                await response.write(raw)
                logger.debug(f"Router proxied {len(raw)} bytes from {upstream!r}")
                if _is_done_line(raw):
                    logger.debug(f"Router received [DONE] from {upstream!r}")
                    break
            await response.write_eof()
    except Exception as e:
        logger.error(f"Router proxy error for upstream {upstream!r}: {e!r}")
        if response.prepared:
            with suppress(Exception):
                await response.write(_error_chunk_bytes(f"[Router Error]: {e}"))
            with suppress(Exception):
                await response.write_eof()
            return response
        return web.json_response(_error_payload(str(e), 500), status=500)

    return response


async def serve_router(
    *,
    socket_path: str,
    upstreams: list[str],
    policy: str,
) -> None:
    logger.info(f"Starting AI router on {socket_path} (policy={policy!r}, upstreams={upstreams!r})")

    app = web.Application()
    app["upstreams"] = upstreams
    app["policy"] = policy
    app["route_state"] = {"index": 0}
    app.router.add_post("/chat/completions", handle_router_chat_completions)

    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = create_site(runner, socket_path)
        await site.start()
    except Exception as e:
        logger.error(f"Failed to start AI router on {socket_path}: {e}")
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
    """Route chat requests across multiple upstream AI sockets."""

    session_socket: str
    """Path/URL of the router socket to listen on."""

    upstream: list[str]
    """Repeatable upstream AI socket paths/URLs."""

    policy: str = "difficulty"
    """Routing policy: difficulty, first, last, or round_robin."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        if not self.upstream:
            raise ValueError("At least one --upstream must be provided")
        if self.policy not in _ROUTER_POLICIES:
            supported = ", ".join(sorted(_ROUTER_POLICIES))
            raise ValueError(f"Unsupported --policy {self.policy!r}. Supported: {supported}")
        await serve_router(socket_path=self.session_socket, upstreams=self.upstream, policy=self.policy)
