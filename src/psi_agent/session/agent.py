from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import anyio
from aiohttp import web
from loguru import logger

from psi_agent.session.ai_client import AiClient
from psi_agent.session.channel_adapter import ChannelAdapter
from psi_agent.session.conversation import Conversation
from psi_agent.session.event_protocol import EventProtocolError, parse_event_envelope
from psi_agent.session.history_display import (
    KIND_COMPACTED,
    TURN_CONTEXT_KEY,
    message_kind,
    messages_for_ai,
    with_kind,
)
from psi_agent.session.protocol import (
    REASONING_KIND_THINKING,
    REASONING_KIND_TOOL_CALL,
    REASONING_KIND_TOOL_RESULT,
    AgentChunk,
    AgentError,
    AgentRunOutcome,
)
from psi_agent.session.runtime_context import runtime_scope
from psi_agent.session.schedule_registry import ScheduleRegistry
from psi_agent.session.system_prompt import SystemPrompt
from psi_agent.session.tool_registry import ToolRegistry
from psi_agent.session.trigger_registry import TriggerRegistry

COMPACTION_COOLDOWN_FRACTION = 0.1
"""Share of the threshold that must accrue before compaction may run again.

Guards against back-to-back compactions when the system prompt itself is a large
fraction of the threshold — in that regime the signal re-fires every turn but
compaction cannot shrink the system prompt, so each pass costs an LLM call and
erodes older context without lowering ``prompt_tokens``.
"""

_CURRENT_TOOL_AI_SOCKET: ContextVar[str | None] = ContextVar(
    "psi_agent_current_tool_ai_socket",
    default=None,
)


def current_tool_ai_socket() -> str | None:
    """Return the invoking Session's AI socket while a workspace tool runs."""

    return _CURRENT_TOOL_AI_SOCKET.get()


class SessionAgent:
    """The session runtime — conversation state, tools, schedules, and the
    lock that serialises concurrent channel requests.

    **Delegation pattern**: all state lives in four registries
    (``ToolRegistry``, ``ScheduleRegistry``, ``SystemPrompt``,
    ``Conversation``) while the agent holds only the ``AiClient``,
    ``ChannelAdapter``, ``Lock``, and ``max_tool_rounds``.

    Design principle: ``__init__`` takes already-built components.
    ``create()`` is the async factory that assembles everything from a
    workspace directory (and optional agent package).  ``handle_request()``
    owns the full request lifecycle: parse → lock+prepare → run → write.
    """

    def __init__(
        self,
        *,
        ai_client: AiClient,
        channel_adapter: ChannelAdapter | None = None,
        conversation: Conversation | None = None,
        tool_registry: ToolRegistry | None = None,
        schedule_registry: ScheduleRegistry | None = None,
        trigger_registry: TriggerRegistry | None = None,
        system_prompt: SystemPrompt | None = None,
        max_tool_rounds: int = 128,
        workspace_path: Path | None = None,
        agent_path: Path | None = None,
    ) -> None:
        self._ai_client = ai_client
        self._channel_adapter = channel_adapter or ChannelAdapter()
        self._conversation = conversation or Conversation()
        self._tool_registry = tool_registry or ToolRegistry()
        self._schedule_registry = schedule_registry or ScheduleRegistry()
        self._trigger_registry = trigger_registry or TriggerRegistry()
        self._system_prompt = system_prompt or SystemPrompt()
        self._max_tool_rounds = max_tool_rounds
        self._lock = anyio.Lock()
        self._workspace_path = workspace_path
        self._agent_path = agent_path
        self._tokens_at_last_compaction: int | None = None

    # -- factory --------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        *,
        ai_socket: str,
        workspace_path: Path,
        max_tool_rounds: int = 128,
        session_id: str | None = None,
        agent_path: Path | None = None,
        appdata_root: str = "",
        active_schedules: set[str] | None = None,
        deactive_schedules: set[str] | None = None,
    ) -> SessionAgent:
        """Production entry point.

        *workspace_path* is the user open-folder (relative file tools) and owns
        **schedules** (``schedules/``).
        *agent_path* loads tools / system / **triggers** (``triggers/``); when omitted, falls
        back to *workspace_path* (single-root compatibility).
        *appdata_root* holds history JSONL (Step 4C); empty → resolve via
        ``PSI_APPDATA`` / platformdirs.

        *active_schedules* / *deactive_schedules* decide, per entry, which
        schedules under ``{workspace}/schedules`` this Session fires: a whitelist
        of ``None`` / empty fires none (the default for user Sessions),
        ``{ACTIVATE_ALL}`` fires all, a named set fires only those ``name`` s;
        the blacklist wins and subtracts the ones assigned elsewhere.
        **Activation is a property of (session x schedule)** — two Sessions on
        the same workspace may activate disjoint subsets, and non-activated
        entries are still loaded into the registry (readable, refreshable), they
        just get no runner. 刻意为之: Feishu spawns one Session per ``open_id``,
        so a schedule must be activated by exactly one Session or the reminder
        gets multiplied by the number of live sessions; the Gateway's
        ``SchedulerManager`` keeps exactly one fully activated (``ACTIVATE_ALL``)
        scheduler Session per workspace. Only the wildcard plus a blacklist (not
        an enumerated whitelist) fires ``TASK.md`` files created later on.
        """
        agent_root = agent_path if agent_path is not None else workspace_path

        ai_client = AiClient(ai_socket)
        conversation = await Conversation.from_workspace(
            workspace_path,
            session_id,
            appdata_root=appdata_root,
        )
        tool_registry = await ToolRegistry.load(agent_root / "tools", conversation.session_id)
        schedule_registry = await ScheduleRegistry.load(
            workspace_path / "schedules",
            active_names=active_schedules,
            deactive_names=deactive_schedules,
        )
        trigger_registry = await TriggerRegistry.load(agent_root / "triggers")
        system_prompt = await SystemPrompt.from_workspace(agent_root, conversation.session_id)

        return cls(
            ai_client=ai_client,
            conversation=conversation,
            tool_registry=tool_registry,
            schedule_registry=schedule_registry,
            trigger_registry=trigger_registry,
            system_prompt=system_prompt,
            max_tool_rounds=max_tool_rounds,
            workspace_path=workspace_path,
            agent_path=agent_root,
        )

    # -- delegation -----------------------------------------------------------

    def start_all(self, task_group: object) -> None:
        """Start schedule runners — called by ``Session.run()``.

        Starts runners only for schedules **activated in this Session**;
        non-activated entries stay readable in the registry (see
        *active_schedules* on ``SessionAgent.create``).
        """
        self._schedule_registry.start_all(task_group, self)

    def set_pending_schedule_chunks(self, chunks: list[AgentChunk]) -> None:
        self._conversation.stash(chunks)

    async def reload_tools(self) -> dict[str, str]:
        return await self._tool_registry.refresh()

    async def reload_schedules(self) -> dict[str, str]:
        return await self._schedule_registry.refresh()

    async def reload_triggers(self) -> dict[str, str]:
        return await self._trigger_registry.refresh()

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

        logger.info("Session request completed")
        return response

    async def handle_event(self, request: web.Request) -> web.Response:
        """aiohttp handler for ``POST /events`` (Channel → Session envelopes)."""
        try:
            body = await request.json()
        except Exception as e:
            return web.json_response({"error": f"invalid JSON: {e}"}, status=400)
        try:
            envelope = parse_event_envelope(body)
        except EventProtocolError as e:
            logger.warning(f"POST /events rejected: {e}")
            return web.json_response({"error": str(e)}, status=400)

        async with self._lock:
            matched = self._trigger_registry.match(envelope)
            fired = await self._trigger_registry.dispatch(envelope, self)

        logger.info(f"POST /events ok event={envelope.event!r} matched={len(matched)} fired={fired!r}")
        return web.json_response(
            {
                "ok": True,
                "event": envelope.event,
                "matched": len(matched),
                "fired": fired,
            }
        )

    # -- agent loop -----------------------------------------------------------

    async def run(
        self,
        user_message: dict[str, Any],
        extra_params: dict[str, Any] | None = None,
        *,
        response_kind: str | None = None,
        outcome: AgentRunOutcome | None = None,
    ) -> AsyncGenerator[AgentChunk]:
        """Run one turn of the ReAct agent loop.  Yields ``AgentChunk``.

        The conversation auto-snapshots on the first mutation; on
        failure the snapshot is restored so that memory and disk
        remain synchronised — the caller can safely retry the same
        user message.

        ``response_kind`` stamps assistant/tool rows for this turn
        (schedule runners pass ``schedule.display`` / ``schedule.silent``).
        When omitted, assistant/tool rows inherit the user message's ``kind``
        (Channel turns default to ``chat``).
        """
        if outcome is not None:
            outcome.termination_reason = None
        request_params = dict(extra_params or {})
        hook_message = dict(user_message)
        hook_message |= request_params
        # Hooks must see the trusted Conversation identity. Request extras still
        # pass through to the AI, but cannot impersonate another Session here.
        hook_message["session_id"] = self._conversation.session_id

        user_kind = message_kind(user_message)
        turn_response_kind = response_kind if response_kind is not None else user_kind
        stored_user_message = with_kind(user_message, user_kind)

        # Gateway embeds many Sessions in one process — bind this turn so
        # tools can read session id / workspace / agent paths via ContextVars.
        with runtime_scope(
            session_id=self._conversation.session_id,
            workspace=str(self._workspace_path) if self._workspace_path is not None else "",
            agent=str(self._agent_path) if self._agent_path is not None else "",
        ):
            async with self._conversation:
                # Reload tools and schedules from their configured roots.
                await self._tool_registry.refresh()
                await self._schedule_registry.refresh()

                if not turn_response_kind.startswith("schedule."):
                    hook_message |= await self._system_prompt.run_before_turn(hook_message)

                # system prompt (lazy + optional rebuild)
                await self._system_prompt.ensure(self._conversation, hook_message)

                # peek pending schedule chunks — yield first, clear only after yield
                # (only schedule.display results are stashed; silent never enters pending)
                pending = self._conversation.peek_pending()
                if pending:
                    logger.info(f"Yielding {len(pending)} pending schedule chunk(s)")
                    for chunk in pending:
                        yield chunk
                    self._conversation.clear_pending()

                # Volatile context (wall-clock time, runtime info) rides on this
                # turn's user message instead of the prompt, so the per-turn
                # change lands at the request tail and leaves the prefix —
                # prompt plus every earlier turn — byte-identical.
                turn_context = await self._system_prompt.turn_context()
                if turn_context:
                    stored_user_message = stored_user_message | {TURN_CONTEXT_KEY: turn_context}

                self._conversation.add(stored_user_message)
                await self._conversation.commit()
                logger.debug(f"History now has {len(self._conversation.messages)} messages")

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

                    ai_messages = messages_for_ai(self._conversation.messages)
                    request_body: dict[str, Any] = {
                        "messages": ai_messages,
                        "tools": tool_defs,
                        "stream": True,
                    }
                    if request_params:
                        request_params.pop("messages", None)
                        request_params.pop("tools", None)
                        request_params.pop("stream", None)
                        request_body |= request_params
                    request_body["routing"] = {"session_id": self._conversation.session_id}

                    logger.info("Sending request to AI via AiClient")
                    logger.debug(f"Request messages count: {len(ai_messages)}, tools: {len(tool_defs)}")

                    finish_reason: str | None = None
                    accumulated_tool_calls: dict[int, dict[str, Any]] = {}
                    accumulated_content: str = ""
                    accumulated_reasoning: str = ""
                    _compaction_needed = False
                    _compaction_prompt_tokens = 0
                    _compaction_threshold = 0

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
                                # Compressed process slot: model thinking stays in
                                # ``reasoning``; tag provenance for Channel/SPA filter.
                                r_kind = delta.kind or REASONING_KIND_THINKING
                                yield AgentChunk(reasoning=delta.reasoning, kind=r_kind)
                                accumulated_reasoning += delta.reasoning

                            if delta.compaction_needed:
                                _compaction_needed = True
                                _compaction_prompt_tokens = delta.prompt_tokens
                                _compaction_threshold = delta.compaction_threshold

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
                                if outcome is not None:
                                    outcome.termination_reason = finish_reason
                                logger.warning("AI returned error, stopping without saving to history")
                                raise AgentError(accumulated_content or accumulated_reasoning or "Unknown AI error")

                            if finish_reason == "tool_calls":
                                logger.info("AI requested tool calls, processing...")
                                ordered_calls = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]

                                assistant_msg: dict[str, Any] = {"role": "assistant", "tool_calls": ordered_calls}
                                if accumulated_content:
                                    assistant_msg["content"] = accumulated_content
                                if accumulated_reasoning:
                                    assistant_msg["reasoning"] = accumulated_reasoning
                                self._conversation.add(with_kind(assistant_msg, turn_response_kind))

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
                                        reasoning=(f"[Tool Call: {func_name}({json.dumps(args, ensure_ascii=False)})]"),
                                        kind=REASONING_KIND_TOOL_CALL,
                                    )
                                    tool_args.append((i, tc, func_name, args))

                                # execute all tools concurrently
                                results: list[str] = [""] * len(ordered_calls)

                                async def _execute_one(idx: int, fn: str, a: dict[str, Any], r: list[str]) -> None:
                                    func = self._tool_registry.get(fn)
                                    if func is None:
                                        r[idx] = f"Error: Tool '{fn}' not found"
                                        logger.error(f"Tool not found: {fn!r}")
                                    else:
                                        try:
                                            token = _CURRENT_TOOL_AI_SOCKET.set(self._ai_client.ai_socket)
                                            try:
                                                raw = await func(**a)
                                            finally:
                                                _CURRENT_TOOL_AI_SOCKET.reset(token)
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
                                    yield AgentChunk(
                                        reasoning=f"[Tool Result: {str(result)[:1000]}]",
                                        kind=REASONING_KIND_TOOL_RESULT,
                                    )
                                    self._conversation.add(
                                        with_kind(
                                            {
                                                "role": "tool",
                                                "tool_call_id": tc.get("id", ""),
                                                "name": func_name,
                                                "content": str(result),
                                            },
                                            turn_response_kind,
                                        )
                                    )
                                await self._conversation.commit()

                                break

                    if finish_reason == "stop":
                        if outcome is not None:
                            outcome.termination_reason = finish_reason
                        logger.debug("AI finished with stop")
                        logger.debug(
                            f"Stop: content={len(accumulated_content)} chars, "
                            f"reasoning={len(accumulated_reasoning)} chars"
                        )
                        assistant_msg: dict[str, Any] = {"role": "assistant"}
                        if accumulated_content or accumulated_reasoning:
                            if accumulated_content:
                                assistant_msg["content"] = accumulated_content
                            if accumulated_reasoning:
                                assistant_msg["reasoning"] = accumulated_reasoning
                            self._conversation.add(with_kind(assistant_msg, turn_response_kind))
                        await self._conversation.commit()
                        await self._system_prompt.run_after_turn(hook_message, assistant_msg)
                        await self._schedule_registry.refresh()
                        if _compaction_needed:
                            await self._maybe_compact(_compaction_prompt_tokens, _compaction_threshold)
                        return

                    if finish_reason not in ("error", "stop", "tool_calls", "compaction_needed"):
                        if outcome is not None:
                            outcome.termination_reason = finish_reason or "missing_finish_reason"
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
                            self._conversation.add(with_kind(assistant_msg, turn_response_kind))
                        await self._conversation.commit()
                        return

                else:
                    if outcome is not None:
                        outcome.termination_reason = "max_tool_rounds"
                    logger.warning(f"Reached max tool rounds ({self._max_tool_rounds}), stopping")
                    self._conversation.add(
                        with_kind(
                            {"role": "assistant", "content": "[Max tool rounds reached]"},
                            turn_response_kind,
                        )
                    )
                    await self._conversation.commit()
                    yield AgentChunk(content="[Max tool rounds reached]")

    async def _maybe_compact(self, prompt_tokens: int = 0, threshold: int = 0) -> None:
        """Invoke compact_history from system.py, insert compaction message
        into conversation.  system prompt merge + old-message trimming is
        deferred to ``messages_for_ai()``.

        A cooldown guards against back-to-back compactions: the signal only says
        "prompt_tokens exceeded the threshold", and compaction cannot shrink the
        system prompt itself.  When the system prompt alone is a large fraction of
        the threshold, every subsequent turn re-raises the signal, so without this
        gate the session would re-summarize constantly — each pass paying an LLM
        call and eroding older context.
        """
        compaction_fn = self._system_prompt.compaction_fn
        if compaction_fn is None:
            logger.warning("No compact_history function in system.py, skipping compaction")
            return

        if not self._compaction_cooldown_elapsed(prompt_tokens, threshold):
            return

        async def complete_fn(messages: list[dict[str, Any]]) -> str:
            body: dict[str, Any] = {"messages": messages, "stream": True}
            parts: list[str] = []
            async with aclosing(self._ai_client.stream(body)) as stream:
                async for delta in stream:
                    if delta.content:
                        parts.append(delta.content)
                    if delta.finish_reason == "error":
                        raise AgentError(delta.content or "Compaction AI call failed")
            return "".join(parts)

        try:
            summary = await compaction_fn(self._conversation.messages, complete_fn)
            if not summary:
                logger.debug("Compaction returned empty summary, skipping")
                return
            logger.info(f"Compaction summary generated ({len(summary)} chars)")

            self._conversation.add({"role": "compacted", "content": summary, "kind": KIND_COMPACTED})
            await self._conversation.commit()
            # Watermark only on success: a failed compaction did not shrink
            # anything, so the next signal should still be allowed through.
            self._tokens_at_last_compaction = prompt_tokens or None
            logger.info("Compaction completed")
        except Exception as e:
            logger.error(f"Compaction failed: {e!r}")

    def _compaction_cooldown_elapsed(self, prompt_tokens: int, threshold: int) -> bool:
        """Whether enough new context accrued since the last compaction.

        Requires growth of at least ``COMPACTION_COOLDOWN_FRACTION`` of the
        threshold.  Measured in upstream-reported ``prompt_tokens`` rather than
        message count, because a single tool result can be tens of thousands of
        tokens while two chat messages are a few hundred — a count-based gate
        would be meaningless for tool-heavy turns.

        Fails open: when the signal carries no usable numbers (older AI layer,
        malformed field) compaction proceeds as before.
        """
        last = self._tokens_at_last_compaction
        if last is None or prompt_tokens <= 0 or threshold <= 0:
            return True

        required = int(threshold * COMPACTION_COOLDOWN_FRACTION)
        grown = prompt_tokens - last
        if grown >= required:
            return True
        logger.info(
            f"Compaction skipped by cooldown: prompt_tokens grew {grown} since last "
            f"compaction (need {required}; threshold={threshold}). The system prompt "
            f"likely dominates the budget, so re-summarizing would not shrink it."
        )
        return False
