from __future__ import annotations

import json
from typing import Any

from .models import RouteDecision, Upstream


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    markers = {
        "image": "[IMAGE]",
        "image_url": "[IMAGE]",
        "input_image": "[IMAGE]",
        "audio": "[AUDIO]",
        "input_audio": "[AUDIO]",
        "file": "[FILE]",
        "input_file": "[FILE]",
    }
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text")
            item_type = item.get("type")
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(item_type, str) and item_type in markers:
                parts.append(markers[item_type])
    return "\n".join(part for part in parts if part)


def _assistant_tools(message: dict[str, Any]) -> str:
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return ""
    names: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return f"[TOOLS] {', '.join(names)}" if names else ""


def serialize_context(messages: Any, *, max_chars: int) -> str:
    if max_chars <= 0:
        raise ValueError("router_context_chars must be positive")
    if not isinstance(messages, list):
        return ""
    system = ""
    blocks: list[str] = []
    has_user = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if not isinstance(role, str):
            continue
        if role == "system" and not system:
            system = _content_text(message.get("content"))
            continue
        if role == "tool":
            blocks.append("[TOOL]\nTool results exist; result bodies are omitted.")
            continue
        if role not in {"user", "assistant"}:
            continue
        text = _content_text(message.get("content"))
        if role == "assistant":
            tools = _assistant_tools(message)
            text = "\n".join(part for part in (text, tools) if part)
        if text:
            has_user = has_user or role == "user"
            blocks.append(f"[{role.upper()}]\n{text}")
    if not has_user:
        return ""
    if system:
        blocks.insert(0, f"[SYSTEM]\n{system}")
    while len("\n\n".join(blocks)) > max_chars and len(blocks) > (2 if system else 1):
        blocks.pop(1 if system else 0)
    result = "\n\n".join(blocks)
    if len(result) <= max_chars:
        return result
    marker = "[TRUNCATED]"
    if max_chars <= len(marker):
        return marker[:max_chars]
    return result[: max_chars - len(marker)] + marker


def build_routing_messages(context: str, targets: tuple[Upstream, ...]) -> list[dict[str, str]]:
    candidates = "\n".join(f"Candidate {index}: {target.description}" for index, target in enumerate(targets))
    system = (
        "Select the single candidate whose description best matches the conversation.\n"
        f"{candidates}\n"
        'Return JSON only: {"candidate":0,"reason":"brief explanation"}.'
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": context}]


def parse_decision(text: str, *, candidate_count: int) -> RouteDecision:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        candidate = value.get("candidate")
        if not isinstance(candidate, int) or isinstance(candidate, bool) or not 0 <= candidate < candidate_count:
            continue
        reason = value.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = "Routing model provided no reason"
        return RouteDecision(candidate=candidate, reason=reason.strip())
    raise ValueError("routing response did not contain a valid candidate")
