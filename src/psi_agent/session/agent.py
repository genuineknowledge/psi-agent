from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import aiohttp
import anyio
from aiohttp import web
from loguru import logger

from psi_agent._socket import resolve_connector_and_endpoint
from psi_agent.session.protocol import ChatCompletionChunk, DeltaMessage, StreamChoice, ToolFunction
from psi_agent.session.scheduler import load_schedules_from_workspace
from psi_agent.session.tools import load_tools_from_workspace


class SessionAgent:
    def __init__(
        self,
        *,
        ai_socket: str,
        tools: dict[str, ToolFunction],
        tool_funcs: dict[str, Callable[..., Any]] | None = None,
        schedules: list | None = None,
        system_prompt_builder: Callable[..., Any] | None = None,
        max_tool_rounds: int = 128,
        history: list[dict] | None = None,
        history_path: Path | None = None,
    ) -> None:
        """Initialize an agent backed by an AI server.

        ``ai_socket`` is a Unix socket path, ``http(s)://`` URL, or Windows
        named pipe for the AI backend.  ``tools`` provides the metadata
        (name + JSON Schema) sent to the AI.

        ``tool_funcs``, ``schedules`` and ``system_prompt_builder`` are
        loaded by ``create()`` from a workspace.  ``history`` and
        ``history_path`` are loaded from / written to for session persistence.

        The plain ``__init__`` is test-friendly — all workspace-derived
        fields default to empty/None.
        """
        self.ai_socket = ai_socket
        self.tools = tools
        self._tool_funcs = tool_funcs if tool_funcs else {}
        self.schedules = schedules if schedules is not None else []
        self._system_prompt_builder = system_prompt_builder
        self.max_tool_rounds = max_tool_rounds
        self.history = history if history is not None else []
        self._history_path = history_path
        self._pending_schedule_chunks: list[ChatCompletionChunk] = []

    @classmethod
    async def create(
        cls,
        *,
        ai_socket: str,
        workspace_path: Path,
        max_tool_rounds: int = 128,
        session_id: str | None = None,
    ) -> SessionAgent:
        """Factory: load tools, schedules and system prompt from a workspace.

        If ``session_id`` is ``None`` a UUID is auto-generated.  The
        history JSONL file is loaded from / saved to
        ``workspace/histories/{session_id}.jsonl``.

        This is the production entry point.  The plain ``__init__`` can
        be used directly in tests when you want to inject mock tools.
        """
        tools, tool_funcs = await load_tools_from_workspace(workspace_path / "tools")
        schedules = await load_schedules_from_workspace(workspace_path / "schedules")

        history, history_path = await _init_history(workspace_path, session_id)

        return cls(
            ai_socket=ai_socket,
            tools=tools,
            tool_funcs=tool_funcs,
            schedules=schedules,
            system_prompt_builder=_load_system_prompt_builder(workspace_path),
            max_tool_rounds=max_tool_rounds,
            history=history,
            history_path=history_path,
        )

    def set_pending_schedule_chunks(self, chunks: list[ChatCompletionChunk]) -> None:
        """Stash schedule-response chunks for the next channel request.

        Called by ``run_one_schedule`` after the AI processes a scheduled
        task.  The next call to ``run()`` yields these chunks before
        processing the channel message, so the user sees the schedule
        response interleaved with their own request.
        """
        self._pending_schedule_chunks = chunks

    async def run(self, user_message: dict, extra_params: dict | None = None) -> AsyncIterator[ChatCompletionChunk]:
        """Run one turn of the ReAct agent loop.

        Takes a single user message (channel sends only the latest, never
        the full history) and yields SSE chunks for the channel to forward
        to the client.

        Flow:
        1. Yield any pending schedule-response chunks (from cron tasks).
        2. Append the user message to the internal history.
        3. Send ``history + tool_defs`` to the AI backend (streaming).
        4. Accumulate content and tool-call fragments from the SSE stream.
        5. On ``finish_reason="stop"`` — save content to history, return.
        6. On ``finish_reason="tool_calls"`` — execute the tools, add
           results to history, loop back to step 3 (up to max_tool_rounds).
        7. On ``finish_reason="error"`` — return without saving anything.
        8. On any other finish_reason (or no finish_reason) — save whatever
           content we have and return.

        History is only stored in memory and is private to this instance.
        Multiple concurrent callers are serialized externally (via the
        ``anyio.Lock`` in ``server.py``).
        """
        # Build system prompt lazily on the first run if a builder was
        # provided and history does not already start with a system message.
        if not self.history and self._system_prompt_builder is not None:
            try:
                sp = await self._system_prompt_builder()
                self.history.append({"role": "system", "content": sp})
                logger.info(f"System prompt loaded ({len(sp) if sp else 0} chars)")
            except Exception as e:
                logger.error(f"Failed to build system prompt: {e}")

        # Yield pending schedule response chunks first
        if self._pending_schedule_chunks:
            logger.info(f"Yielding {len(self._pending_schedule_chunks)} pending schedule chunk(s)")
            for chunk in self._pending_schedule_chunks:
                yield chunk
            self._pending_schedule_chunks = []

        self.history.append(user_message)
        logger.debug(f"History now has {len(self.history)} messages")

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

            logger.info(f"Sending request to AI socket: {self.ai_socket}")
            logger.debug(f"Request messages count: {len(self.history)}, tools: {len(tool_defs)}")

            finish_reason: str | None = None
            accumulated_tool_calls: dict[int, dict] = {}
            accumulated_content: str = ""
            accumulated_reasoning: str = ""

            # --- SSE stream consumption ---
            async for chunk in self._stream_ai_request(request_body):
                yield chunk

                if chunk.choices:
                    choice = chunk.choices[0]
                    if choice.finish_reason and not finish_reason:
                        finish_reason = choice.finish_reason
                    if choice.delta.content:
                        accumulated_content += choice.delta.content
                    if choice.delta.reasoning:
                        accumulated_reasoning += choice.delta.reasoning
                    if choice.delta.tool_calls:
                        for tc in choice.delta.tool_calls:
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
                    return

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

                    # Order accumulated tool_calls by index
                    ordered_calls = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]

                    # Add assistant message with tool_calls to history
                    assistant_msg: dict = {"role": "assistant", "tool_calls": ordered_calls}
                    if accumulated_content:
                        assistant_msg["content"] = accumulated_content
                    if accumulated_reasoning:
                        assistant_msg["reasoning"] = accumulated_reasoning
                    self.history.append(assistant_msg)

                    # Execute each tool call
                    for tc in ordered_calls:
                        func_info = tc.get("function", {})
                        func_name = func_info.get("name", "")
                        func_args_str = func_info.get("arguments", "{}")

                        try:
                            args = json.loads(func_args_str)
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(f"Failed to parse tool call arguments: {func_args_str[:200]}")
                            args = {}

                        logger.info(f"Executing tool: {func_name}({args})")

                        yield ChatCompletionChunk(
                            id="tool_call",
                            choices=[
                                StreamChoice(
                                    index=0,
                                    delta=DeltaMessage(
                                        reasoning=(f"[Tool Call: {func_name}({json.dumps(args, ensure_ascii=False)})]"),
                                    ),
                                )
                            ],
                        )

                        func = self._tool_funcs.get(func_name)
                        if func is None:
                            result = f"Error: Tool '{func_name}' not found"
                            logger.error(result)
                        else:
                            try:
                                result = await func(**args)
                                logger.info(f"Tool result ({func_name}): {str(result)[:200]}")
                            except Exception as e:
                                result = f"Error executing tool '{func_name}': {e}"
                                logger.error(result)

                        yield ChatCompletionChunk(
                            id="tool_result",
                            choices=[
                                StreamChoice(
                                    index=0,
                                    delta=DeltaMessage(reasoning=f"[Tool Result: {str(result)[:500]}]"),
                                )
                            ],
                        )

                        self.history.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "name": func_name,
                                "content": str(result),
                            }
                        )

                    # Inner for-tc loop done — fall through to next _round
                    break

            # SSE stream ended without a recognised finish_reason
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
            yield ChatCompletionChunk(
                id="max_rounds",
                choices=[
                    StreamChoice(
                        index=0,
                        delta=DeltaMessage(content="[Max tool rounds reached]"),
                        finish_reason="stop",
                    )
                ],
            )

    def _build_connector_and_endpoint(self) -> tuple[aiohttp.BaseConnector, str]:
        """Resolve self.ai_socket to an aiohttp connector and HTTP endpoint."""
        return resolve_connector_and_endpoint(self.ai_socket)

    async def _stream_ai_request(self, request_body: dict) -> AsyncIterator[ChatCompletionChunk]:
        """Send a request to the AI backend and yield parsed SSE chunks.

        Parses the SSE stream into ``ChatCompletionChunk`` objects — one
        per SSE data line.  Non-200 responses are yielded as a single
        error chunk with ``finish_reason="error"`` and the stream terminates.
        """
        connector, endpoint = self._build_connector_and_endpoint()
        async with (
            aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=None)) as session,
            session.post(endpoint, json=request_body) as resp,
        ):
            logger.info(f"AI response status: {resp.status}")
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"AI error: {error_text[:500]}")
                yield ChatCompletionChunk(
                    id="error",
                    choices=[
                        StreamChoice(
                            index=0,
                            delta=DeltaMessage(content=f"[AI Error: {resp.status}]"),
                            finish_reason="error",
                        )
                    ],
                )
                return

            async for raw_line in resp.content:
                line = raw_line.decode().strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    continue

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse SSE data: {data_str[:100]}")
                    continue

                choices_data = data.get("choices", [])
                if len(choices_data) > 1:
                    logger.warning(f"Expected 1 choice, got {len(choices_data)}, yielding error")
                    yield ChatCompletionChunk(
                        id="error",
                        choices=[
                            StreamChoice(
                                index=0,
                                delta=DeltaMessage(content=f"[AI Error: expected 1 choice, got {len(choices_data)}]"),
                                finish_reason="error",
                            )
                        ],
                    )
                    return
                if not choices_data:
                    continue

                c = choices_data[0]
                delta_data = c.get("delta")
                if not isinstance(delta_data, dict):
                    delta_data = {}
                delta = DeltaMessage(
                    content=delta_data.get("content"),
                    role=delta_data.get("role"),
                    reasoning=delta_data.get("reasoning"),
                    tool_calls=delta_data.get("tool_calls"),
                )
                yield ChatCompletionChunk(
                    id=data.get("id", "chatcmpl-unknown"),
                    created=data.get("created", 0),
                    choices=[
                        StreamChoice(
                            index=c.get("index", 0),
                            delta=delta,
                            finish_reason=c.get("finish_reason"),
                        )
                    ],
                )

    async def handle_chat_completions(self, request: web.Request) -> web.StreamResponse:
        """Channel-facing HTTP/SSE handler — one request per channel message.

        Attached to ``serve_session`` as the route handler.  The channel
        sends only the latest user message (no history); the agent
        maintains its own conversation history internally.
        """
        logger.info("Received channel request")
        lock: anyio.Lock = request.app["lock"]

        try:
            body: dict = await request.json()
            logger.debug(f"Channel request body: {json.dumps(body, ensure_ascii=False)[:500]}")
        except Exception as e:
            logger.error(f"Failed to parse request body: {e}")
            return web.json_response(
                {"error": {"message": str(e), "type": "invalid_request_error", "param": None, "code": 400}},
                status=400,
            )

        messages = body.pop("messages", [])
        if not messages:
            return web.json_response(
                {
                    "error": {
                        "message": "No messages in request",
                        "type": "invalid_request_error",
                        "param": None,
                        "code": 400,
                    }
                },
                status=400,
            )

        user_message = messages[-1]
        if user_message.get("role") != "user":
            user_message = {"role": "user", "content": str(user_message.get("content", ""))}

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

        async with lock:
            await response.prepare(request)
            logger.info("Acquired session lock, processing request")
            try:
                async for chunk in self.run(user_message, extra_params=body):
                    await response.write(chunk.to_sse().encode())
                    logger.debug(
                        f"Chunk sent: content={chunk.choices[0].delta.content!r}, "
                        f"reasoning={chunk.choices[0].delta.reasoning!r}"
                    )
            except Exception as e:
                logger.error(f"Error in agent run: {e}")
                err_chunk = ChatCompletionChunk(
                    id="error",
                    choices=[
                        StreamChoice(
                            index=0,
                            delta=DeltaMessage(content=f"[Session Error: {e}]"),
                            finish_reason="error",
                        )
                    ],
                )
                await response.write(err_chunk.to_sse().encode())

        await response.write(b"data: [DONE]\n\n")
        logger.debug("Session request completed")
        return response


async def _init_history(
    workspace_path: Path,
    session_id: str | None = None,
) -> tuple[list[dict], Path]:
    """Set up the history directory and load an existing JSONL file.

    Returns the loaded message list and the path to the JSONL file.
    """
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
    """Load conversation history from a JSONL file.

    Each line must be a valid JSON object (a message dict).  Corrupt
    lines are skipped with a warning.
    """
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
    """Overwrite a JSONL file with the current conversation history.

    Errors are caught and logged — a failed save does not interrupt
    the ongoing conversation.
    """
    try:
        content = "\n".join(json.dumps(msg, ensure_ascii=False) for msg in history) + "\n"
        await anyio.Path(str(path)).write_text(content)
        logger.debug(f"History saved to {path} ({len(history)} messages)")
    except Exception as e:
        logger.error(f"Failed to save history: {e}")


def _load_system_prompt_builder(workspace_path: Path) -> Callable[..., Any] | None:
    """Import ``system_prompt_builder`` from ``workspace/systems/system.py``.

    Returns an async callable (the module's ``system_prompt_builder``
    function) or ``None`` if the file does not exist, the function is
    not found, or is not async.
    """
    system_py = workspace_path / "systems" / "system.py"
    try:
        spec = importlib.util.spec_from_file_location("psi_workspace_system", str(system_py))
        if spec is None or spec.loader is None:
            logger.warning(f"No system.py found at {system_py}")
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules["psi_workspace_system"] = module
        spec.loader.exec_module(module)
        func = getattr(module, "system_prompt_builder", None)
        if func is None or not inspect.iscoroutinefunction(func):
            logger.warning(f"system_prompt_builder not found or not async in {system_py}")
            return None
        return func
    except Exception as e:
        logger.error(f"Failed to load system_prompt_builder: {e}")
        return None
