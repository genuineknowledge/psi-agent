from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import anyio

from psi_agent._logging import setup_logging
from psi_agent.ai.anthropic_messages.server import serve_anthropic_messages
from psi_agent.ai.openai_completions.server import serve_openai_completions
from psi_agent.errors import UserFacingError
from psi_agent.run.config import RunProfileConfig, load_run_profile_config
from psi_agent.session import build_session_agent
from psi_agent.workspace import resolve_workspace_path

AiBackend = Literal["openai-completions", "anthropic-messages"]
OutputFormat = Literal["text", "json"]


@dataclass(frozen=True)
class RunResult:
    text: str
    reasoning: str
    had_error: bool = False


@dataclass
class Run:
    """Run a one-shot workspace-backed agent call."""

    message: str
    """Message to send to the agent."""

    workspace: str = ""
    """Path to the workspace directory. Falls back to default_workspace in config."""

    ai_socket: str = ""
    """Existing AI backend socket path or http(s) /v1 endpoint. If omitted, a temporary backend is started."""

    ai: str = ""
    """AI backend to start when ai_socket is omitted. Allowed: openai-completions, anthropic-messages."""

    model: str = ""
    """Model name. Falls back to backend-specific environment variables."""

    api_key: str = ""
    """API key for the temporary backend. Falls back to backend-specific environment variables."""

    base_url: str = ""
    """Base URL for the temporary backend. Falls back to backend-specific environment variables."""

    profile: str = ""
    """Profile name in the psi-agent config file."""

    config: str = ""
    """Path to psi-agent config TOML. Defaults to PSI_AGENT_CONFIG or ~/.psi-agent/config.toml."""

    output_format: OutputFormat = "text"
    """Output format for stdout."""

    show_reasoning: bool = False
    """Write reasoning and tool trace chunks to stderr."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        result = await run_once(
            workspace=self.workspace,
            message=self.message,
            ai_socket=self.ai_socket,
            ai=self.ai,
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            profile=self.profile,
            config=self.config,
        )

        if self.show_reasoning and result.reasoning:
            sys.stderr.write(result.reasoning)
            if not result.reasoning.endswith("\n"):
                sys.stderr.write("\n")

        if result.had_error:
            message = result.text or result.reasoning or "Agent run failed"
            if self.verbose:
                raise UserFacingError(f"Agent run failed: {message}")
            raise _friendly_agent_error(message)

        if self.output_format == "json":
            sys.stdout.write(json.dumps({"text": result.text}, ensure_ascii=False))
            sys.stdout.write("\n")
        else:
            sys.stdout.write(result.text)
            if not result.text.endswith("\n"):
                sys.stdout.write("\n")


async def run_once(
    *,
    workspace: str,
    message: str,
    ai_socket: str = "",
    ai: str = "",
    model: str = "",
    api_key: str = "",
    base_url: str = "",
    profile: str = "",
    config: str = "",
) -> RunResult:
    profile_config = (
        load_run_profile_config(config_path=config, profile=profile)
        if _should_load_profile_config(
            workspace=workspace,
            ai_socket=ai_socket,
            ai=ai,
            model=model,
            api_key=api_key,
            base_url=base_url,
            profile=profile,
            config=config,
        )
        else RunProfileConfig()
    )
    workspace_path = resolve_workspace_path(workspace or profile_config.workspace)
    effective_ai = _resolve_ai(ai=ai, profile_config=profile_config)
    effective_model = _resolve_model(ai=effective_ai, model=model or profile_config.model)
    effective_api_key = api_key or profile_config.api_key
    effective_base_url = base_url or profile_config.base_url

    if ai_socket:
        return await _run_against_ai_socket(
            workspace=str(workspace_path),
            message=message,
            ai_socket=ai_socket,
            model=effective_model,
        )

    _validate_temporary_backend_config(
        ai=effective_ai,
        model=effective_model,
        api_key=effective_api_key,
        base_url=effective_base_url,
    )

    with _temporary_directory(prefix="psi-agent-run-") as tmp_dir:
        backend_socket = _make_temporary_backend_endpoint(tmp_dir)
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                partial(
                    _serve_temporary_backend,
                    ai=effective_ai,
                    socket_path=backend_socket,
                    model=effective_model,
                    api_key=effective_api_key,
                    base_url=effective_base_url,
                )
            )
            await _wait_for_backend(backend_socket)
            result = await _run_against_ai_socket(
                workspace=str(workspace_path),
                message=message,
                ai_socket=backend_socket,
                model=effective_model,
            )
            tg.cancel_scope.cancel()
            return result


@contextmanager
def _temporary_directory(*, prefix: str) -> Iterator[str]:
    root = Path(tempfile.gettempdir())
    for _ in range(100):
        path = root / f"{prefix}{uuid.uuid4().hex}"
        try:
            path.mkdir(mode=0o755)
        except FileExistsError:
            continue
        break
    else:
        raise FileExistsError(f"Could not create temporary directory under {root}")

    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


async def _run_against_ai_socket(
    *,
    workspace: str,
    message: str,
    ai_socket: str,
    model: str,
) -> RunResult:
    agent = await build_session_agent(workspace=workspace, ai_socket=ai_socket, model=model)
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    had_error = False

    try:
        async for chunk in agent.run({"role": "user", "content": message}):
            for choice in chunk.choices:
                if choice.delta.content:
                    content_parts.append(choice.delta.content)
                if choice.delta.reasoning_content:
                    reasoning_parts.append(choice.delta.reasoning_content)
                if choice.finish_reason == "error":
                    had_error = True
    finally:
        await agent.close()

    return RunResult(text="".join(content_parts), reasoning="".join(reasoning_parts), had_error=had_error)


async def _serve_temporary_backend(
    *,
    ai: AiBackend,
    socket_path: str,
    model: str,
    api_key: str,
    base_url: str,
) -> None:
    if ai == "openai-completions":
        await serve_openai_completions(
            socket_path=socket_path,
            model=model,
            api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
            base_url=_resolve_base_url(ai=ai, base_url=base_url),
        )
        return

    await serve_anthropic_messages(
        socket_path=socket_path,
        model=model,
        api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
        base_url=_resolve_base_url(ai=ai, base_url=base_url),
    )


def _make_temporary_backend_endpoint(tmp_dir: str) -> str:
    if os.name != "nt":
        return str(Path(tmp_dir) / "ai.sock")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1"


async def _wait_for_backend(socket_path: str, timeout_sec: float = 10.0) -> None:
    if socket_path.startswith("http://"):
        await _wait_for_tcp_url(socket_path, timeout_sec=timeout_sec)
        return
    await _wait_for_socket(socket_path, timeout_sec=timeout_sec)


async def _wait_for_socket(socket_path: str, timeout_sec: float) -> None:
    deadline = anyio.current_time() + timeout_sec
    socket_anyio = anyio.Path(socket_path)
    while anyio.current_time() < deadline:
        if await socket_anyio.exists():
            await anyio.sleep(0.1)
            return
        await anyio.sleep(0.05)
    raise TimeoutError(f"Temporary AI backend socket was not created: {socket_path}")


async def _wait_for_tcp_url(url: str, timeout_sec: float) -> None:
    parsed = urlparse(url)
    if not parsed.hostname or parsed.port is None:
        raise ValueError(f"Temporary AI backend URL must include host and port: {url}")

    deadline = anyio.current_time() + timeout_sec
    while anyio.current_time() < deadline:
        try:
            stream = await anyio.connect_tcp(parsed.hostname, parsed.port)
        except OSError:
            await anyio.sleep(0.05)
            continue
        await stream.aclose()
        await anyio.sleep(0.1)
        return
    raise TimeoutError(f"Temporary AI backend TCP listener was not ready: {url}")


def _resolve_model(*, ai: AiBackend, model: str) -> str:
    if model:
        return model
    if ai == "openai-completions":
        return os.environ.get("OPENAI_MODEL", "gpt-4")
    return os.environ.get("ANTHROPIC_MODEL", "")


def _resolve_ai(*, ai: str, profile_config: RunProfileConfig) -> AiBackend:
    if not ai:
        return profile_config.ai or "openai-completions"
    if ai == "openai-completions":
        return "openai-completions"
    if ai == "anthropic-messages":
        return "anthropic-messages"
    raise UserFacingError(
        f"Unsupported AI backend: {ai}",
        "Use --ai openai-completions or --ai anthropic-messages.",
    )


def _should_load_profile_config(
    *,
    workspace: str,
    ai_socket: str,
    ai: str,
    model: str,
    api_key: str,
    base_url: str,
    profile: str,
    config: str,
) -> bool:
    if not workspace:
        return True
    return bool(profile or config or os.environ.get("PSI_AGENT_PROFILE")) or not (
        ai_socket or ai or model or api_key or base_url
    )


def _validate_temporary_backend_config(
    *,
    ai: AiBackend,
    model: str,
    api_key: str,
    base_url: str,
) -> None:
    env_key = "OPENAI_API_KEY" if ai == "openai-completions" else "ANTHROPIC_API_KEY"
    effective_api_key = api_key or os.environ.get(env_key, "")
    if not effective_api_key:
        raise UserFacingError(
            "API key is not configured.",
            f"Set {env_key}, pass --api-key, or configure api_key_env in ~/.psi-agent/config.toml.",
        )

    if not model:
        env_model = "OPENAI_MODEL" if ai == "openai-completions" else "ANTHROPIC_MODEL"
        raise UserFacingError(
            "Model is not configured.",
            f"Set {env_model}, pass --model, or configure model in ~/.psi-agent/config.toml.",
        )

    if not _resolve_base_url(ai=ai, base_url=base_url):
        env_base_url = "OPENAI_BASE_URL" if ai == "openai-completions" else "ANTHROPIC_BASE_URL"
        raise UserFacingError(
            "Base URL is not configured.",
            f"Set {env_base_url}, pass --base-url, or configure base_url in ~/.psi-agent/config.toml.",
        )


def _resolve_base_url(*, ai: AiBackend, base_url: str) -> str:
    if base_url:
        return base_url
    if ai == "openai-completions":
        return os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    return os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")


def _friendly_agent_error(message: str) -> UserFacingError:
    lower = message.lower()
    if "401" in lower or "unauthorized" in lower or "authentication" in lower or "api key" in lower:
        return UserFacingError(
            "Model service authentication failed.",
            "Check your API key environment variable or profile config, then try again.",
        )
    if "connection error" in lower or "cannot connect" in lower or "connection refused" in lower:
        return UserFacingError(
            "Cannot connect to the model service.",
            "Check your base_url/network settings, or run with --verbose for technical details.",
        )
    if "timeout" in lower or "timed out" in lower:
        return UserFacingError(
            "The model service did not respond in time.",
            "Try again, or check your network and model provider status.",
        )
    return UserFacingError(
        "Agent run failed.",
        "Run again with --verbose to see technical details.",
    )
