from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from pathlib import Path
from typing import Any

import anyio
from aiohttp import web
from loguru import logger

from psi_agent.session.after_turn import AfterTurnFn, load_after_turn_fn
from psi_agent.session.ai_client import AiClient
from psi_agent.session.channel_adapter import ChannelAdapter
from psi_agent.session.conversation import Conversation
from psi_agent.session.protocol import AgentChunk, AgentError
from psi_agent.session.schedule_registry import ScheduleRegistry
from psi_agent.session.system_prompt import SystemPrompt
from psi_agent.session.tool_registry import ToolRegistry


class SessionAgent:
    """The session runtime — conversation state, tools, schedules, and the
    lock that serialises concurrent channel requests.

    **Delegation pattern**: all state lives in four registries
    (``ToolRegistry``, ``ScheduleRegistry``, ``SystemPrompt``,
    ``Conversation``) while the agent holds only the ``AiClient``,
    ``ChannelAdapter``, ``Lock``, and ``max_tool_rounds``.

    Design principle: ``__init__`` takes already-built components.
    ``create()`` is the async factory that assembles everything from a
    workspace directory.  ``handle_request()`` owns the full request
    lifecycle: parse → lock+prepare → run → write.
    """

    def __init__(
        self,
        *,
        ai_client: AiClient,
        channel_adapter: ChannelAdapter | None = None,
        conversation: Conversation | None = None,
        tool_registry: ToolRegistry | None = None,
        schedule_registry: ScheduleRegistry | None = None,
        system_prompt: SystemPrompt | None = None,
        max_tool_rounds: int = 128,
        after_turn_fn: AfterTurnFn | None = None,
    ) -> None:
        self._ai_client = ai_client
        self._channel_adapter = channel_adapter or ChannelAdapter()
        self._conversation = conversation or Conversation()
        self._tool_registry = tool_registry or ToolRegistry()
        self._schedule_registry = schedule_registry or ScheduleRegistry()
        self._system_prompt = system_prompt or SystemPrompt()
        self._max_tool_rounds = max_tool_rounds
        self._lock = anyio.Lock()

        # -- after-turn self-evolution hook (best-effort, optional) --
        self._after_turn_fn = after_turn_fn
        self._after_turn_task_group: Any | None = None
        # Tool usage of the most recently completed turn, consumed by
        # ``spawn_after_turn_task``.
        self._last_turn_tool_count = 0
        self._last_turn_tools: list[str] = []

    # -- factory --------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        *,
        ai_socket: str,
        workspace_path: Path,
        max_tool_rounds: int = 128,
        session_id: str | None = None,
    ) -> SessionAgent:
        """Production entry point.  Loads everything from *workspace_path*."""
        ai_client = AiClient(ai_socket)
        conversation = await Conversation.from_workspace(workspace_path, session_id)
        tool_registry = await ToolRegistry.load(workspace_path / "tools", conversation.session_id)
        schedule_registry = await ScheduleRegistry.load(workspace_path / "schedules")
        system_prompt = await SystemPrompt.from_workspace(workspace_path, conversation.session_id)

        # Build the optional after-turn hook from the workspace's System class.
        # ``tool_executors`` exposes every loaded tool to the self-evolution
        # review loop by name.
        tool_executors = {name: tool_registry.get(name) for name in tool_registry.tools}
        after_turn_fn = load_after_turn_fn(
            workspace_path,
            ai_client=ai_client,
            tool_executors={n: fn for n, fn in tool_executors.items() if fn is not None},
        )

        return cls(
            ai_client=ai_client,
            conversation=conversation,
            tool_registry=tool_registry,
            schedule_registry=schedule_registry,
            system_prompt=system_prompt,
            max_tool_rounds=max_tool_rounds,
            after_turn_fn=after_turn_fn,
        )

    # -- delegation -----------------------------------------------------------

    def start_all(self, task_group: object) -> None:
        """Start schedule runners — called by ``Session.run()``."""
        self._schedule_registry.start_all(task_group, self)

    def set_pending_schedule_chunks(self, chunks: list[AgentChunk]) -> None:
        self._conversation.stash(chunks)

    # -- after-turn hook ------------------------------------------------------

    def set_after_turn_task_group(self, task_group: Any) -> None:
        """Provide the task group used to run after-turn hooks in the
        background — called by ``Session.run()``."""
        self._after_turn_task_group = task_group

    def spawn_after_turn_task(self) -> None:
        """Schedule the workspace after-turn hook for the turn that just
        completed.

        Best-effort: does nothing if no hook is configured or no task group is
        available.  The hook runs in the background and never affects the main
        response stream — all errors are swallowed and logged.
        """
        after_turn_fn = self._after_turn_fn
        if after_turn_fn is None or self._after_turn_task_group is None:
            return

        messages = list(self._conversation.messages)
        tool_count = self._last_turn_tool_count
        called_tools = list(self._last_turn_tools)

        async def _run() -> None:
            try:
                await after_turn_fn(messages, tool_count, called_tools)
            except Exception as e:
                logger.error(f"after-turn hook failed: {e!r}")

        try:
            self._after_turn_task_group.start_soon(_run)
        except Exception as e:
            logger.error(f"Failed to spawn after-turn task: {e!r}")

    async def reload_tools(self) -> dict[str, str]:
        return await self._tool_registry.refresh()

    async def reload_schedules(self) -> dict[str, str]:
        return await self._schedule_registry.refresh()

    # -- channel request lifecycle --------------------------------------------

    async def handle_request(self, request: web.Request) -> web.StreamResponse:
        """aiohttp handler registered by ``serve_session``."""
        try:
            user_message, extra_params = await self._channel_adapter.parse_request(request)
        except ChannelAdapter.ParseError as e:
            return web.json_response(
                {"error": {"message": str(e), "type": "invalid_request_error", "param": None, "code": 400}},
                status=400,
            )

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

        async with self._lock:
            try:
                await response.prepare(request)
            except Exception:
                logger.warning("Failed to prepare SSE response, client likely disconnected")
                return response

            logger.info("Acquired session lock, processing request")
            await self._channel_adapter.write(response, self.run(user_message, extra_params))

        # Fire the after-turn self-evolution hook (background, best-effort).
        self.spawn_after_turn_task()

        logger.info("Session request completed")
        return response

    # -- agent loop -----------------------------------------------------------

    async def run(
        self, user_message: dict[str, Any], extra_params: dict[str, Any] | None = None
    ) -> AsyncGenerator[AgentChunk]:
        """Run one turn of the ReAct agent loop.  Yields ``AgentChunk``.

        The conversation auto-snapshots on the first mutation; on
        failure the snapshot is restored so that memory and disk
        remain synchronised — the caller can safely retry the same
        user message.
        """
        try:
            # reload tools and schedules from workspace (incremental hash-based)
            await self._tool_registry.refresh()
            await self._schedule_registry.refresh()

            # system prompt (lazy + optional rebuild)
            await self._system_prompt.ensure(self._conversation)

            # peek pending schedule chunks — yield first, clear only after yield
            pending = self._conversation.peek_pending()
            if pending:
                logger.info(f"Yielding {len(pending)} pending schedule chunk(s)")
                for chunk in pending:
                    yield chunk
                self._conversation.clear_pending()

            self._conversation.add(user_message)
            await self._conversation.commit()
            logger.debug(f"History now has {len(self._conversation.messages)} messages")

            # Reset per-turn tool stats consumed by the after-turn hook.
            self._last_turn_tool_count = 0
            self._last_turn_tools = []

            for _round in range(self._max_tool_rounds):
                logger.debug(f"Agent loop round {_round + 1}/{self._max_tool_rounds}")

                tool_defs = [
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        },
                    }
                    for t in self._tool_registry.tools.values()
                ]

                request_body: dict[str, Any] = {
                    "messages": self._conversation.messages,
                    "tools": tool_defs,
                    "stream": True,
                }
                if extra_params:
                    extra_params.pop("messages", None)
                    extra_params.pop("tools", None)
                    extra_params.pop("stream", None)
                    request_body |= extra_params

                logger.info("Sending request to AI via AiClient")
                logger.debug(f"Request messages count: {len(self._conversation.messages)}, tools: {len(tool_defs)}")

                finish_reason: str | None = None
                accumulated_tool_calls: dict[int, dict[str, Any]] = {}
                accumulated_content: str = ""
                accumulated_reasoning: str = ""

                async with aclosing(self._ai_client.stream(request_body)) as stream:
                    async for delta in stream:
                        logger.debug(
                            f"AI delta: content={delta.content!r}, reasoning={delta.reasoning!r}, "
                            f"finish_reason={delta.finish_reason!r}, "
                            f"tools={len(delta.tool_calls) if delta.tool_calls else 0}"
                        )
                        if delta.content:
                            yield AgentChunk(content=delta.content)
                            accumulated_content += delta.content
                        if delta.reasoning:
                            yield AgentChunk(reasoning=delta.reasoning)
                            accumulated_reasoning += delta.reasoning

                        if delta.finish_reason and not finish_reason:
                            finish_reason = delta.finish_reason

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

                        if finish_reason == "error":
                            logger.warning("AI returned error, stopping without saving to history")
                            raise AgentError(accumulated_content or accumulated_reasoning or "Unknown AI error")

                        if finish_reason == "stop":
                            logger.debug("AI finished with stop")
                            logger.debug(
                                f"Stop: content={len(accumulated_content)} chars, "
                                f"reasoning={len(accumulated_reasoning)} chars"
                            )
                            if accumulated_content or accumulated_reasoning:
                                assistant_msg: dict[str, Any] = {"role": "assistant"}
                                if accumulated_content:
                                    assistant_msg["content"] = accumulated_content
                                if accumulated_reasoning:
                                    assistant_msg["reasoning"] = accumulated_reasoning
                                self._conversation.add(assistant_msg)
                            await self._conversation.commit()
                            return

                        if finish_reason == "tool_calls":
                            logger.info("AI requested tool calls, processing...")
                            ordered_calls = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]

                            assistant_msg: dict[str, Any] = {"role": "assistant", "tool_calls": ordered_calls}
                            if accumulated_content:
                                assistant_msg["content"] = accumulated_content
                            if accumulated_reasoning:
                                assistant_msg["reasoning"] = accumulated_reasoning
                            self._conversation.add(assistant_msg)

                            # pre-compute args + yield tool-call intent
                            tool_args: list[tuple[int, dict[str, Any], str, dict[str, Any]]] = []
                            for i, tc in enumerate(ordered_calls):
                                func_info = tc.get("function", {})
                                func_name = func_info.get("name", "")
                                func_args_str = func_info.get("arguments", "{}")

                                try:
                                    args = json.loads(func_args_str)
                                    if not isinstance(args, dict):
                                        logger.warning(f"Tool arguments is not a dict: {type(args).__name__}")
                                        args = {}
                                except json.JSONDecodeError, TypeError:
                                    logger.warning(f"Failed to parse tool call arguments: {func_args_str[:1000]!r}")
                                    args = {}

                                logger.info(f"Executing tool: {func_name!r}({args!r})")
                                yield AgentChunk(
                                    reasoning=f"[Tool Call: {func_name}({json.dumps(args, ensure_ascii=False)})]"
                                )
                                tool_args.append((i, tc, func_name, args))

                            # accumulate per-turn tool stats for the after-turn hook
                            self._last_turn_tool_count += len(tool_args)
                            self._last_turn_tools.extend(fn for _i, _tc, fn, _a in tool_args if fn)

                            # execute all tools concurrently
                            results: list[str] = [""] * len(ordered_calls)

                            async def _execute_one(idx: int, fn: str, a: dict[str, Any], r: list[str]) -> None:
                                func = self._tool_registry.get(fn)
                                if func is None:
                                    r[idx] = f"Error: Tool '{fn}' not found"
                                    logger.error(f"Tool not found: {fn!r}")
                                else:
                                    try:
                                        raw = await func(**a)
                                        r[idx] = str(raw)
                                        logger.info(f"Tool result ({fn!r}): {str(raw)[:1000]!r}")
                                    except Exception as e:
                                        r[idx] = f"Error executing tool '{fn}': {e}"
                                        logger.error(f"Tool execution error ({fn!r}): {e!r}")

                            async with anyio.create_task_group() as tg:
                                for i, _tc, func_name, args in tool_args:
                                    if func_name:
                                        tg.start_soon(_execute_one, i, func_name, args, results)
                                    else:
                                        results[i] = "Error: empty tool call name"

                            # yield results in order, save
                            for i, tc, func_name, _args in tool_args:
                                result = results[i]
                                yield AgentChunk(reasoning=f"[Tool Result: {str(result)[:1000]}]")
                                self._conversation.add(
                                    {
                                        "role": "tool",
                                        "tool_call_id": tc.get("id", ""),
                                        "name": func_name,
                                        "content": str(result),
                                    }
                                )
                            await self._conversation.commit()

                            break

                if finish_reason not in ("error", "stop", "tool_calls"):
                    logger.warning(
                        f"Unexpected finish_reason={finish_reason!r}, "
                        f"saving {len(accumulated_content)} chars of content and stopping"
                    )
                    if accumulated_content or accumulated_reasoning:
                        assistant_msg: dict[str, Any] = {"role": "assistant"}
                        if accumulated_content:
                            assistant_msg["content"] = accumulated_content
                        if accumulated_reasoning:
                            assistant_msg["reasoning"] = accumulated_reasoning
                        self._conversation.add(assistant_msg)
                    await self._conversation.commit()
                    return

            else:
                logger.warning(f"Reached max tool rounds ({self._max_tool_rounds}), stopping")
                self._conversation.add({"role": "assistant", "content": "[Max tool rounds reached]"})
                await self._conversation.commit()
                yield AgentChunk(content="[Max tool rounds reached]")
        finally:
            self._conversation.rollback()
