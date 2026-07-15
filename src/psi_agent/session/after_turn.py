"""Workspace after-turn hook — post-turn self-evolution wiring.

The framework calls an optional ``System.after_turn(...)`` coroutine from the
workspace's ``systems/system.py`` after each completed agent turn.  This module
builds that callable:

- ``make_complete_fn`` wraps the existing :class:`AiClient` into a non-streaming
  ``complete_fn(messages, tools=None) -> dict`` that the workspace review loop
  uses to make its own LLM calls.
- ``load_after_turn_fn`` loads the ``System`` class from ``systems/system.py``,
  finds its ``after_turn`` method, and returns a thin wrapper that injects
  ``complete_fn`` / ``tool_executors`` according to the method's signature.

Everything is best-effort and backward compatible: a workspace without a
``System`` class or an ``after_turn`` method yields ``None`` and the framework
simply skips the hook.
"""

from __future__ import annotations

import hashlib
import inspect
import sys
import types
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
from loguru import logger

from psi_agent.session.ai_client import AiClient

# Matches ``ReviewCompleteFn`` in the workspace ``system.py``: takes messages and
# an optional tool-schema list, returns an OpenAI-style completion dict.
CompleteFn = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None], Awaitable[dict[str, Any]]]
ToolExecutors = dict[str, Callable[..., Awaitable[Any]]]
AfterTurnFn = Callable[[list[dict[str, Any]], int, list[str]], Awaitable[None]]


def make_complete_fn(ai_client: AiClient) -> CompleteFn:
    """Build a non-streaming completion function on top of ``AiClient``.

    The workspace self-evolution loop needs to make its own LLM calls with a
    tool schema and read back the assembled assistant message (content +
    tool_calls).  We reuse ``AiClient.stream`` — which already handles the
    HTTP/SSE transport — and aggregate the deltas into a single OpenAI-style
    response dict, so no transport code is duplicated here.
    """

    async def complete_fn(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        request_body: dict[str, Any] = {"messages": messages, "stream": True}
        if tools:
            request_body["tools"] = tools

        finish_reason: str | None = None
        content_parts: list[str] = []
        accumulated_tool_calls: dict[int, dict[str, Any]] = {}

        async for delta in ai_client.stream(request_body):
            if delta.finish_reason and not finish_reason:
                finish_reason = delta.finish_reason
            if delta.content:
                content_parts.append(delta.content)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.get("index", 0)
                    if idx not in accumulated_tool_calls:
                        accumulated_tool_calls[idx] = {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    acc = accumulated_tool_calls[idx]
                    if tc.get("id"):
                        acc["id"] = tc["id"]
                    func = tc.get("function", {})
                    if func.get("name"):
                        acc["function"]["name"] = func["name"]
                    if func.get("arguments"):
                        acc["function"]["arguments"] += func["arguments"]

        message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
        if accumulated_tool_calls:
            message["tool_calls"] = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]

        return {"choices": [{"index": 0, "message": message, "finish_reason": finish_reason}]}

    return complete_fn


# -- system module loading -----------------------------------------------------


def _load_system_module(workspace_path: Path) -> types.ModuleType | None:
    """Compile+exec ``workspace/systems/system.py`` into a fresh module.

    Mirrors :meth:`SystemPrompt._load_module` (compile+exec rather than
    ``importlib``) so a hot-edited ``system.py`` is always re-read and never
    served from a stale bytecode cache.
    """
    system_py = workspace_path / "systems" / "system.py"
    try:
        source = system_py.read_text(encoding="utf-8")
    except OSError:
        logger.debug(f"No system.py found at {system_py}, after-turn hook disabled")
        return None

    file_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    module_name = f"psi_after_turn_{file_hash}"
    try:
        compiled = compile(source, str(system_py), "exec")
    except Exception as e:
        logger.error(f"Failed to compile {system_py!r} for after-turn hook: {e!r}")
        return None

    module = types.ModuleType(module_name)
    module.__file__ = str(system_py)
    sys.modules[module_name] = module
    try:
        exec(compiled, module.__dict__)
    except Exception as e:
        logger.error(f"Failed to execute system module {system_py!r} for after-turn hook: {e!r}")
        sys.modules.pop(module_name, None)
        return None
    return module


def load_after_turn_fn(
    workspace_path: Path,
    *,
    ai_client: AiClient,
    tool_executors: ToolExecutors,
) -> AfterTurnFn | None:
    """Build the after-turn callable from ``workspace/systems/system.py``.

    Returns ``None`` (hook disabled) when the workspace has no ``System`` class
    or no async ``after_turn`` method — keeping behaviour unchanged for
    workspaces that don't opt into self-evolution.
    """
    module = _load_system_module(workspace_path)
    if module is None:
        return None

    system_class = getattr(module, "System", None)
    if system_class is None:
        logger.debug(f"No System class in {workspace_path / 'systems' / 'system.py'}, after-turn hook disabled")
        return None

    try:
        system = system_class(anyio.Path(str(workspace_path)))
    except Exception as e:
        logger.error(f"Failed to instantiate System for after-turn hook: {e!r}")
        return None

    method = getattr(system, "after_turn", None)
    if method is None:
        logger.debug("System.after_turn not found, after-turn hook disabled")
        return None
    if not inspect.iscoroutinefunction(method):
        logger.warning("System.after_turn is not async, after-turn hook disabled")
        return None

    complete_fn = make_complete_fn(ai_client)
    params = inspect.signature(method).parameters
    accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())

    async def after_turn(messages: list[dict[str, Any]], tool_call_count: int, called_tools: list[str]) -> None:
        kwargs: dict[str, Any] = {}
        if accepts_kwargs or "complete_fn" in params:
            kwargs["complete_fn"] = complete_fn
        if accepts_kwargs or "tool_executors" in params:
            kwargs["tool_executors"] = tool_executors
        await method(messages, tool_call_count, called_tools, **kwargs)

    return after_turn
