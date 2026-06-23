from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import aiohttp
import anyio
from aiohttp import web
from loguru import logger

from psi_agent.session.protocol import ChatCompletionChunk, DeltaMessage, StreamChoice, ToolFunction
from psi_agent.session.scheduler import load_schedules_from_workspace
from psi_agent.session.tools import load_tools_from_workspace


class SessionAgent:
    def __init__(
        self,
        *,
        ai_socket: str,
        tools: dict[str, ToolFunction],
        max_tool_rounds: int = 128,
    ) -> None:
        """Initialize an agent backed by an AI server.

        ``ai_socket`` is a Unix socket path, ``http(s)://`` URL, or Windows
        named pipe for the AI backend.  ``tools`` provides the metadata
        (name + JSON Schema) sent to the AI.

        The lower-level constructor is test-friendly.  For production use
        the ``create()`` classmethod which also loads tools, schedules,
        and the system prompt from a workspace.
        """
        self.ai_socket = ai_socket
        self.tools = tools
        self._tool_funcs: dict[str, Callable[..., Any]] = {}
        self._system_prompt_builder: Callable[..., Any] | None = None
        self.max_tool_rounds = max_tool_rounds
        self.history: list[dict] = []
        self._pending_schedule_chunks: list[ChatCompletionChunk] = []
        self.schedules: list = []  # populated by create()

    @classmethod
    async def create(
        cls,
        *,
        ai_socket: str,
        workspace_path: Path,
        max_tool_rounds: int = 128,
    ) -> SessionAgent:
        """Factory: load tools, schedules and system prompt from a workspace.

        This is the production entry point.  The plain ``__init__`` can
        be used directly in tests when you want to inject mock tools.
        """
        tools, tool_funcs = await load_tools_from_workspace(workspace_path / "tools")
        schedules = await load_schedules_from_workspace(workspace_path / "schedules")

        agent = cls(ai_socket=ai_socket, tools=tools, max_tool_rounds=max_tool_rounds)
        agent.schedules = schedules
        for name in tool_funcs:
            agent.register_tool_func(name, tool_funcs[name])
            logger.info(f"Registered tool callable: {name}")
        agent._system_prompt_builder = _load_system_prompt_builder(workspace_path)
        return agent

    def register_tool_func(self, name: str, func: Callable[..., Any]) -> None:
        """Register a callable for a previously-loaded tool.

        Tool metadata (from ``load_tools_from_workspace``) tells the AI what
        functions exist.  This method binds the actual ``async def`` function
        that will be invoked when the AI requests the tool.
        """
        self._tool_funcs[name] = func

    def set_pending_schedule_chunks(self, chunks: list[ChatCompletionChunk]) -> None:
        """Stash schedule-response chunks for the next channel request.

        Called by ``run_one_schedule`` after the AI processes a scheduled
        task.  The next call to ``run()`` yields these chunks before
        processing the channel message, so the user sees the schedule
        response interleaved with their own request.
        """
        self._pending_schedule_chunks = chunks

    async def run(self, user_message: dict) -> AsyncIterator[ChatCompletionChunk]:
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

            request_body = {
                "messages": self.history,
                "tools": tool_defs,
                "stream": True,
            }

            logger.info(f"Sending request to AI socket: {self.ai_socket}")
            logger.debug(f"Request messages count: {len(self.history)}, tools: {len(tool_defs)}")

            finish_reason: str | None = None
            accumulated_tool_calls: dict[int, dict] = {}
            accumulated_content: str = ""

            # --- SSE stream consumption ---
            async for chunk in self._stream_ai_request(request_body):
                yield chunk

                if chunk.choices:
                    for choice in chunk.choices:
                        if choice.finish_reason and not finish_reason:
                            finish_reason = choice.finish_reason
                        if choice.delta.content:
                            accumulated_content += choice.delta.content
                        if choice.delta.tool_calls:
                            # Streaming tool calls arrive piecemeal across
                            # multiple SSE chunks with the same index.
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
                    if accumulated_content:
                        self.history.append({"role": "assistant", "content": accumulated_content})
                    return

                if finish_reason == "tool_calls":
                    logger.info("AI requested tool calls, processing...")

                    # Order accumulated tool_calls by index
                    ordered_calls = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]

                    # Add assistant message with tool_calls to history
                    self.history.append(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": ordered_calls,
                        }
                    )

                    # Execute each tool call
                    for tc in ordered_calls:
                        func_info = tc.get("function", {})
                        func_name = func_info.get("name", "")
                        func_args_str = func_info.get("arguments", "{}")

                        try:
                            args = json.loads(func_args_str)
                        except json.JSONDecodeError, TypeError:
                            logger.warning(f"Failed to parse tool call arguments: {func_args_str[:200]}")
                            args = {}

                        logger.info(f"Executing tool: {func_name}({args})")

                        yield ChatCompletionChunk(
                            id="tool_call",
                            choices=[
                                StreamChoice(
                                    index=0,
                                    delta=DeltaMessage(
                                        reasoning_content=(
                                            f"[Tool Call: {func_name}({json.dumps(args, ensure_ascii=False)})]"
                                        ),
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
                                    delta=DeltaMessage(reasoning_content=f"[Tool Result: {str(result)[:500]}]"),
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
                if accumulated_content:
                    self.history.append({"role": "assistant", "content": accumulated_content})
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

    def _build_connector_and_endpoint(self) -> tuple:
        """Resolve self.ai_socket to an aiohttp connector and HTTP endpoint.

        Supported transports:
        - ``http(s)://host:port`` → TCPConnector
        - ``\\\\.\\pipe\\name`` → NamedPipeConnector (Windows only)
        - bare filesystem path  → UnixConnector
        """
        if self.ai_socket.startswith(("http://", "https://")):
            connector = aiohttp.TCPConnector(ssl=self.ai_socket.startswith("https://"))
            endpoint = self.ai_socket.rstrip("/") + "/chat/completions"
        elif self.ai_socket.startswith("\\\\.\\pipe\\"):
            connector = aiohttp.NamedPipeConnector(self.ai_socket)
            endpoint = "http://localhost/chat/completions"
        else:
            connector = aiohttp.UnixConnector(path=self.ai_socket)
            endpoint = "http://localhost/chat/completions"
        return connector, endpoint

    async def _stream_ai_request(self, request_body: dict) -> AsyncIterator[ChatCompletionChunk]:
        """Send a request to the AI backend and yield parsed SSE chunks.

        Parses the SSE stream into ``ChatCompletionChunk`` objects — one
        per SSE data line.  Non-200 responses are yielded as a single
        error chunk with ``finish_reason="error"`` and the stream terminates.
        """
        connector, endpoint = self._build_connector_and_endpoint()
        async with (
            aiohttp.ClientSession(
                connector=connector, timeout=aiohttp.ClientTimeout(total=300, sock_connect=30, sock_read=60)
            ) as session,
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
                for c in choices_data:
                    delta_data = c.get("delta")
                    if not isinstance(delta_data, dict):
                        delta_data = {}
                    delta = DeltaMessage(
                        content=delta_data.get("content"),
                        role=delta_data.get("role"),
                        reasoning_content=delta_data.get("reasoning_content"),
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
                {"error": {"message": str(e), "type": "invalid_request", "code": "400"}},
                status=400,
            )

        messages = body.get("messages", [])
        if not messages:
            return web.json_response(
                {"error": {"message": "No messages in request", "type": "invalid_request", "code": "400"}},
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
                async for chunk in self.run(user_message):
                    await response.write(chunk.to_sse().encode())
                    logger.debug(
                        f"Chunk sent: content={chunk.choices[0].delta.content!r}, "
                        f"reasoning={chunk.choices[0].delta.reasoning_content!r}"
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
