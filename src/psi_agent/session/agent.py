from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from copy import deepcopy
from typing import Any

from aiohttp import ClientTimeout
from loguru import logger

from psi_agent.memory.adapter import SessionMemoryAdapter
from psi_agent.net import make_client_session
from psi_agent.session.protocol import ChatCompletionChunk, DeltaMessage, StreamChoice, ToolFunction

MAX_TOOL_ROUNDS = 10
AfterTurnFn = Callable[[list[dict], int, list[str]], Awaitable[None]]
AfterTurnSnapshot = tuple[list[dict], int, list[str]]


class SessionAgent:
    def __init__(
        self,
        *,
        ai_socket: str,
        tools: dict[str, ToolFunction],
        model: str,
        system_prompt: str | None = None,
        after_turn_fn: AfterTurnFn | None = None,
        memory_adapter: SessionMemoryAdapter | None = None,
    ) -> None:
        self.ai_socket = ai_socket
        self.tools = tools
        self._tool_funcs: dict[str, Any] = {}
        self.model = model
        self._after_turn_fn = after_turn_fn
        self.history: list[dict] = []
        if system_prompt:
            self.history.append({"role": "system", "content": system_prompt})
        self._pending_schedule_chunks: list[ChatCompletionChunk] = []
        self._after_turn_snapshot: AfterTurnSnapshot | None = None
        self._memory = memory_adapter

    def register_tool_func(self, name: str, func: Any) -> None:
        self._tool_funcs[name] = func

    def set_pending_schedule_chunks(self, chunks: list[ChatCompletionChunk]) -> None:
        self._pending_schedule_chunks = chunks

    def spawn_after_turn_task(self, task_group: Any | None = None) -> None:
        if self._after_turn_fn is None or self._after_turn_snapshot is None:
            return
        if task_group is None:
            logger.warning("After-turn hook is configured but no task group is available")
            return
        snapshot = self._consume_after_turn_snapshot()
        if snapshot is None:
            return
        try:
            task_group.start_soon(self.run_after_turn, *snapshot)
        except Exception as e:
            logger.error(f"after_turn task could not be scheduled: {e}")

    async def run_after_turn(
        self,
        messages: list[dict] | None = None,
        tool_call_count: int | None = None,
        called_tools: list[str] | None = None,
    ) -> None:
        if self._after_turn_fn is None:
            return
        if messages is None or tool_call_count is None or called_tools is None:
            snapshot = self._consume_after_turn_snapshot()
            if snapshot is None:
                return
            messages, tool_call_count, called_tools = snapshot
        try:
            await self._after_turn_fn(messages, tool_call_count, called_tools)
        except Exception as e:
            logger.error(f"after_turn task failed: {e}")

    async def run(self, user_message: dict) -> AsyncIterator[ChatCompletionChunk]:
        self._after_turn_snapshot = None

        if self._pending_schedule_chunks:
            logger.info(f"Yielding {len(self._pending_schedule_chunks)} pending schedule chunk(s)")
            for chunk in self._pending_schedule_chunks:
                yield chunk
            self._pending_schedule_chunks = []

        memory_context = await self._retrieve_memory_context(user_message)
        self.history.append(user_message)
        memory_message = self._insert_memory_context(memory_context)
        logger.debug(f"History now has {len(self.history)} messages")

        tool_call_count = 0
        called_tools: list[str] = []

        try:
            for _round in range(MAX_TOOL_ROUNDS):
                logger.debug(f"Agent loop round {_round + 1}/{MAX_TOOL_ROUNDS}")

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
                    "model": self.model,
                    "messages": list(self.history),
                    "tools": tool_defs,
                    "stream": True,
                }

                logger.info(f"Sending request to AI socket: {self.ai_socket}")
                logger.debug(f"Request messages count: {len(self.history)}, tools: {len(tool_defs)}")

                finish_reason: str | None = None
                accumulated_tool_calls: dict[int, dict] = {}
                accumulated_content: str = ""

                async for chunk in self._stream_ai_request(request_body):
                    yield chunk

                    if chunk.choices:
                        for choice in chunk.choices:
                            if choice.finish_reason and not finish_reason:
                                finish_reason = choice.finish_reason
                            if choice.delta.content:
                                accumulated_content += choice.delta.content
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
                        if accumulated_content:
                            self.history.append({"role": "assistant", "content": accumulated_content})
                        await self._record_memory_turn(user_message, accumulated_content)
                        self._save_after_turn_snapshot(tool_call_count, called_tools)
                        return

                    if finish_reason == "tool_calls":
                        logger.info("AI requested tool calls, processing...")
                        ordered_calls = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]
                        self.history.append(
                            {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": ordered_calls,
                            }
                        )

                        for tc in ordered_calls:
                            func_info = tc.get("function", {})
                            func_name = func_info.get("name", "")
                            func_args_str = func_info.get("arguments", "{}")

                            try:
                                args = json.loads(func_args_str)
                            except json.JSONDecodeError, TypeError:
                                args = {}

                            logger.info(f"Executing tool: {func_name}({args})")
                            tool_call_count += 1
                            called_tools.append(func_name)

                            yield ChatCompletionChunk(
                                id="tool_call",
                                model=self.model,
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
                                model=self.model,
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

                        break

            else:
                logger.warning(f"Reached max tool rounds ({MAX_TOOL_ROUNDS}), stopping")
                self._save_after_turn_snapshot(tool_call_count, called_tools)
                yield ChatCompletionChunk(
                    id="max_rounds",
                    model=self.model,
                    choices=[
                        StreamChoice(
                            index=0,
                            delta=DeltaMessage(content="[Max tool rounds reached]"),
                            finish_reason="stop",
                        )
                    ],
                )
        finally:
            if memory_message is not None:
                self.history.remove(memory_message)

    async def close(self) -> None:
        if self._memory is not None:
            await self._memory.close()
            self._memory = None

    async def _retrieve_memory_context(self, user_message: dict[str, Any]) -> str | None:
        if self._memory is None:
            return None
        return await self._memory.retrieve_for_turn(user_message)

    async def _record_memory_turn(self, user_message: dict[str, Any], assistant_content: str) -> None:
        if self._memory is None:
            return
        await self._memory.record_turn(user_message, assistant_content)

    def _insert_memory_context(self, memory_context: str | None) -> dict[str, str] | None:
        if not memory_context:
            return None
        message = {"role": "system", "content": memory_context}
        insert_at = 1 if self.history and self.history[0].get("role") == "system" else 0
        self.history.insert(insert_at, message)
        return message

    def _save_after_turn_snapshot(self, tool_call_count: int, called_tools: list[str]) -> None:
        self._after_turn_snapshot = (deepcopy(self.history), tool_call_count, list(called_tools))

    def _consume_after_turn_snapshot(self) -> AfterTurnSnapshot | None:
        snapshot = self._after_turn_snapshot
        self._after_turn_snapshot = None
        return snapshot

    async def _stream_ai_request(self, request_body: dict) -> AsyncIterator[ChatCompletionChunk]:
        client_session, endpoint = make_client_session(self.ai_socket, timeout=ClientTimeout(total=None))
        async with client_session as session, session.post(endpoint, json=request_body) as resp:
            logger.info(f"AI response status: {resp.status}")
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"AI error: {error_text[:500]}")
                yield ChatCompletionChunk(
                    id="error",
                    model=self.model,
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
                    delta_data = c.get("delta", {})
                    delta = DeltaMessage(
                        content=delta_data.get("content"),
                        role=delta_data.get("role"),
                        reasoning_content=delta_data.get("reasoning_content"),
                        tool_calls=delta_data.get("tool_calls"),
                    )
                    yield ChatCompletionChunk(
                        id=data.get("id", "chatcmpl-unknown"),
                        model=data.get("model", ""),
                        created=data.get("created", 0),
                        choices=[
                            StreamChoice(
                                index=c.get("index", 0),
                                delta=delta,
                                finish_reason=c.get("finish_reason"),
                            )
                        ],
                    )
