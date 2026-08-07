"""LLM-backed candidate selection without exposing private socket addresses."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Protocol

from loguru import logger

from ..errors import InvalidRouterRequestError
from ..models import CompletionResult
from ..request import copy_public_request_body
from .errors import RouteSelectionError
from .models import RoutingConfig, SelectionResult
from .prompts import build_selector_messages


class _CompletionClient(Protocol):
    async def complete(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> CompletionResult: ...


class RouteSelector:
    """Build a compact selector request and resolve its opaque candidate ID."""

    def __init__(self, *, config: RoutingConfig, client: _CompletionClient) -> None:
        self.config = config
        self.client = client
        self._targets = {target.candidate_id: target for target in config.targets}

    async def select(self, *, request_body: dict[str, Any]) -> SelectionResult:
        """Return exactly one configured target for a Chat Completions request."""

        result = await self.client.complete(
            socket=self.config.selector_socket,
            body=self.build_request(request_body=request_body),
            timeout=self.config.selector_timeout,
        )
        if result.finish_reason != "stop":
            raise RouteSelectionError(f"Selector returned unsupported finish reason {result.finish_reason!r}")
        if result.tool_calls:
            raise RouteSelectionError("Selector returned tool calls instead of a routing decision")
        try:
            selection = json.loads(result.content.strip())
        except json.JSONDecodeError as error:
            raise RouteSelectionError(f"Selector output is not valid JSON: {error.msg}") from error
        if not isinstance(selection, dict) or set(selection) != {"candidate_id"}:
            raise RouteSelectionError("Selector output must contain only candidate_id")
        candidate_id = selection.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id not in self._targets:
            raise RouteSelectionError(f"Selector chose an unknown candidate {candidate_id!r}")
        target = self._targets[candidate_id]
        logger.info(f"Routing selector chose candidate {candidate_id!r}")
        return SelectionResult(candidate_id=candidate_id, target=target)

    def build_request(self, *, request_body: dict[str, Any]) -> dict[str, Any]:
        """Build the private selector request from public task information."""

        messages = request_body.get("messages")
        if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
            raise InvalidRouterRequestError("messages must be a list of objects")
        tools = request_body.get("tools", [])
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise InvalidRouterRequestError("tools must be a list of objects")

        candidates = [
            {"candidate_id": target.candidate_id, "description": target.description} for target in self.config.targets
        ]
        selector_body = {
            "messages": build_selector_messages(
                candidates=candidates,
                conversation=self._compact_messages(messages),
                available_tools=self._tool_summaries(tools),
            ),
            "stream": True,
            "temperature": 0,
        }
        return copy_public_request_body(
            body=selector_body,
            request_overrides=self.config.selector_request_overrides,
        )

    def _compact_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if not isinstance(role, str):
                continue
            if isinstance(content, str):
                normalized.append({"role": role, "content": content})
            elif isinstance(content, list):
                normalized.append({"role": role, "content": f"[multimodal content with {len(content)} block(s)]"})

        selected: list[dict[str, str]] = []
        remaining = self.config.max_selection_chars
        for message in reversed(normalized):
            encoded = json.dumps(message, ensure_ascii=False)
            if len(encoded) <= remaining:
                selected.append(deepcopy(message))
                remaining -= len(encoded)
                continue
            if not selected and remaining > 64:
                content_budget = max(0, remaining - len(message["role"]) - 40)
                compact_content = message["content"][-content_budget:] if content_budget else ""
                selected.append({"role": message["role"], "content": compact_content})
            break
        selected.reverse()
        return selected

    @staticmethod
    def _tool_summaries(tools: list[dict[str, Any]]) -> list[dict[str, str]]:
        summaries: list[dict[str, str]] = []
        for tool in tools:
            function = tool.get("function")
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue
            description = function.get("description")
            summaries.append(
                {
                    "name": name,
                    "description": description[:256] if isinstance(description, str) else "",
                }
            )
        return summaries


__all__ = ["RouteSelector"]
