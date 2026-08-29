from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import anyio
from aiohttp import web
from loguru import logger

from psi_agent.protocol import (
    FINISH_REASON_COMPACTION_NEEDED,
    FINISH_REASON_ERROR,
    FINISH_REASON_STOP,
    FINISH_REASON_TOOL_CALLS,
    REASONING_KIND_THINKING,
    REASONING_KIND_TOOL_CALL,
    REASONING_KIND_TOOL_RESULT,
    is_terminal_finish,
)
from psi_agent.session.ai_client import AiClient
from psi_agent.session.channel_adapter import ChannelAdapter
from psi_agent.session.conversation import Conversation
from psi_agent.session.event_protocol import EventProtocolError, parse_event_envelope
from psi_agent.session.history_display import (
    KIND_COMPACTED,
    TURN_CONTEXT_KEY,
    message_kind,
    messages_for_ai,
    truncate_tool_result,
    with_kind,
)
from psi_agent.session.protocol import (
    AgentChunk,
    AgentError,
    AgentRunResult,
    AgentRunStatus,
    AgentStopCause,
    AgentTokenUsage,
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

MIN_SUMMARY_CHARS = 200
"""Below this, a *large* history's summary is treated as a failed compaction.

A compaction that summarizes hundreds of turns cannot legitimately come back as
one line.  In a real 3660-row history, 9 of 88 summaries were exactly
``HEARTBEAT_OK`` — the model had answered the transcript instead of summarizing
it, and the result was written to the ``compacted`` row and carried forward from
then on.  Rejecting the write costs one un-compacted turn; accepting it silently
discards the conversation.

Only meaningful together with ``MIN_SOURCE_CHARS``: a short conversation has a
legitimately short summary, so the floor cannot be absolute.
"""

MIN_SOURCE_CHARS = 2000
"""How much conversation must exist before a short summary is suspicious.

Guards the length floor against firing on small histories, where "three turns in,
one sentence out" is the correct result rather than a hijack.  The field failures
were nowhere near this line — 121,830 characters of transcript reduced to 12 —
so the gap between legitimate and catastrophic is orders of magnitude, not a
close call.
"""

HIJACK_ECHO_PREFIXES = ("HEARTBEAT_OK",)
"""Canned replies that, when they *open* a summary, mean the model complied.

Matched only as a prefix, and only ever as a supplement to the length floor.
Measured against the 88 ``compacted`` rows of the field log: the floor alone
caught 19 of the 31 rows containing a hijack marker and missed 12, including a
1200-character summary whose chained ``<existing-summary>`` fence had the
poisoned ``HEARTBEAT_OK`` at its head.  Prefix-matching catches all 11 such rows.

Substring matching was measured and rejected: 20 rows contain ``[SEND:`` and the
9 longest are legitimate summaries *of* file-delivery turns.  Banning the marker
outright would have thrown those away — a summary is allowed to describe
instructions, it just must not be one.
"""

_CURRENT_TOOL_AI_SOCKET: ContextVar[str | None] = ContextVar(
    "psi_agent_current_tool_ai_socket",
    default=None,
)


RECENT_TURNS_MARKER = "\n[Recent turns]\n"
"""Separator ``compact_history`` puts between the summary and the verbatim tail.

The tail is raw conversation text, so it would mask a collapsed summary from any
length check applied to the whole return value.
"""


def _summary_looks_hijacked(summary: str, source_chars: int) -> bool:
    """Whether a compaction summary collapsed instead of summarizing.

    Catches the failure mode observed in the field: the model treated the
    transcript as the live request and answered it, so the "summary" of hundreds
    of turns came back as a single line — ``HEARTBEAT_OK``, 12 characters.

    Two signals, both measured against the field log's 88 ``compacted`` rows:
    the summary is implausibly short for how much went in, or it *opens* with a
    canned reply, which is what a compliance echo looks like even when the
    surrounding text is long.  Together they flag all 11 rows carrying a
    ``HEARTBEAT_OK`` summary with no false positive among the long legitimate
    ones.

    ``source_chars`` is how much conversation was handed to the compaction: the
    length test only applies once there is enough input that a one-liner cannot
    be the right answer.  Only the part before the verbatim recent tail is
    measured, and an *empty* summary part is legitimate — with nothing older than
    the verbatim window, ``compact_history`` returns the tail alone.

    Not a full detector.  A hijacked response that is both long and does not
    begin with a known canned reply still gets through; the point is that the
    catastrophic case — an entire conversation replaced by one line, then chained
    forward forever — cannot be written silently.
    """
    head = summary.split(RECENT_TURNS_MARKER, 1)[0].strip()
    if not head:
        return False
    if source_chars >= MIN_SOURCE_CHARS and len(head) < MIN_SUMMARY_CHARS:
        return True
    # A chained summary wraps the previous one in a fence; the echo then sits
    # just inside it rather than at character zero.
    probe = head[:120].replace("<existing-summary>", "").replace("</existing-summary>", "").strip()
    return probe.startswith(HIJACK_ECHO_PREFIXES)


def _conversation_chars(messages: list[dict[str, Any]]) -> int:
    """Rough size of what a compaction was asked to summarize."""
    return sum(len(m["content"]) for m in messages if isinstance(m.get("content"), str))


def current_tool_ai_socket() -> str | None:
    """Return the invoking Session's AI socket while a workspace tool runs."""

    return _CURRENT_TOOL_AI_SOCKET.get()


class AgentRun:
    """One in-flight agent run: an ``AgentChunk`` stream plus its terminal result.

    Async-iterable, so callers keep the familiar ``async for chunk in run``
    shape.  ``result`` is ``None`` until the stream is exhausted, then holds an
    ``AgentRunResult``.  A run that fails raises ``AgentError`` out of the
    iteration and leaves ``result`` at ``None`` — result and error are mutually
    exclusive by construction.

    Abandoning a run early (``break``, cancellation, client disconnect) also
    leaves ``result`` at ``None``: the run never reached a terminal state, and
    guessing one would be worse than saying nothing.
    """

    def __init__(self, start: Callable[[AgentRun], AsyncGenerator[AgentChunk]]) -> None:
        # The loop needs to hand its result back to *this* object, so it is
        # started with the run already in hand rather than wired up afterwards.
        self._result: AgentRunResult | None = None
        self._model_calls = 0
        self._usage_calls = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._chunks = start(self)

    @property
    def result(self) -> AgentRunResult | None:
        """Terminal result, or ``None`` if the run has not finished normally."""
        return self._result

    def _set_result(self, result: AgentRunResult) -> None:
        """Called by the agent loop at each normal exit.  Internal."""
        self._result = result

    @property
    def token_usage(self) -> AgentTokenUsage:
        """Usage observed so far, including normally completed partial runs."""

        complete = self._usage_calls == self._model_calls
        return AgentTokenUsage(
            model_calls=self._model_calls,
            input_tokens=self._input_tokens if complete else None,
            output_tokens=self._output_tokens if complete else None,
        )

    def _start_model_call(self) -> None:
        self._model_calls += 1

    def _record_model_usage(self, input_tokens: int | None, output_tokens: int | None) -> None:
        if input_tokens is None or output_tokens is None:
            return
        self._usage_calls += 1
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens

    def __aiter__(self) -> AgentRun:
        return self

    async def __anext__(self) -> AgentChunk:
        return await self._chunks.__anext__()

    async def aclose(self) -> None:
        """Close the underlying generator — lets ``aclosing(run)`` work."""
        await self._chunks.aclose()


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

    @property
    def workspace_path(self) -> Path | None:
        """This Session's workspace root, or ``None`` when it has no folder.

        Read-only accessor for ``GET /files`` (``session/server.py``): outbound
        cross-container file transfer confines every read to this root, and the
        server holds only the agent. ``None`` makes that endpoint refuse
        outright — no root, nothing safe to serve.
        """
        return self._workspace_path

    @property
    def session_id(self) -> str:
        """This Session's id — the identity ``session.live_agent`` registers under.

        Read-only accessor for ``serve_session``: the id lives on the Conversation,
        and out-of-band resumes address an agent by the same id a tool reads from
        ``runtime_context.get_session_id()``.
        """
        return self._conversation.session_id

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
            run = self.run_streamed(user_message, extra_params)
            await self._channel_adapter.write(response, run)

        # SSE shape is unchanged (see ChannelAdapter.write); the result is
        # diagnostics only — it tells the log whether the turn actually finished.
        result = run.result
        if result is None:
            logger.info("Session request completed without a terminal result (failed or abandoned)")
        elif result.is_complete:
            logger.info(f"Session request completed ({result.stop_cause}, model_turns={result.model_turns})")
        else:
            logger.warning(
                f"Session request incomplete: stop_cause={result.stop_cause}, "
                f"model_finish_reason={result.model_finish_reason!r}, model_turns={result.model_turns}"
            )
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
            with runtime_scope(
                session_id=self._conversation.session_id,
                workspace=str(self._workspace_path) if self._workspace_path is not None else "",
                agent=str(self._agent_path) if self._agent_path is not None else "",
            ):
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

    def run_streamed(
        self,
        user_message: dict[str, Any],
        extra_params: dict[str, Any] | None = None,
        *,
        response_kind: str | None = None,
    ) -> AgentRun:
        """Run one turn and return an ``AgentRun`` — chunk stream + terminal result.

        Preferred entry point over ``run()``: iterate it exactly the same way,
        then read ``run.result`` afterwards to learn *how* the turn ended
        (complete answer, stopped short, turn limit, no finish reason).
        Execution failure still raises ``AgentError`` out of the iteration.

        Layered *on top of* ``run()`` rather than beside it: ``run()`` stays the
        single implementation of the loop, so a subclass that overrides it keeps
        taking effect here.  An override that ignores ``_result_sink`` simply
        leaves ``result`` at ``None`` — which is already what "never reported a
        terminal state" means.
        """
        return AgentRun(
            lambda sink: self.run(user_message, extra_params, response_kind=response_kind, _result_sink=sink)
        )

    async def run(
        self,
        user_message: dict[str, Any],
        extra_params: dict[str, Any] | None = None,
        *,
        response_kind: str | None = None,
        _result_sink: AgentRun | None = None,
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

        ``_result_sink`` is filled in by ``run_streamed()``; direct callers of
        ``run()`` ignore it and just get the chunk stream as before.
        """

        def _finish(
            status: AgentRunStatus,
            stop_cause: AgentStopCause,
            model_finish_reason: str | None,
            model_turns: int,
        ) -> None:
            if _result_sink is not None:
                _result_sink._set_result(
                    AgentRunResult(
                        status=status,
                        stop_cause=stop_cause,
                        model_finish_reason=model_finish_reason,
                        model_turns=model_turns,
                        token_usage=_result_sink.token_usage,
                    )
                )

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

                model_turns = 0
                for _round in range(self._max_tool_rounds):
                    logger.debug(f"Agent loop round {_round + 1}/{self._max_tool_rounds}")
                    model_turns = _round + 1
                    if _result_sink is not None:
                        _result_sink._start_model_call()

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
                    _input_tokens: int | None = None
                    _output_tokens: int | None = None

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

                            if delta.input_tokens is not None and delta.output_tokens is not None:
                                _input_tokens = delta.input_tokens
                                _output_tokens = delta.output_tokens

                            if is_terminal_finish(delta.finish_reason) and not finish_reason:
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

                            if finish_reason == FINISH_REASON_ERROR:
                                logger.warning("AI returned error, stopping without saving to history")
                                raise AgentError(accumulated_content or accumulated_reasoning or "Unknown AI error")

                            if finish_reason == FINISH_REASON_TOOL_CALLS:
                                logger.info("AI requested tool calls, processing...")
                                ordered_calls = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]

                                assistant_msg: dict[str, Any] = {"role": "assistant"}
                                if accumulated_content:
                                    assistant_msg["content"] = accumulated_content
                                if ordered_calls:
                                    assistant_msg["tool_calls"] = ordered_calls
                                if accumulated_reasoning:
                                    assistant_msg["reasoning"] = accumulated_reasoning
                                if accumulated_content or ordered_calls:
                                    self._conversation.add(with_kind(assistant_msg, turn_response_kind))

                                # pre-compute args + yield tool-call intent
                                tool_args: list[tuple[int, dict[str, Any], str, dict[str, Any], str | None]] = []
                                for i, tc in enumerate(ordered_calls):
                                    func_info = tc.get("function", {})
                                    func_name = func_info.get("name", "")
                                    func_args_str = func_info.get("arguments", "{}")
                                    argument_error: str | None = None

                                    try:
                                        args = json.loads(func_args_str)
                                        if not isinstance(args, dict):
                                            logger.warning(f"Tool arguments is not a dict: {type(args).__name__}")
                                            argument_error = (
                                                f"Error: Tool '{func_name}' arguments must be a JSON object"
                                            )
                                            args = {}
                                    except json.JSONDecodeError, TypeError:
                                        logger.warning(f"Failed to parse tool call arguments: {func_args_str[:1000]!r}")
                                        argument_error = f"Error: Tool '{func_name}' arguments must be valid JSON"
                                        args = {}

                                    logger.info(f"Executing tool: {func_name!r}({args!r})")
                                    yield AgentChunk(
                                        reasoning=(f"[Tool Call: {func_name}({json.dumps(args, ensure_ascii=False)})]"),
                                        kind=REASONING_KIND_TOOL_CALL,
                                    )
                                    tool_args.append((i, tc, func_name, args, argument_error))

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
                                    for i, _tc, func_name, args, argument_error in tool_args:
                                        if not func_name:
                                            results[i] = "Error: empty tool call name"
                                        elif argument_error is not None:
                                            results[i] = argument_error
                                        else:
                                            tg.start_soon(_execute_one, i, func_name, args, results)

                                # yield results in order, save
                                for i, tc, func_name, _args, _argument_error in tool_args:
                                    result = results[i]
                                    yield AgentChunk(
                                        reasoning=f"[Tool Result: {str(result)[:1000]}]",
                                        kind=REASONING_KIND_TOOL_RESULT,
                                    )
                                    raw_result = str(result)
                                    stored_result = truncate_tool_result(raw_result)
                                    if len(stored_result) != len(raw_result):
                                        logger.warning(
                                            f"Tool result truncated ({func_name!r}): "
                                            f"{len(raw_result)} -> {len(stored_result)} chars"
                                        )
                                    self._conversation.add(
                                        with_kind(
                                            {
                                                "role": "tool",
                                                "tool_call_id": tc.get("id", ""),
                                                "name": func_name,
                                                "content": stored_result,
                                            },
                                            turn_response_kind,
                                        )
                                    )
                                await self._conversation.commit()

                                break

                    if _result_sink is not None:
                        _result_sink._record_model_usage(_input_tokens, _output_tokens)

                    if finish_reason == FINISH_REASON_STOP:
                        logger.debug("AI finished with stop")
                        logger.debug(
                            f"Stop: content={len(accumulated_content)} chars, "
                            f"reasoning={len(accumulated_reasoning)} chars"
                        )
                        assistant_msg: dict[str, Any] = {"role": "assistant"}
                        if accumulated_content:
                            assistant_msg["content"] = accumulated_content
                        if accumulated_reasoning:
                            assistant_msg["reasoning"] = accumulated_reasoning
                        if accumulated_content:
                            self._conversation.add(with_kind(assistant_msg, turn_response_kind))
                        await self._conversation.commit()
                        await self._system_prompt.run_after_turn(hook_message, assistant_msg)
                        await self._schedule_registry.refresh()
                        if _compaction_needed:
                            await self._maybe_compact(_compaction_prompt_tokens, _compaction_threshold)
                        _finish(
                            AgentRunStatus.COMPLETED,
                            AgentStopCause.MODEL_COMPLETED,
                            finish_reason,
                            model_turns,
                        )
                        return

                    if finish_reason not in (
                        FINISH_REASON_ERROR,
                        FINISH_REASON_STOP,
                        FINISH_REASON_TOOL_CALLS,
                        FINISH_REASON_COMPACTION_NEEDED,
                    ):
                        logger.warning(
                            f"Unexpected finish_reason={finish_reason!r}, "
                            f"saving {len(accumulated_content)} chars of content and stopping"
                        )
                        if accumulated_content:
                            assistant_msg: dict[str, Any] = {"role": "assistant"}
                            assistant_msg["content"] = accumulated_content
                            if accumulated_reasoning:
                                assistant_msg["reasoning"] = accumulated_reasoning
                            self._conversation.add(with_kind(assistant_msg, turn_response_kind))
                        await self._conversation.commit()
                        # No finish reason at all is a broken stream, not a model
                        # decision — keep the two apart so triage can tell "the
                        # model stopped early" from "we never heard why".
                        _finish(
                            AgentRunStatus.INCOMPLETE,
                            AgentStopCause.MODEL_STOPPED
                            if finish_reason is not None
                            else AgentStopCause.INVALID_MODEL_STREAM,
                            finish_reason,
                            model_turns,
                        )
                        return

                else:
                    logger.warning(f"Reached max tool rounds ({self._max_tool_rounds}), stopping")
                    self._conversation.add(
                        with_kind(
                            {"role": "assistant", "content": "[Max tool rounds reached]"},
                            turn_response_kind,
                        )
                    )
                    await self._conversation.commit()
                    yield AgentChunk(content="[Max tool rounds reached]")
                    # Loop ran out of rounds; the last model turn asked for yet
                    # more tools, so its finish reason is typically "tool_calls".
                    _finish(
                        AgentRunStatus.INCOMPLETE,
                        AgentStopCause.AGENT_TURN_LIMIT,
                        finish_reason,
                        model_turns,
                    )

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
                    if delta.finish_reason == FINISH_REASON_ERROR:
                        raise AgentError(delta.content or "Compaction AI call failed")
            return "".join(parts)

        try:
            summary = await compaction_fn(self._conversation.messages, complete_fn)
            if not summary:
                logger.debug("Compaction returned empty summary, skipping")
                return
            source_chars = _conversation_chars(self._conversation.messages)
            if _summary_looks_hijacked(summary, source_chars):
                logger.warning(f"Compaction summary looks hijacked ({len(summary)} chars), retrying once")
                summary = await compaction_fn(self._conversation.messages, complete_fn)
                if not summary or _summary_looks_hijacked(summary, source_chars):
                    # Writing it would replace the whole conversation with the
                    # model's answer to the transcript, permanently.  Skipping
                    # leaves history un-compacted, which the next turn retries.
                    logger.error("Compaction summary still looks hijacked after retry, not writing it")
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
        if grown < 0:
            # The watermark is the *pre*-compaction count, so a successful
            # compaction guarantees the next turn reports fewer tokens and
            # ``grown`` goes negative — permanently, since it can then never
            # reach ``required`` again.  Measured on the live deployment: 24 of
            # 25 cooldown rejections in 18 hours were negative growth, i.e. the
            # gate was refusing to compact *because the last compaction had
            # worked*.  Shrinkage is evidence the mechanism works, and the
            # signal only fires while ``prompt_tokens`` is still over the
            # threshold, so this is exactly when compaction should run.
            logger.info(
                f"Compaction allowed: prompt_tokens fell {-grown} below the last "
                f"compaction's watermark, so the previous pass did shrink the context."
            )
            return True
        logger.info(
            f"Compaction skipped by cooldown: prompt_tokens grew {grown} since last "
            f"compaction (need {required}; threshold={threshold}). The system prompt "
            f"likely dominates the budget, so re-summarizing would not shrink it."
        )
        return False
