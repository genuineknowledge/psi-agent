from __future__ import annotations

import json
from typing import Any

import anyio
from aiohttp import ClientSession, TCPConnector, web
from loguru import logger

from psi_agent.protocol import ErrorResponse


async def serve_anthropic_messages(
    *,
    socket_path: str,
    model: str,
    api_key: str,
    base_url: str,
) -> None:
    logger.info(f"Starting anthropic-messages AI service on {socket_path} (model={model}, base_url={base_url})")

    app = web.Application()
    app["model"] = model
    app["api_key"] = api_key
    app["base_url"] = base_url
    app.router.add_post("/v1/chat/completions", handle_chat_completions)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, socket_path)
    await site.start()

    logger.info(f"anthropic-messages listening on {socket_path}")

    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down anthropic-messages on {socket_path}")
        await runner.cleanup()


def _convert_openai_tools_to_anthropic(tools: list[dict]) -> list[dict]:
    result: list[dict] = []
    for tool in tools:
        func = tool.get("function", {})
        result.append(
            {
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}, "required": []}),
            }
        )
    return result


def _convert_openai_messages_to_anthropic(
    messages: list[dict],
) -> tuple[list[dict], list[str]]:
    anthropic_messages: list[dict] = []
    system_content: list[str] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            system_content.append(str(content))
            continue

        if role == "assistant" and msg.get("tool_calls"):
            tool_use_blocks: list[dict] = []
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError, TypeError:
                    args = {}
                tool_use_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": args,
                    }
                )
            anthropic_messages.append({"role": "assistant", "content": tool_use_blocks})
            continue

        if role == "tool":
            anthropic_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": str(content) if content else "",
                        }
                    ],
                }
            )
            continue

        anthropic_messages.append({"role": role, "content": content})

    return anthropic_messages, system_content


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    logger.info("Received chat completion request (Anthropic backend)")
    try:
        body = await request.json()
        logger.debug(f"Request body: {json.dumps(body, ensure_ascii=False)[:500]}")
    except Exception as e:
        logger.error(f"Failed to parse request body: {e}")
        err = ErrorResponse(message=str(e), type="invalid_request", code="400")
        return web.json_response(err.to_dict(), status=400)

    model = request.app["model"]
    api_key = request.app["api_key"]
    base_url = request.app["base_url"]

    upstream_url = base_url.rstrip("/") + "/messages"

    openai_messages = body.get("messages", [])
    openai_tools = body.get("tools", [])

    anthropic_messages, system_content = _convert_openai_messages_to_anthropic(openai_messages)
    anthropic_tools = _convert_openai_tools_to_anthropic(openai_tools)

    request_body: dict = {
        "model": model,
        "messages": anthropic_messages,
        "max_tokens": body.get("max_tokens", 4096),
        "stream": True,
    }
    if system_content:
        request_body["system"] = "\n".join(system_content)
    if anthropic_tools:
        request_body["tools"] = anthropic_tools

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    logger.info(f"Forwarding to upstream (Anthropic): {upstream_url}")
    logger.debug(f"Anthropic request: {json.dumps(request_body, ensure_ascii=False)[:500]}")

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await response.prepare(request)

    try:
        use_ssl = base_url.startswith("https")
        async with ClientSession(connector=TCPConnector(ssl=use_ssl)) as session:  # noqa: SIM117
            async with session.post(upstream_url, json=request_body, headers=headers) as upstream_resp:
                logger.info(f"Upstream response status: {upstream_resp.status}")
                if upstream_resp.status != 200:
                    error_text = await upstream_resp.text()
                    logger.error(f"Upstream error: {error_text[:500]}")
                    err_data = json.dumps(
                        {
                            "error": {
                                "message": f"Upstream error: {error_text[:200]}",
                                "type": "upstream_error",
                                "code": str(upstream_resp.status),
                            }
                        }
                    )
                    await response.write(f"data: {err_data}\n\n".encode())
                    await response.write(b"data: [DONE]\n\n")
                    return response

                await _convert_anthropic_stream_to_openai_sse(response, upstream_resp.content)
    except Exception as e:
        logger.error(f"Error forwarding to upstream: {e}")
        err_data = json.dumps(
            {
                "error": {
                    "message": str(e),
                    "type": "upstream_connection",
                    "code": "502",
                }
            }
        )
        await response.write(f"data: {err_data}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        return response

    await response.write(b"data: [DONE]\n\n")
    logger.debug("Request completed")
    return response


async def _convert_anthropic_stream_to_openai_sse(
    response: web.StreamResponse,
    upstream_content: Any,
) -> None:
    current_tool_calls: dict[int, dict] = {}
    chunk_index = 0

    async for raw_line in upstream_content:
        line = raw_line.decode().strip()
        if not line:
            continue

        logger.debug(f"Anthropic raw SSE: {line[:300]}")

        event_data: dict | None = None

        if line.startswith("data: "):
            try:
                event_data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
        elif line.startswith("event: "):
            continue
        else:
            try:
                event_data = json.loads(line)
            except json.JSONDecodeError:
                continue

        if event_data is None:
            continue

        event_type = event_data.get("type", "")
        index = event_data.get("index", 0)

        if event_type == "message_stop":
            continue

        if event_type == "content_block_start":
            cb = event_data.get("content_block", {})
            if cb.get("type") == "tool_use":
                current_tool_calls[index] = {
                    "index": index,
                    "id": cb.get("id", ""),
                    "type": "function",
                    "function": {"name": cb.get("name", ""), "arguments": ""},
                }
            continue

        if event_type == "content_block_delta":
            delta = event_data.get("delta", {})
            delta_type = delta.get("type", "")

            if delta_type == "thinking_delta":
                thinking_text = delta.get("thinking", "")
                chunk = {
                    "id": f"chatcmpl-{chunk_index}",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"reasoning_content": thinking_text},
                            "finish_reason": None,
                        }
                    ],
                }
                await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
                chunk_index += 1

            elif delta_type == "text_delta":
                text = delta.get("text", "")
                chunk = {
                    "id": f"chatcmpl-{chunk_index}",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": text},
                            "finish_reason": None,
                        }
                    ],
                }
                await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
                chunk_index += 1

            elif delta_type == "input_json_delta":
                partial = delta.get("partial_json", "")
                if index in current_tool_calls:
                    current_tool_calls[index]["function"]["arguments"] += partial
                    tc_copy = dict(current_tool_calls[index])
                    # Send partial tool_call delta
                    chunk = {
                        "id": f"chatcmpl-{chunk_index}",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": "",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": index,
                                            "id": tc_copy.get("id", ""),
                                            "type": "function",
                                            "function": {
                                                "name": tc_copy["function"]["name"],
                                                "arguments": partial,
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": None,
                            }
                        ],
                    }
                    await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    chunk_index += 1

    final_chunk = {
        "id": f"chatcmpl-{chunk_index}",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "",
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }
    await response.write(f"data: {json.dumps(final_chunk)}\n\n".encode())
