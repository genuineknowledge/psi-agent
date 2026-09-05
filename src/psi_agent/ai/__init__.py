"""AI backend — unified multi-provider LLM client served over a Unix socket, TCP or Named Pipe."""

from __future__ import annotations

import os
from dataclasses import dataclass

import anyio
import httpx
from aiohttp import web
from aiohttp.typedefs import Handler
from any_llm.any_llm import AnyLLM
from any_llm.providers.anthropic.base import BaseAnthropicProvider
from any_llm.providers.openai.base import BaseOpenAIProvider
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent._sockets import create_site
from psi_agent._tls import client_ssl_context
from psi_agent.protocol import DEFAULT_MAX_CONTEXT_TOKENS

from .server import handle_chat_completions


def _build_http_client(provider: str) -> httpx.AsyncClient | None:
    """给 any-llm 备一个自带 TLS 上下文的 httpx client; 不适用则回 ``None``。

    存在理由只有一个: 默认组列表下部分网络会丢 TLS 握手包, 表现为对话请求全部
    超时 (实测 19s 超时 vs 换上下文后 0.6s 拿到上游响应)。见 ``psi_agent._tls``。

    **为什么要按 provider 挑**: ``client_args`` 是直接灌进 provider 的 client
    构造函数的。OpenAI 与 Anthropic 的 SDK 收 ``http_client``, 但 Gemini
    (google-genai) 与 Mistral 不收 —— 无条件传过去, 那些 provider 会当场
    ``TypeError``, 等于为了修一条路把另外几条弄断。

    在此建一次而不是每请求一个: 每请求新建等于扔掉连接池, 每次对话白付一轮
    TCP + TLS 握手; 而这个进程全程只对一个上游说话。
    """
    try:
        provider_class = AnyLLM.get_provider_class(provider)
    except Exception as e:
        # 拿不到 provider 类不该拦住服务起来 —— 真正的报错要留给第一次请求,
        # 那时 any-llm 会给出带 provider 名字的完整信息。
        logger.debug(f"Cannot resolve provider {provider!r} for TLS client selection: {e}")
        return None
    if not issubclass(provider_class, (BaseOpenAIProvider, BaseAnthropicProvider)):
        logger.debug(f"Provider {provider!r} takes no http_client; using any-llm defaults")
        return None
    # 不设 timeout: 超时由上层 (Channel/Gateway) 决定, 这里设了会把长回答截断。
    return httpx.AsyncClient(verify=client_ssl_context(), timeout=None)


async def serve_ai(
    *,
    socket_path: str,
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    max_context_tokens: int = 0,
    handler: Handler,
) -> None:
    """Serve an AI backend on a Unix socket, TCP address or Named Pipe (see ``psi_agent._sockets``)."""

    api_key_status = "set" if api_key else "empty"
    logger.info(
        f"Starting AI service on {socket_path} "
        f"(provider={provider!r}, model={model!r}, base_url={base_url}, api_key={api_key_status})"
    )

    # Large conversation contexts (long histories, tool outputs) routinely exceed
    # aiohttp's 1 MiB default body limit, which would reject the request with
    # HTTPRequestEntityTooLarge before it ever reaches the upstream. Match the
    # gateway app's 100 MiB ceiling so the forwarder accepts the same payloads.
    app = web.Application(client_max_size=100 * 1024 * 1024)
    app["provider"] = provider
    app["model"] = model
    app["api_key"] = api_key
    app["base_url"] = base_url
    app["max_context_tokens"] = max_context_tokens
    app["http_client"] = _build_http_client(provider)
    app.router.add_post("/chat/completions", handler)

    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = create_site(runner, socket_path)
        await site.start()
    except Exception as e:
        logger.error(f"Failed to start AI service on {socket_path}: {e}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        raise

    logger.info(f"AI listening on {socket_path}")

    try:
        await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down AI on {socket_path}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
            http_client = app["http_client"]
            if http_client is not None:
                await http_client.aclose()
        logger.info(f"AI shutdown complete on {socket_path}")


@dataclass
class Ai:
    """Start an AI backend service that forwards to any LLM provider."""

    session_socket: str
    """Address to listen on: Unix socket path (POSIX), ``http(s)://host:port``, or ``\\\\.\\pipe\\name`` (Windows)."""

    provider: str = ""
    """Provider key (openai, anthropic, gemini, etc.). Falls back to PSI_AI_PROVIDER env var."""

    model: str = ""
    """Model name. Falls back to PSI_AI_MODEL env var."""

    api_key: str = ""
    """API key for the upstream service. Falls back to PSI_AI_API_KEY env var."""

    base_url: str = ""
    """Base URL of the upstream API. Falls back to PSI_AI_BASE_URL env var."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    max_context_tokens: int = -1
    """Prompt token threshold for triggering compaction (default 200K).
    -1 = use PSI_MAX_CONTEXT_TOKENS env var or 200K. 0 disables compaction.
    CLI: --max-context-tokens."""

    async def run(self) -> None:
        """Start the server and block until cancelled."""
        setup_logging(verbose=self.verbose)
        provider = self.provider or os.environ.get("PSI_AI_PROVIDER", "")
        model = self.model or os.environ.get("PSI_AI_MODEL", "")
        api_key = self.api_key or os.environ.get("PSI_AI_API_KEY", "")
        base_url = self.base_url or os.environ.get("PSI_AI_BASE_URL", "")
        if self.max_context_tokens == -1:
            env_val = os.environ.get("PSI_MAX_CONTEXT_TOKENS", "")
            if env_val:
                try:
                    self.max_context_tokens = int(env_val)
                except ValueError:
                    logger.warning(
                        f"Invalid PSI_MAX_CONTEXT_TOKENS={env_val!r}, using default {DEFAULT_MAX_CONTEXT_TOKENS}"
                    )
                    self.max_context_tokens = DEFAULT_MAX_CONTEXT_TOKENS
            else:
                self.max_context_tokens = DEFAULT_MAX_CONTEXT_TOKENS
        logger.debug(
            f"AI resolved params: provider={provider!r}, model={model!r}, "
            f"base_url={base_url!r}, api_key={'*' * 8 if api_key else '(empty)'}, "
            f"max_context_tokens={self.max_context_tokens}"
        )
        await serve_ai(
            socket_path=self.session_socket,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_context_tokens=self.max_context_tokens,
            handler=handle_chat_completions,
        )
