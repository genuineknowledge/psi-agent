from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, cast

import anyio
from aiohttp import web
from any_llm.api import ChatCompletionChunk, acompletion
from loguru import logger

from psi_agent.protocol import make_compaction_signal, make_error_chunk

# Raised from 1000 so a long ``content`` no longer pushes sibling keys out of
# the line. ``_describe_delta`` is the actual safeguard — see below.
_CHUNK_LOG_LIMIT = 8000

# Every field a provider might carry reasoning in, plus the ones we consume.
# ``session/ai_client.py`` reads ``reasoning`` only, so a provider emitting
# ``reasoning_content`` or ``thinking`` would be silently dropped — telling that
# apart from "the model never emitted reasoning at all" is exactly what the
# census line is for.
_DELTA_FIELDS = ("content", "reasoning", "reasoning_content", "thinking", "role")

# The same names, looked for on the *request* side. Production measured 7908
# response chunks with all three reasoning fields ABSENT, which rules out "the
# model emitted reasoning and we dropped it" but not *why* it never used the
# channel. Answering that needs the request we sent, and ``Request body`` is
# truncated — so the outgoing messages get a census of their own.
_MESSAGE_REASONING_FIELDS = ("reasoning_content", "reasoning", "thinking")

# ** 为什么要显式传 reasoning_effort **: any-llm 的 DeepSeek provider 把
# ``reasoning_effort`` 缺省值 ``"auto"`` 当成「调用方没要思维」, 于是往请求体里塞
# ``extra_body.thinking={"type": "disabled"}`` —— 见 1.26.0 的
# ``providers/deepseek/deepseek.py``。DeepSeek V4 官方默认是**开**思维, any-llm
# 为对齐旧版 ``deepseek-chat`` 行为主动反转成关。
#
# 后果不是「字段丢了」而是「思维根本没生成」: 模型被关掉思维通道后仍要推理, 就把
# 自我对话直接写进 ``content`` —— 这就是线上看到的泄漏 (复述提问 + 自问自答)。
# 实测同一 prompt: 不传 = 0/9 个 chunk 带思维链, 传 "medium" = 20/23。
#
# 该默认值是 1.21.0 之后引入的 (1.21.0 的同一文件里没有 thinking 分支), 而依赖写的
# 是 ``any-llm-sdk>=1.21.0``, 所以是一次静默的上游行为变更改掉了我们的线上语义。
#
# 只在调用方**没给**时兜底, 给了就用它的 —— 这里是转发层, 不该覆盖上游意图。
_DEFAULT_REASONING_EFFORT = "medium"


def _describe_delta(data: str) -> str:
    """One never-truncated line naming which delta fields exist, and how long.

    Truncating the raw chunk is not merely lossy, it destroys the judgement:
    a key sitting past the cutoff and a key that was never sent look identical.
    This line is bounded by the *number* of fields rather than their size, so it
    survives any ``content`` length.

    Values are never echoed — only names, lengths, and presence.
    """
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return "unparseable"
    if not isinstance(parsed, dict):
        return f"non-object payload ({type(parsed).__name__})"
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        return "no choices"
    first = choices[0]
    if not isinstance(first, dict):
        return f"non-object choice ({type(first).__name__})"
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return f"no delta ({type(delta).__name__})"

    parts: list[str] = []
    for field in _DELTA_FIELDS:
        if field not in delta or delta[field] is None:
            parts.append(f"{field}=ABSENT")
            continue
        value = delta[field]
        parts.append(f"{field}={len(value)}ch" if isinstance(value, str) else f"{field}={value!r}")
    tool_calls = delta.get("tool_calls")
    parts.append(f"tool_calls={len(tool_calls) if isinstance(tool_calls, list) else 0}")
    # Any reasoning-ish key we do not know about yet.
    extra = [k for k in delta if k not in {*_DELTA_FIELDS, "tool_calls"}]
    if extra:
        parts.append(f"other={sorted(extra)}")
    parts.append(f"finish_reason={first.get('finish_reason')!r}")
    return " ".join(parts)


def _describe_messages(messages: Any) -> str:
    """One never-truncated line describing the messages we are about to send.

    The counterpart to ``_describe_delta``, and bounded the same way: by the
    *number* of messages rather than their size, so a 100 KB history still fits
    on one line. ``Request body`` truncates, which cannot answer "was
    ``reasoning_content`` on the wire at all" — a key past the cutoff and a key
    never sent look identical, the same trap the delta census exists for.

    Reports each message as ``<index><role>`` plus any reasoning-ish key it
    carries with that key's length, e.g. ``3assistant(reasoning_content=812ch,
    tool_calls=1)``. Values are never echoed — only names, lengths, and counts.
    """
    if not isinstance(messages, list):
        return f"non-list messages ({type(messages).__name__})"
    if not messages:
        return "no messages"

    parts: list[str] = []
    carriers = 0
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            parts.append(f"{i}non-object({type(msg).__name__})")
            continue
        role = msg.get("role")
        notes: list[str] = []
        for field in _MESSAGE_REASONING_FIELDS:
            value = msg.get(field)
            if value is None:
                continue
            carriers += 1
            notes.append(f"{field}={len(value)}ch" if isinstance(value, str) else f"{field}={type(value).__name__}")
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            notes.append(f"tool_calls={len(tool_calls)}")
        content = msg.get("content")
        if isinstance(content, str):
            notes.append(f"content={len(content)}ch")
        elif content is None:
            notes.append("content=ABSENT")
        else:
            notes.append(f"content={type(content).__name__}")
        parts.append(f"{i}{role}({', '.join(notes)})" if notes else f"{i}{role}")
    # The headline number: how many messages carry a reasoning field at all.
    # Zero here alongside zero on the response side means the channel was never
    # opened in either direction.
    return f"n={len(messages)} reasoning_carriers={carriers} | " + " ".join(parts)


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    logger.info("Received chat completion request")
    try:
        body: dict[str, Any] = await request.json()
        logger.debug(f"Request body: {json.dumps(body, ensure_ascii=False)[:_CHUNK_LOG_LIMIT]}")
    except Exception as e:
        logger.error(f"Failed to parse request body: {e!r}")
        # OpenAI-compatible error response.
        return web.json_response(
            {"error": {"message": str(e), "type": "invalid_request_error", "param": None, "code": 400}},
            status=400,
        )

    provider = request.app["provider"]
    model = request.app["model"]
    api_key = request.app["api_key"]
    base_url = request.app["base_url"]
    # ``client_args`` 走的是 provider 的 client 构造, 不是请求体 —— 换掉 TLS
    # 上下文只能从这里进去, any-llm 内部自己 new httpx client。client 由
    # ``serve_ai`` 按 provider 建 (不收 http_client 的 provider 拿到 None)、
    # 进程退出时关。见 psi_agent._tls 与 _build_http_client。
    # 用 get 而不是 []: 这个 handler 也被不经 ``serve_ai`` 装配的 app 用 (测试、
    # 将来的嵌入式用法), 少一个键不该变成 500。缺了就走 any-llm 默认 client。
    http_client = request.app.get("http_client")
    client_args: dict[str, Any] = {"client_args": {"http_client": http_client}} if http_client else {}

    logger.debug(f"Body keys before pop: {list(body)}")
    messages = body.pop("messages", [])
    # Logged after the pop so it describes exactly what goes upstream.
    logger.debug(f"message census: {_describe_messages(messages)}")
    body.pop("stream", None)
    body.pop("provider", None)
    body.pop("model", None)
    body.pop("api_key", None)
    body.pop("api_base", None)
    body.pop("routing", None)
    # 见 ``_DEFAULT_REASONING_EFFORT``: 不传等于让 DeepSeek provider 关掉思维模式。
    # ``setdefault`` 而非赋值 —— 调用方显式给的值 (含 ``"none"``) 优先。
    body.setdefault("reasoning_effort", _DEFAULT_REASONING_EFFORT)
    stream_opts = body.get("stream_options", {})
    if isinstance(stream_opts, dict):
        stream_opts["include_usage"] = True
        body["stream_options"] = stream_opts
    else:
        body["stream_options"] = {"include_usage": True}
    logger.debug(f"Body keys to passthrough: {list(body)}")

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            # SSE standard headers — per MDN / HTML spec
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    try:
        await response.prepare(request)
    except Exception:
        logger.warning("Client disconnected before SSE response prepared")
        return response

    logger.debug(f"Forwarding to upstream: provider={provider!r}, model={model!r}, base_url={base_url!r}")
    upstream_error = False
    client_gone = False
    compaction_needed = False
    stream: AsyncIterator[ChatCompletionChunk] | None = None
    try:
        stream = cast(
            AsyncIterator[ChatCompletionChunk],
            # ``acompletion()`` returns ``ChatCompletion | AsyncIterator[ChatCompletionChunk]``
            # depending on the ``stream`` flag.  We always pass ``stream=True``, so the
            # runtime type is always ``AsyncIterator[ChatCompletionChunk]`` — the cast is safe.
            await acompletion(
                provider=provider,
                model=model,
                messages=messages,
                stream=True,
                api_key=api_key,
                api_base=base_url,
                **client_args,
                **body,
            ),
        )
        logger.debug("Starting to consume upstream SSE stream")
        max_context_tokens: int = request.app.get("max_context_tokens", 0)
        compaction_usage: dict[str, int] = {}
        async for chunk in stream:
            if max_context_tokens > 0 and chunk.usage and chunk.usage.prompt_tokens > max_context_tokens:
                compaction_needed = True
                compaction_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }
                logger.debug(
                    f"Compaction needed: prompt_tokens={chunk.usage.prompt_tokens} > threshold={max_context_tokens}"
                )
            data = chunk.model_dump_json()
            logger.debug(f"delta keys: {_describe_delta(data)}")
            logger.debug(f"SSE chunk: {data[:_CHUNK_LOG_LIMIT]}")
            await response.write(f"data: {data}\n\n".encode())
        if compaction_needed:
            signal = json.dumps(
                make_compaction_signal(
                    prompt_tokens=compaction_usage.get("prompt_tokens", 0),
                    threshold=max_context_tokens,
                )
            )
            logger.debug(f"SSE compaction signal: {signal[:500]}")
            await response.write(f"data: {signal}\n\n".encode())
    except ConnectionResetError:
        # Downstream client (session/channel) disconnected — e.g. user pressed
        # "stop". The finally block closes the upstream provider stream.
        client_gone = True
        logger.info("Client disconnected; cancelling upstream stream")
    except Exception as e:
        upstream_error = True
        logger.error(f"Error forwarding to upstream (provider={provider!r}, model={model!r}): {e!r}")
        err_chunk = json.dumps(make_error_chunk(f"[Upstream Error]: {e}"))
        logger.debug(f"SSE error chunk: {err_chunk[:1000]}")
        try:
            await response.write(f"data: {err_chunk}\n\n".encode())
        except Exception:
            logger.warning("Failed to send upstream error chunk to client")
    else:
        if compaction_needed:
            logger.debug("Request completed with compaction signal")
        else:
            logger.debug("Upstream stream completed successfully")
    finally:
        # Always release the upstream connection, even on cancellation
        # (client disconnect / shutdown). Shielded so aclose() completes
        # while a CancelledError is propagating through this finally.
        if stream is not None:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                logger.debug("Closing upstream stream")
                with anyio.CancelScope(shield=True):
                    try:
                        await aclose()
                    except Exception as close_err:
                        logger.warning(f"Failed to close upstream stream: {close_err}")

    if client_gone:
        logger.info("Request cancelled by client disconnect")
    elif upstream_error:
        logger.info("Request completed with upstream error")
    else:
        logger.info("Request completed successfully")
    return response
