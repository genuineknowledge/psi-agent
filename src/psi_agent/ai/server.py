from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any, cast

import anyio
from aiohttp import web
from any_llm.api import ChatCompletionChunk, acompletion
from loguru import logger

from psi_agent._session_context import session_id_scope
from psi_agent.protocol import make_compaction_signal, make_error_chunk

# Raised from 1000 so a long ``content`` no longer pushes sibling keys out of
# the line. ``_describe_delta`` is the actual safeguard — see below.
_CHUNK_LOG_LIMIT = 8000

# ── 回合标记: 模型墙上时间的**权威**判据 ──────────────────────────────────────
#
# 这两端 (open / close) 的计数必须相等, 用例钉住了包括 ``response.prepare`` 失败在内
# 的每条 return 路径。
#
# **不要去改用 agent.py 那侧的标记, 也不要去补它。** 实测 2,331 个回合里有 241 个
# (10%) 只有 AI 侧标记而没有 agent 侧, 据此算出的模型耗时占比是 39.2%, 而正确值是
# 63.4% —— 差 24 个百分点, 且系统性偏低: 掉的那批恰好是走特殊分支的慢回合。
#
# 选这一侧作权威, 三个理由:
#   1. **配平在这里是结构性保证。** 所有上游调用都必经这个 handler, 于是 open 一次、
#      close 一次可以由一个函数的控制流锁死, 并被用例断言。放在 agent.py 则要靠人自
#      觉, 下次新加一个分支又会静默失衡。
#   2. **这两端量的正好是想要的东西** —— 上游墙上时间。agent.py 那一对还会把 Session
#      自己的历史读写与落盘算进去。
#   3. ``"Sending request to AI via AiClient"`` 保留用于观测**发起**, 但不得用来配对
#      算耗时。
#
# 改这几个字符串, 要同步 ``scripts/latency-probe/parse.py``。
_TURN_MARKER_OPEN = "ai-turn open"
_TURN_MARKER_CLOSE = "ai-turn close"
# 请求体都没解析出来的那类, 刻意用第三个词: 它没有配对的 open, 不该混进配平计数。
_TURN_MARKER_REJECTED = "ai-turn rejected"

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
# **兜底必须只作用于会误关思维的 provider, 不能无条件对全部 provider 生效**
# (2026-09 修正): 最初这段默认对**所有** provider 全局生效, 而 ``openai`` provider
# 直连 DeepSeek 兼容端点 (ToB 部署形态: provider=openai + api_base=api.deepseek.com)
# 并没有 auto→disabled 逻辑 —— 不传 ``reasoning_effort`` 时 thinking 本来就开着,
# 思考照常进 ``reasoning_content``。强制传 ``"medium"`` 反而把思考档位压到中档,
# 模型于是把**过程叙述**写进 ``content`` (每轮 tool call 前一段自述), 用户在飞书
# 里看到整段自我对话 —— 与本注释描述的泄漏形态一致。实测同一 tool-call prompt:
# 不传 reasoning_effort → content=0 / reasoning=306; 传 "medium" → content=34 /
# reasoning=345。
#
# 该默认值是 1.21.0 之后引入的 (1.21.0 的同一文件里没有 thinking 分支), 而依赖写的
# 是 ``any-llm-sdk>=1.21.0``, 所以是一次静默的上游行为变更改掉了我们的线上语义。
#
# 只在调用方**没给**时兜底, 给了就用它的 —— 这里是转发层, 不该覆盖上游意图。
#
# **为什么做成可配置** (``PSI_AI_REASONING_EFFORT``): 档位是延迟与可读性的直接权衡,
# 而各部署的取舍不同 —— 真链路实测同一批 6 题: ``medium`` 23.5s / ``minimal`` 13.1s /
# ``none`` 5.5s (墙钟)。但**省下的时间不是白来的**: 关掉思考会让上面那条泄漏回来,
# 实测 ``none`` 时约一半答复的开头变成英文自我对话 (自问自答直接进 ``content``),
# ``minimal`` 没观察到。所以**默认值一个字不改** (不设这个变量时行为与引入它之前
# 逐字节相同), 只把「选哪一档」交给部署方, 免得为调档去改安装包里的常量。
#
# 值是**模块常量, 进程启动时读一次** —— 调档要重启 AI 进程, 不是热更新。空串/纯空白
# 按「没设置」处理 (回落 ``_FALLBACK_REASONING_EFFORT``)。这里**刻意不做白名单校验**:
# 合法取值由上游定义 (any-llm 的 ``ReasoningEffort`` 字面量有 8 个档), 在本层再抄一份
# 只会在上游加档位时变成假的拒绝; 而非法值上游自己会拒 —— ``reasoning_effort`` 进的是
# ``CompletionParams``, 那是个 ``extra="forbid"`` 的 pydantic 模型, 值不在字面量集合里
# 当场报错, 与调用方显式传同一个错值的表现完全一致。
_REASONING_EFFORT_ENV = "PSI_AI_REASONING_EFFORT"
# 未设置 (或设成空串/空白) 时下发的档位 —— 与引入本变量之前一字不改。
_FALLBACK_REASONING_EFFORT = "medium"
_DEFAULT_REASONING_EFFORT = os.environ.get(_REASONING_EFFORT_ENV, "").strip() or _FALLBACK_REASONING_EFFORT
# 只对会因缺省 auto 而关掉 thinking 的 provider 兜底 (见上); 其余 provider 保持
# 不传, 交给上游默认行为。
_REASONING_EFFORT_DEFAULT_PROVIDERS = frozenset({"deepseek"})


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
    """一次上游调用的入口 —— 顺带把这一回合的会话 id 绑上, 供日志归属。

    会话 id 只能从请求体 ``routing.session_id`` 取: AI 是 socket 后面另一个 aiohttp
    进程, Session 那边的 ContextVar 过不来。
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception as e:
        # 刻意用第三个标记词: 它没有配对的 open, 不该混进配平计数 —— 但也不能不记,
        # 否则「上游一直没被调用」与「请求根本没进来」在日志里长得一样。
        logger.error(f"{_TURN_MARKER_REJECTED} unparseable body: {e!r}")
        # OpenAI-compatible error response.
        return web.json_response(
            {"error": {"message": str(e), "type": "invalid_request_error", "param": None, "code": 400}},
            status=400,
        )

    routing = body.get("routing")
    turn_session_id = ""
    if isinstance(routing, dict):
        raw_sid = routing.get("session_id")
        if isinstance(raw_sid, str):
            turn_session_id = raw_sid.strip()

    with session_id_scope(turn_session_id):
        return await _forward_chat_completion(request, body)


async def _forward_chat_completion(request: web.Request, body: dict[str, Any]) -> web.StreamResponse:
    logger.info(_TURN_MARKER_OPEN)
    logger.debug(f"Request body: {json.dumps(body, ensure_ascii=False)[:_CHUNK_LOG_LIMIT]}")
    turn_started = anyio.current_time()

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
    # ``setdefault`` 而非赋值 —— 调用方显式给的值 (含 ``"none"``) 优先, 部署方定的档位
    # (``PSI_AI_REASONING_EFFORT``) 只补「谁都没表态」这一种情况。
    # 只对会误关思维的 provider (deepseek) 兜底; openai 打 DeepSeek 兼容端点时强制
    # medium 反而让模型把过程叙述写进 content (线上泄漏), 见常量注释。
    if provider in _REASONING_EFFORT_DEFAULT_PROVIDERS:
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
        # 这条早退分支原先只有上面那句 warning、没有任何 close —— 它是 open/close 计数
        # 不配平的第二个来源 (另一个是 agent.py 侧的标记本就不全)。
        logger.info(f"{_TURN_MARKER_CLOSE} elapsed_ms=0 outcome=prepare_failed")
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

    # 三条终态日志收成一条出口: 结局进 ``outcome=`` 字段, 于是「配平」只需数两个词,
    # 不必知道有几种收尾方式 —— 将来多一种结局也不会让脚本漏计一个 close。
    if client_gone:
        outcome = "client_disconnect"
    elif upstream_error:
        outcome = "upstream_error"
    else:
        outcome = "ok"
    elapsed_ms = int((anyio.current_time() - turn_started) * 1000)
    logger.info(f"{_TURN_MARKER_CLOSE} elapsed_ms={elapsed_ms} outcome={outcome}")
    return response
