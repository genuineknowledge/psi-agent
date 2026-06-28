from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import re
import sys
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
from aiohttp import web
from loguru import logger

from psi_agent.session.ai_client import AiClient
from psi_agent.session.channel_adapter import ChannelAdapter
from psi_agent.session.protocol import AgentChunk, AgentError, ToolFunction
from psi_agent.session.scheduler import load_schedules_from_workspace, run_one_schedule
from psi_agent.session.tools import load_tools_from_workspace

if TYPE_CHECKING:
    from psi_agent.session.scheduler import Schedule


# ── SessionAgent ─────────────────────────────────────────────────────────────
# The session is the heart of the psi-agent runtime.  It owns every piece of
# state that a conversation needs — conversation history, available tools, cron
# schedules, the lock that serialises concurrent channel requests, and the two
# protocol adapters (AiClient for the AI side, ChannelAdapter for the channel
# side).
#
# Design principle: ``__init__`` is a plain constructor that accepts already-
# built components (test-friendly, no IO).  ``create()`` is the async factory
# that assembles everything from a workspace directory (production path).
# Together they avoid the "async __init__" anti-pattern.
# ──────────────────────────────────────────────────────────────────────────────

class SessionAgent:
    # -- constructor ----------------------------------------------------------
    # Takes already-constructed components.  Every parameter has a default
    # (empty dict/list/None) so that tests can inject only the subset they
    # care about.  ``create()`` (below) is the full factory.
    # ------------------------------------------------------------------------

    def __init__(
        self,
        *,
        ai_client: AiClient,
        channel_socket: str = "",
        channel_adapter: ChannelAdapter | None = None,
        tools: dict[str, ToolFunction],
        tool_funcs: dict[str, Callable[..., Any]] | None = None,
        schedules: list | None = None,
        system_prompt_builder: Callable[..., Any] | None = None,
        system_prompt_rebuild_checker: Callable[..., Any] | None = None,
        max_tool_rounds: int = 128,
        history: list[dict] | None = None,
        history_path: Path | None = None,
        agent_uuid: str = "",
    ) -> None:
        self._ai_client = ai_client
        self._channel_socket = channel_socket
        self._channel_adapter = channel_adapter if channel_adapter is not None else ChannelAdapter()
        self.tools = tools
        self._tool_funcs = tool_funcs if tool_funcs else {}
        self.schedules = schedules if schedules is not None else []
        self._system_prompt_builder = system_prompt_builder
        self._system_prompt_rebuild_checker = system_prompt_rebuild_checker
        self.max_tool_rounds = max_tool_rounds
        self.history = history if history is not None else []
        self._history_path = history_path
        self._pending_schedule_chunks: list[AgentChunk] = []
        self._lock = anyio.Lock()
        self._tg: Any = None
        self._file_hashes: dict[str, str] = {}
        self._workspace_path: Path | None = None
        self._agent_uuid = agent_uuid or uuid.uuid4().hex

    # -- factory --------------------------------------------------------------
    # Production entry point.  Generates a UUID for this agent instance (used
    # to isolate ``sys.modules`` entries when multiple agents share a process),
    # then loads tools, schedules, system prompt, and persisted history from
    # the workspace.
    #
    # ``system_prompt_rebuild_checker`` is an optional async function loaded
    # from ``systems/system.py``.  If it returns True, the system prompt is
    # rebuilt on the next ``run()`` call — this supports dynamic prompt
    # refreshing without restarting the session.
    # ------------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        *,
        ai_socket: str,
        channel_socket: str = "",
        workspace_path: Path,
        max_tool_rounds: int = 128,
        session_id: str | None = None,
    ) -> SessionAgent:
        agent_uuid = uuid.uuid4().hex
        tools, tool_funcs, file_hashes = await load_tools_from_workspace(
            workspace_path / "tools", agent_uuid
        )
        schedules = await load_schedules_from_workspace(workspace_path / "schedules")
        history, history_path = await _init_history(workspace_path, session_id)
        builder, checker = _load_system_module(workspace_path, agent_uuid)

        agent = cls(
            ai_client=AiClient(ai_socket),
            channel_socket=channel_socket,
            tools=tools,
            tool_funcs=tool_funcs,
            schedules=schedules,
            system_prompt_builder=builder,
            system_prompt_rebuild_checker=checker,
            max_tool_rounds=max_tool_rounds,
            history=history,
            history_path=history_path,
            agent_uuid=agent_uuid,
        )
        agent._file_hashes = file_hashes
        agent._workspace_path = workspace_path
        return agent

    # -- dynamic reload -------------------------------------------------------
    # Tools and schedules can be added at runtime without restarting.
    # ``set_task_group()`` must be called first so that new schedule runners
    # can be spawned inside the session's task group.
    #
    # Tools are matched by SHA-256 file hash — only changed or new files are
    # reloaded.  Schedules are de-duplicated by name; already-running schedules
    # are not restarted.
    # ------------------------------------------------------------------------

    def set_task_group(self, tg: Any) -> None:
        self._tg = tg

    async def reload_tools(self) -> dict[str, str]:
        if self._workspace_path is None:
            logger.warning("No workspace_path set, cannot reload tools")
            return {}

        new_tools, new_funcs, new_hashes = await load_tools_from_workspace(
            self._workspace_path / "tools", self._agent_uuid
        )
        result: dict[str, str] = {}
        for name, tf in new_tools.items():
            if name not in self.tools:
                self.tools[name] = tf
                self._tool_funcs[name] = new_funcs[name]
                result[name] = "added"
            elif new_hashes.get(name) != self._file_hashes.get(name):
                self.tools[name] = tf
                self._tool_funcs[name] = new_funcs[name]
                result[name] = "updated"
            else:
                result[name] = "skipped"
        self._file_hashes = new_hashes
        changed = {k: v for k, v in result.items() if v != "skipped"}
        logger.info(f"Tool reload complete: {changed or 'no changes'}")
        return result

    async def reload_schedules(self) -> list[Schedule]:
        if self._workspace_path is None:
            logger.warning("No workspace_path set, cannot reload schedules")
            return []

        new_scheds = await load_schedules_from_workspace(self._workspace_path / "schedules")
        existing = {s.name for s in self.schedules}
        added = []
        for s in new_scheds:
            if s.name not in existing:
                self.schedules.append(s)
                if self._tg is not None:
                    self._tg.start_soon(run_one_schedule, s, self)
                added.append(s)
        if added:
            logger.info(f"Schedule reload: added {[s.name for s in added]}")
        return added

    # -- system prompt --------------------------------------------------------
    # Built lazily on the first ``run()``.  Later calls consult
    # ``system_prompt_rebuild_checker`` — if the workspace defines this
    # function and it returns True, the system prompt is replaced in-place
    # (``history[0]`` is overwritten).
    # ------------------------------------------------------------------------

    async def _build_system_prompt(self) -> None:
        assert self._system_prompt_builder is not None
        try:
            sp = await self._system_prompt_builder()
            self.history.append({"role": "system", "content": sp})
            logger.info(f"System prompt loaded ({len(sp) if sp else 0} chars)")
        except Exception as e:
            logger.error(f"Failed to build system prompt: {e}")

    # -- channel request lifecycle --------------------------------------------
    # ``handle_request`` is the aiohttp handler registered by ``serve_session``.
    #
    # It owns the full request lifecycle in one place:
    #   1. parse HTTP body → ``user_message`` + ``extra_params``
    #   2. create SSE ``StreamResponse``
    #   3. acquire the session lock, ``prepare()`` the response (client sees
    #      200 only after the lock is acquired — this is deliberate: the HTTP
    #      status doubles as a fairness signal)
    #   4. run the agent loop and stream chunks through ChannelAdapter
    #   5. release the lock (``async with``)
    #
    # The lock is owned by the agent, not by the transport layer.  This keeps
    # the concurrency policy visible at a glance.
    # ------------------------------------------------------------------------

    async def handle_request(self, request: web.Request) -> web.StreamResponse:
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

    # -- schedule ↔ channel interleaving --------------------------------------
    # When a cron schedule fires, its AI response is stored as a list of
    # ``AgentChunk`` via ``set_pending_schedule_chunks()``.  The VERY NEXT
    # channel request that acquires the lock will yield these chunks first,
    # then process the user's message normally.  This gives the illusion of
    # the schedule "talking" through the channel without a separate push
    # mechanism.
    # ------------------------------------------------------------------------

    def set_pending_schedule_chunks(self, chunks: list[AgentChunk]) -> None:
        self._pending_schedule_chunks = chunks

    # -- agent loop -----------------------------------------------------------
    # ``run()`` is an async generator — it yields ``AgentChunk`` (pure semantic
    # output: content + reasoning) and consumes ``AiDelta`` from the AI backend
    # via ``AiClient.stream()``.
    #
    # The loop is a classic ReAct pattern:
    #   send (history + tools) → receive SSE stream → dispatch on finish_reason
    #
    # finish_reason  dispatch
    # ─────────────  ────────────────────────────────────────────────────────
    # stop            Save assistant reply to history, return (loop ends).
    # tool_calls      Accumulate partial tool-calls, execute them one by one,
    #                 append results to history, loop back for the next round.
    # error           Raise ``AgentError`` — no history is saved.  Caught by
    #                 ``ChannelAdapter.write()`` which turns it into an SSE
    #                 error chunk for the channel client.
    # <unrecognised>  Save whatever content we have and return.
    #
    # Tool execution is deliberately fault-tolerant: broken JSON in arguments
    # falls back to ``{}``, missing tools produce an error result, and tool
    # exceptions are caught and reported as result text.  Nothing interrupts
    # the loop.
    # ------------------------------------------------------------------------

    async def run(self, user_message: dict, extra_params: dict | None = None) -> AsyncIterator[AgentChunk]:
        # ── system prompt (lazy + optional rebuild) ─────────────────────────
        if self._system_prompt_builder is not None:
            if not self.history:
                await self._build_system_prompt()
            elif self._system_prompt_rebuild_checker is not None:
                try:
                    if await self._system_prompt_rebuild_checker():
                        logger.info("Rebuild checker returned True — rebuilding system prompt")
                        self.history[0] = {"role": "system", "content": await self._system_prompt_builder()}
                except Exception as e:
                    logger.error(f"Rebuild check or rebuild failed: {e}")

        # ── flush pending schedule chunks before user message ───────────────
        if self._pending_schedule_chunks:
            logger.info(f"Yielding {len(self._pending_schedule_chunks)} pending schedule chunk(s)")
            for chunk in self._pending_schedule_chunks:
                yield chunk
            self._pending_schedule_chunks = []

        self.history.append(user_message)
        logger.debug(f"History now has {len(self.history)} messages")

        # ── ReAct loop ──────────────────────────────────────────────────────
        for _round in range(self.max_tool_rounds):
            logger.debug(f"Agent loop round {_round + 1}/{self.max_tool_rounds}")

            tool_defs = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in self.tools.values()
            ]

            request_body: dict = {
                "messages": self.history,
                "tools": tool_defs,
                "stream": True,
            }
            if extra_params:
                request_body |= extra_params

            logger.info("Sending request to AI via AiClient")
            logger.debug(f"Request messages count: {len(self.history)}, tools: {len(tool_defs)}")

            finish_reason: str | None = None
            accumulated_tool_calls: dict[int, dict] = {}
            accumulated_content: str = ""
            accumulated_reasoning: str = ""

            # ── consume AiDelta stream ──────────────────────────────────────
            # ``AiClient.stream()`` already handled HTTP errors and SSE
            # parse failures.  We aggregate partial tool_call fragments
            # (the AI streams them over multiple chunks) and forward
            # content/reasoning as ``AgentChunk``.
            async for delta in self._ai_client.stream(request_body):
                if delta.content:
                    yield AgentChunk(content=delta.content)
                    accumulated_content += delta.content
                if delta.reasoning:
                    yield AgentChunk(reasoning=delta.reasoning)
                    accumulated_reasoning += delta.reasoning

                if delta.finish_reason and not finish_reason:
                    finish_reason = delta.finish_reason

                # Partial tool_call fragments — accumulate by index.
                # The AI may send ``id`` and ``function.name`` in earlier
                # chunks and ``function.arguments`` spread across many.
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

                # ── dispatch on finish_reason ───────────────────────────────
                if finish_reason == "error":
                    logger.warning("AI returned error, stopping without saving to history")
                    raise AgentError(accumulated_content or accumulated_reasoning or "Unknown AI error")

                if finish_reason == "stop":
                    logger.debug("AI finished with stop")
                    if accumulated_content or accumulated_reasoning:
                        assistant_msg: dict = {"role": "assistant"}
                        if accumulated_content:
                            assistant_msg["content"] = accumulated_content
                        if accumulated_reasoning:
                            assistant_msg["reasoning"] = accumulated_reasoning
                        self.history.append(assistant_msg)
                        if self._history_path is not None:
                            await _save_history(self._history_path, self.history)
                    return

                if finish_reason == "tool_calls":
                    logger.info("AI requested tool calls, processing...")

                    # Sort by index to preserve the AI's intended call order.
                    ordered_calls = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]

                    # Record the assistant's tool_calls intent in history.
                    assistant_msg: dict = {"role": "assistant", "tool_calls": ordered_calls}
                    if accumulated_content:
                        assistant_msg["content"] = accumulated_content
                    if accumulated_reasoning:
                        assistant_msg["reasoning"] = accumulated_reasoning
                    self.history.append(assistant_msg)

                    # ── execute each tool call ──────────────────────────────
                    for tc in ordered_calls:
                        func_info = tc.get("function", {})
                        func_name = func_info.get("name", "")
                        func_args_str = func_info.get("arguments", "{}")

                        # Malformed JSON → fall back to empty args.
                        try:
                            args = json.loads(func_args_str)
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(f"Failed to parse tool call arguments: {func_args_str[:200]}")
                            args = {}

                        logger.info(f"Executing tool: {func_name!r}({args!r})")

                        # Notify the channel client which tool is being called.
                        yield AgentChunk(reasoning=f"[Tool Call: {func_name}({json.dumps(args, ensure_ascii=False)})]")

                        # Look up and invoke the tool.  Missing tool or
                        # exception → produce an error result string.
                        func = self._tool_funcs.get(func_name)
                        if func is None:
                            result = f"Error: Tool '{func_name}' not found"
                            logger.error(repr(result))
                        else:
                            try:
                                result = await func(**args)
                                logger.info(f"Tool result ({func_name!r}): {str(result)[:200]!r}")
                            except Exception as e:
                                result = f"Error executing tool '{func_name}': {e}"
                                logger.error(repr(result))

                        # Truncate to 500 chars for the channel notification.
                        yield AgentChunk(reasoning=f"[Tool Result: {str(result)[:500]}]")

                        # Append the tool result to history so the AI can
                        # reference it in the next round.
                        self.history.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "name": func_name,
                                "content": str(result),
                            }
                        )

                    # After executing all tools, break to the next round.
                    break

            # The SSE stream ended without one of the expected finish reasons.
            # Save whatever content we received so the conversation isn't lost.
            if finish_reason not in ("error", "stop", "tool_calls"):
                logger.warning(
                    f"Unexpected finish_reason={finish_reason!r}, "
                    f"saving {len(accumulated_content)} chars of content and stopping"
                )
                if accumulated_content or accumulated_reasoning:
                    assistant_msg: dict = {"role": "assistant"}
                    if accumulated_content:
                        assistant_msg["content"] = accumulated_content
                    if accumulated_reasoning:
                        assistant_msg["reasoning"] = accumulated_reasoning
                    self.history.append(assistant_msg)
                return

        else:
            logger.warning(f"Reached max tool rounds ({self.max_tool_rounds}), stopping")
            yield AgentChunk(content="[Max tool rounds reached]")


# ── History persistence ──────────────────────────────────────────────────────
# History is stored as JSONL (one JSON message per line) under
# ``workspace/histories/{session_id}.jsonl``.
#
# Only saved on ``finish_reason="stop"`` — errors and intermediate tool-call
# states are never persisted.  ``session_id`` is validated against path
# traversal (only ``[a-zA-Z0-9_-]+`` allowed).
# ──────────────────────────────────────────────────────────────────────────────

async def _init_history(
    workspace_path: Path,
    session_id: str | None = None,
) -> tuple[list[dict], Path]:
    if session_id is not None and not re.fullmatch(r"[a-zA-Z0-9_-]+", session_id):
        raise ValueError(f"Invalid session_id: {session_id!r} (only alphanumeric, dash, underscore allowed)")
    session_id = session_id or uuid.uuid4().hex
    logger.info(f"Starting session: {session_id}")

    histories_dir = anyio.Path(str(workspace_path / "histories"))
    dir_created = False
    if not await histories_dir.is_dir():
        await histories_dir.mkdir(parents=True)
        logger.info(f"Created histories directory: {histories_dir}")
        dir_created = True
    if dir_created:
        await (histories_dir / ".gitignore").write_text("*\n")
        logger.debug(f"Created .gitignore in {histories_dir}")

    history_path = workspace_path / "histories" / f"{session_id}.jsonl"
    history = await _load_history(history_path)
    return history, history_path


async def _load_history(path: Path) -> list[dict]:
    history: list[dict] = []
    path_anyio = anyio.Path(str(path))
    if not await path_anyio.exists():
        logger.info(f"No history file found at {path}")
        return history

    content = await path_anyio.read_text()
    for lineno, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            history.append(json.loads(stripped))
        except json.JSONDecodeError:
            logger.warning(f"Skipping malformed line {lineno} in {path}")

    logger.info(f"History loaded from {path} ({len(history)} messages)")
    return history


async def _save_history(path: Path, history: list[dict]) -> None:
    try:
        content = "\n".join(json.dumps(msg, ensure_ascii=False) for msg in history) + "\n"
        await anyio.Path(str(path)).write_text(content)
        logger.debug(f"History saved to {path} ({len(history)} messages)")
    except Exception as e:
        logger.error(f"Failed to save history: {e}")


# ── System module loading ────────────────────────────────────────────────────
# Imports ``system_prompt_builder`` and ``system_prompt_rebuild_checker`` from
# ``workspace/systems/system.py`` using ``importlib``.
#
# The module name includes ``agent_uuid`` and a ``file_hash[:12]`` so that
# multiple agents in the same process receive isolated ``sys.modules`` entries.
# Without this, two agents sharing a workspace would collide on a hard-coded
# ``psi_workspace_system`` key and see each other's already-imported module.
# ──────────────────────────────────────────────────────────────────────────────

def _load_system_module(
    workspace_path: Path, agent_uuid: str
) -> tuple[Callable[..., Any] | None, Callable[..., Any] | None]:
    system_py = workspace_path / "systems" / "system.py"
    try:
        file_bytes = system_py.read_bytes()
    except OSError:
        logger.warning(f"No system.py found at {system_py}")
        return None, None

    file_hash = hashlib.sha256(file_bytes).hexdigest()
    module_name = f"psi_system_{agent_uuid}_{file_hash[:12]}"

    try:
        spec = importlib.util.spec_from_file_location(module_name, str(system_py))
        if spec is None or spec.loader is None:
            logger.warning(f"Could not load spec for {system_py}")
            return None, None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as e:
        logger.error(f"Failed to load {system_py}: {e}")
        sys.modules.pop(module_name, None)
        return None, None

    builder = _extract_async_func(module, "system_prompt_builder")
    checker = _extract_async_func(module, "system_prompt_rebuild_checker")
    return builder, checker


def _extract_async_func(module: object, name: str) -> Callable[..., Any] | None:
    func = getattr(module, name, None)
    if func is None or not inspect.iscoroutinefunction(func):
        return None
    return func
