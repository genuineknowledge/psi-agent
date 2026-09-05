from __future__ import annotations

import inspect
import json
import sys
import types
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import anyio
from aiohttp import web
from loguru import logger

from psi_agent._card_markers import (
    CARD_ACTION_BATCH_PATTERN,
    CARD_ACTION_TAG,
)
from psi_agent._card_markers import (
    CARD_ACTION_PATTERN as _CARD_ACTION_PATTERN,
)
from psi_agent._card_markers import (
    SILENT_REPLY as _SILENT_REPLY,
)
from psi_agent.protocol import (
    FINISH_REASON_COMPACTION_NEEDED,
    FINISH_REASON_ERROR,
    FINISH_REASON_STOP,
    FINISH_REASON_TOOL_CALLS,
    REASONING_KIND_THINKING,
    REASONING_KIND_TOOL_CALL,
    REASONING_KIND_TOOL_RESULT,
)
from psi_agent.session.ai_client import AiClient
from psi_agent.session.channel_adapter import ChannelAdapter
from psi_agent.session.conversation import Conversation
from psi_agent.session.event_protocol import EventProtocolError, parse_event_envelope
from psi_agent.session.history_display import (
    KIND_COMPACTED,
    TURN_CONTEXT_KEY,
    message_kind,
    truncate_tool_result,
    with_kind,
)
from psi_agent.session.prompt_budget import log_tool_schema_size
from psi_agent.session.protocol import (
    DEFAULT_MAX_TOOL_ROUNDS,
    MAX_ROUNDS_NOTICE,
    AgentChunk,
    AgentError,
    AgentRunResult,
    AgentRunStatus,
    AgentStopCause,
)
from psi_agent.session.request_assembly import RequestAssembler
from psi_agent.session.runtime_context import mark_workflow_touched, runtime_scope
from psi_agent.session.schedule_registry import ScheduleRegistry
from psi_agent.session.system_prompt import SystemPrompt
from psi_agent.session.tool_convergence import ToolCallConvergence
from psi_agent.session.tool_defs import ToolDefsCache, build_tool_defs
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
_WORKFLOW_LAUNCHERS = frozenset({"flow_run", "run_flow", "run_flow_resume"})


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


def _extract_card_actions(content: Any) -> list[tuple[str, dict[str, Any]]] | None:
    """Extract single card-action JSON payloads from a (possibly batched) callback message.

    Returns ``None`` when the message is not composed **entirely** of card actions
    (any residue text, or an unparseable payload, means the caller should fall
    back to the ordinary AI turn instead of guessing).
    """
    if not isinstance(content, str) or f"<{CARD_ACTION_TAG}" not in content:
        return None
    # 剥离 batch 外壳再提取:连点合并后的消息是 <feishu_card_action_batch>
    # 包裹多条 <feishu_card_action>,外壳本身不算「非回调文本」。
    inner = CARD_ACTION_BATCH_PATTERN.sub("", content)
    matches = _CARD_ACTION_PATTERN.findall(inner)
    if not matches:
        return None
    residue = _CARD_ACTION_PATTERN.sub("", inner).strip()
    if residue:
        return None
    actions: list[tuple[str, dict[str, Any]]] = []
    for raw in matches:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        actions.append((raw.strip(), payload))
    return actions


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
        self._chunks = start(self)

    @property
    def result(self) -> AgentRunResult | None:
        """Terminal result, or ``None`` if the run has not finished normally."""
        return self._result

    def _set_result(self, result: AgentRunResult) -> None:
        """Called by the agent loop at each normal exit.  Internal."""
        self._result = result

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
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
        workspace_path: Path | None = None,
        agent_path: Path | None = None,
        max_context_tokens: int = -1,
        request_assembler: RequestAssembler | None = None,
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
        # One per session: it carries the calibrated chars/token ratio and the
        # set of rows already elided, both of which must persist across turns
        # for hysteresis to mean anything.
        self._request_assembler = request_assembler or RequestAssembler(max_context_tokens=max_context_tokens)
        # ``tools`` is part of the upstream prefix-cache key, and the registry is
        # re-read every turn — so the array is frozen per Session rather than
        # rebuilt. See ``session/tool_defs.py``.
        self._tool_defs_cache = ToolDefsCache()

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
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
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

    # -- deterministic card dispatch ------------------------------------------

    async def _try_direct_card_dispatch(
        self,
        user_message: dict[str, Any],
        turn_response_kind: str,
    ) -> list[AgentChunk] | None:
        """Short-circuit deterministic Feishu card callbacks — call the handler tool
        directly, skipping the AI turn.

        Card ticks/untick are deterministic: the clicked button's ``dispatch.handler``
        already names the tool that should run. Routing them through a full AI turn
        costs a model round-trip (seconds of thinking) for zero judgement, which is
        exactly what "状态更新太慢" is. This path fires only when **every** condition
        holds — the whole message is card actions, each ``dispatch.handler`` resolves
        to a registered tool that accepts ``card_action_json`` — otherwise it returns
        ``None`` and the ordinary AI turn runs (behaviour-compatible with anything the
        model used to decide, e.g. skills as handlers).

        Records the turn in conversation history like any other turn; yields one
        silent chunk on full success (the Channel suppresses ``NO_REPLY``), or a
        visible error chunk naming the failures. The card's visual tick is applied
        by the Channel *before* this runs, so success stays quiet.
        """
        actions = _extract_card_actions(user_message.get("content", ""))
        if actions is None:
            return None

        calls: list[tuple[str, Any, str, str]] = []  # (handler, func, payload_json, operator)
        for payload_json, payload in actions:
            dispatch = payload.get("dispatch")
            if not isinstance(dispatch, dict):
                return None
            # 信任边界:只有 Channel 侧受控分发的回调(matched=true)才允许直调。
            # 任意用户都能发一条伪造的 <feishu_card_action> 消息,/chat/completions
            # 本身无鉴权——不校验 matched 等于给"以任意身份执行任意卡片工具"
            # 开一条纯文本注入面。matched 缺失/为 false 一律回落 AI 轮次。
            if dispatch.get("matched") is not True:
                return None
            handler = dispatch.get("handler")
            if not isinstance(handler, str) or not handler.strip():
                return None
            func = self._tool_registry.get(handler.strip())
            if func is None:
                return None
            if "card_action_json" not in inspect.signature(func).parameters:
                return None
            operator = payload.get("operator_open_id") or ""
            calls.append((handler.strip(), func, payload_json, operator))

        logger.info(f"Direct card dispatch: {len(calls)} action(s) -> {[c[0] for c in calls]}")

        summaries: list[str] = []
        async with self._conversation:
            self._conversation.add(with_kind(user_message, message_kind(user_message)))
            await self._conversation.commit()
            for handler, func, payload_json, operator in calls:
                kwargs: dict[str, Any] = {"card_action_json": payload_json}
                if "user_key" in inspect.signature(func).parameters:
                    kwargs["user_key"] = operator
                try:
                    result = str(await func(**kwargs))
                    summaries.append(f"{handler}: ok ({result[:200]})")
                except Exception as e:
                    summaries.append(f"{handler}: FAILED ({e!r})")
                    logger.error(f"Direct card dispatch {handler} failed: {e!r}")
            self._conversation.add(
                with_kind(
                    {"role": "assistant", "content": "[card direct] " + " | ".join(summaries)},
                    turn_response_kind,
                )
            )
            await self._conversation.commit()

        failures = [s for s in summaries if "FAILED" in s]
        if failures:
            # 异常细节只进日志(见上方的 logger.error),用户侧给短文案——
            # 裸 repr 直出对话没有信息量还难看。
            return [AgentChunk(content=f"卡片操作有 {len(failures)} 项失败,请重试或稍后再试。")]
        return [AgentChunk(content=_SILENT_REPLY)]

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

    async def _record_authored_workflows(
        self,
        paths: set[str],
        user_message: str,
    ) -> None:
        """Persist generated-only workflow sources without a model tool call."""

        if not paths or not user_message.strip() or self._agent_path is None:
            return
        module_path = self._agent_path / "skills" / "workflow" / "workflow_sample.py"
        module_file = anyio.Path(module_path)
        if not await module_file.is_file():
            return

        module_name = f"psi_workflow_sample_{id(self)}"
        try:
            source = await module_file.read_text(encoding="utf-8")
            module = types.ModuleType(module_name)
            module.__file__ = str(module_path)
            sys.modules[module_name] = module
            exec(compile(source, str(module_path), "exec"), module.__dict__)
            recorder = getattr(module, "_record_workflow_authoring", None)
            if not inspect.iscoroutinefunction(recorder):
                logger.warning("Workflow sample runtime has no async authoring recorder")
                return

            for raw_path in sorted(paths):
                normalized = raw_path.strip().replace("\\", "/")
                candidate = Path(normalized)
                if (
                    candidate.is_absolute()
                    or candidate.parts[:1] != ("flows",)
                    or candidate.suffix.lower() not in {".workflow", ".g4"}
                ):
                    continue
                try:
                    result = await recorder(
                        candidate.as_posix(),
                        ["Persist the authored workflow snapshot"],
                        user_message,
                        workflow_touched=True,
                    )
                except Exception as error:
                    logger.warning(f"Could not record generated workflow sample: {error!r}")
                else:
                    if result is not None:
                        logger.info("Recorded generated workflow authoring sample")
        except Exception as error:
            logger.warning(f"Could not load workflow sample runtime: {error!r}")
        finally:
            sys.modules.pop(module_name, None)

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
        turn_user_text = user_message.get("content", "")
        if not isinstance(turn_user_text, str):
            turn_user_text = ""
        workflow_touched: set[str] = set()
        workflow_launcher_called = False

        # Gateway embeds many Sessions in one process — bind this turn so
        # tools can read session id / workspace / agent paths via ContextVars.
        with runtime_scope(
            session_id=self._conversation.session_id,
            workspace=str(self._workspace_path) if self._workspace_path is not None else "",
            agent=str(self._agent_path) if self._agent_path is not None else "",
            user_message=turn_user_text,
            workflow_touched=workflow_touched,
        ):
            async with self._conversation:
                # Reload tools and schedules from their configured roots.
                await self._tool_registry.refresh()
                await self._schedule_registry.refresh()

                # 确定性卡片回调短路(tick/untick 等):handler 是已注册工具时
                # 直调、跳过 AI 回合与 system prompt 构建;不匹配则原样走 AI。
                direct_chunks = await self._try_direct_card_dispatch(stored_user_message, turn_response_kind)
                if direct_chunks is not None:
                    for chunk in direct_chunks:
                        yield chunk
                    # 直调回合同样要落终态(0 个模型回合):否则 handle_request
                    # 会把成功的直调记成 "completed without a terminal result
                    # (failed or abandoned)",排障日志误导。
                    _finish(AgentRunStatus.COMPLETED, AgentStopCause.MODEL_COMPLETED, None, 0)
                    return

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
                # ``hook_message`` rather than the stored row: it carries what
                # the before-turn hook attached (supervisor advice) plus the
                # trusted session identity, which is what the volatile blocks
                # key off.
                turn_context = await self._system_prompt.turn_context(hook_message)
                if turn_context:
                    stored_user_message = stored_user_message | {TURN_CONTEXT_KEY: turn_context}

                self._conversation.add(stored_user_message)
                await self._conversation.commit()
                logger.debug(f"History now has {len(self._conversation.messages)} messages")

                model_turns = 0
                # One tracker per turn, so a refusal earned by this question is
                # never inherited by the next one (``tool_convergence``).
                convergence = ToolCallConvergence()
                for _round in range(self._max_tool_rounds):
                    logger.debug(f"Agent loop round {_round + 1}/{self._max_tool_rounds}")
                    model_turns = _round + 1

                    # Frozen after the first non-empty assembly: a tool that shows
                    # up mid-Session would otherwise rewrite this array and
                    # re-prefill every cached turn behind it.
                    tool_defs = self._tool_defs_cache.freeze(build_tool_defs(self._tool_registry.tools))

                    # Logged next to the prompt breakdown, not inside it: these
                    # schemas are their own request field, so they are a
                    # per-turn fixed cost the prompt total does not include.
                    log_tool_schema_size(tool_defs, context=f"session={self._conversation.session_id}")

                    # The budget is enforced *here*, at the one place a request
                    # is assembled, rather than observed downstream after the
                    # fact: an over-budget payload can no longer be built, so it
                    # can no longer be sent. See ``request_assembly``.
                    extra: dict[str, Any] = dict(request_params) if request_params else {}
                    extra["routing"] = {"session_id": self._conversation.session_id}
                    assembled = self._request_assembler.build(
                        self._conversation.messages,
                        tool_defs,
                        extra,
                    )
                    request_body = assembled.body
                    ai_messages = assembled.body["messages"]
                    _sent_chars = assembled.chars

                    logger.info("Sending request to AI via AiClient")
                    logger.debug(f"Request messages count: {len(ai_messages)}, tools: {len(tool_defs)}")

                    finish_reason: str | None = None
                    accumulated_tool_calls: dict[int, dict[str, Any]] = {}
                    accumulated_content: str = ""
                    accumulated_reasoning: str = ""
                    _compaction_needed = False
                    _compaction_prompt_tokens = 0
                    _compaction_threshold = 0
                    _usage_prompt_tokens = 0

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

                            if delta.usage_prompt_tokens:
                                # Calibrate as soon as the number arrives, not at
                                # the end of the round: the tool-calls branch
                                # ``break``s out of this loop, so anything left
                                # for afterwards would never run on the very
                                # turns that spend the most context.
                                _usage_prompt_tokens = delta.usage_prompt_tokens
                                self._request_assembler.calibrate(_sent_chars, _usage_prompt_tokens)

                            if delta.compaction_needed:
                                _compaction_needed = True
                                _compaction_prompt_tokens = delta.prompt_tokens
                                _compaction_threshold = delta.compaction_threshold
                                # The signal carries the AI layer's own ceiling.
                                # Adopting it keeps the two layers on one number
                                # even if only one of them was configured.
                                self._request_assembler.adopt_threshold(delta.compaction_threshold)

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
                                    nonlocal workflow_launcher_called
                                    if fn in _WORKFLOW_LAUNCHERS:
                                        workflow_launcher_called = True
                                    func = self._tool_registry.get(fn)
                                    if func is None:
                                        r[idx] = f"Error: Tool '{fn}' not found"
                                        logger.error(f"Tool not found: {fn!r}")
                                    else:
                                        # ** elapsed_ms 就记在结果行上 **: 工具在一个 task
                                        # group 里**并发**执行, 所以「Executing tool」与
                                        # 「Tool result」在日志里是交错的 —— 靠配对时间戳
                                        # 反推单个工具的耗时会把别人的等待算进来, 并发度一
                                        # 高就彻底错。测量点与被测区间同在一个函数里, 这个
                                        # 数就不可能配错对。
                                        #
                                        # ``anyio.current_time`` 是单调时钟 (与 wall clock
                                        # 无关), 校时不会让耗时变成负数。
                                        started = anyio.current_time()
                                        try:
                                            token = _CURRENT_TOOL_AI_SOCKET.set(self._ai_client.ai_socket)
                                            try:
                                                raw = await func(**a)
                                            finally:
                                                _CURRENT_TOOL_AI_SOCKET.reset(token)
                                            if (
                                                fn in {"write", "edit"}
                                                and isinstance(a.get("file_path"), str)
                                                and isinstance(raw, str)
                                                and raw.lstrip().startswith("[OK]")
                                            ):
                                                mark_workflow_touched(a["file_path"])
                                            r[idx] = str(raw)
                                            elapsed_ms = int((anyio.current_time() - started) * 1000)
                                            logger.info(
                                                f"Tool result ({fn!r}) elapsed_ms={elapsed_ms}: {str(raw)[:1000]!r}"
                                            )
                                        except Exception as e:
                                            r[idx] = f"Error executing tool '{fn}': {e}"
                                            # 失败也带耗时: 「工具卡了 30 秒才超时」与「立刻
                                            # 报参数错」是两种完全不同的故障, 少了这个数就得
                                            # 靠猜。
                                            elapsed_ms = int((anyio.current_time() - started) * 1000)
                                            logger.error(
                                                f"Tool execution error ({fn!r}) elapsed_ms={elapsed_ms}: {e!r}"
                                            )

                                # Refused calls are decided *before* the task
                                # group so the tool never runs, and are tracked
                                # by index so their outcome is not fed back into
                                # the counters as if it were a real attempt.
                                executed: list[tuple[int, str, dict[str, Any]]] = []
                                async with anyio.create_task_group() as tg:
                                    for i, _tc, func_name, args, argument_error in tool_args:
                                        if not func_name:
                                            results[i] = "Error: empty tool call name"
                                        elif argument_error is not None:
                                            results[i] = argument_error
                                        elif (refusal := convergence.refusal_for(func_name, args)) is not None:
                                            # Stated, not silent: the notice is
                                            # the tool result the model reads.
                                            results[i] = refusal
                                        else:
                                            executed.append((i, func_name, args))
                                            tg.start_soon(_execute_one, i, func_name, args, results)

                                for i, func_name, args in executed:
                                    convergence.record(func_name, args, results[i])

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
                        if not workflow_launcher_called:
                            await self._record_authored_workflows(workflow_touched, turn_user_text)
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
                    logger.warning(
                        f"Reached max tool rounds ({self._max_tool_rounds}), stopping; "
                        f"stop_cause={AgentStopCause.AGENT_TURN_LIMIT}"
                    )
                    # The notice goes to the *user*, not just the log: with the
                    # ceiling at DEFAULT_MAX_TOOL_ROUNDS this branch is reachable
                    # in normal use, and the reply it terminates is by definition
                    # half-finished (the model had just asked for more tools). A
                    # bare marker here reads as a glitch; naming the cause and the
                    # round count makes the stop explicable and actionable.
                    notice = MAX_ROUNDS_NOTICE.format(rounds=self._max_tool_rounds)
                    self._conversation.add(
                        with_kind(
                            {"role": "assistant", "content": notice},
                            turn_response_kind,
                        )
                    )
                    await self._conversation.commit()
                    yield AgentChunk(content=notice)
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
        deferred to ``project_history_for_wire()``.

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
