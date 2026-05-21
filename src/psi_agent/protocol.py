from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls is not None:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            d["name"] = self.name
        return d


@dataclass
class ToolFunction:
    name: str
    description: str
    parameters: dict[str, Any]

    @classmethod
    def from_callable(cls, func: Any) -> ToolFunction:
        sig = inspect.signature(func)
        doc = inspect.getdoc(func) or ""
        param_desc = cls._parse_param_descriptions(doc)
        type_hints = {}
        try:
            import typing

            type_hints = typing.get_type_hints(func)
        except Exception:
            pass
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            annotation = type_hints.get(param_name)
            param_type = "string"
            if annotation is not None:
                param_type = cls._python_type_to_json_type(annotation)
            properties[param_name] = {
                "type": param_type,
                "description": param_desc.get(param_name, ""),
            }
            if param.default is inspect.Parameter.empty:
                required.append(param_name)
        return cls(
            name=func.__name__,
            description=cls._parse_description(doc),
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
            },
        )

    @staticmethod
    def _python_type_to_json_type(annotation: Any) -> str:
        type_map: dict[type, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}
        origin = getattr(annotation, "__origin__", None)
        if origin is not None:
            return "string"
        return type_map.get(annotation, "string")

    @staticmethod
    def _parse_description(doc: str) -> str:
        lines = doc.strip().split("\n")
        desc_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("Args:") or stripped.startswith("Returns") or stripped.startswith("Yields"):
                break
            if stripped:
                desc_lines.append(stripped)
        return " ".join(desc_lines)

    @staticmethod
    def _parse_param_descriptions(doc: str) -> dict[str, str]:
        result: dict[str, str] = {}
        in_args = False
        current_param = ""
        current_desc: list[str] = []
        for line in doc.split("\n"):
            stripped = line.strip()
            if stripped.startswith("Args:"):
                in_args = True
                continue
            if in_args:
                if stripped.startswith("Returns") or stripped.startswith("Yields"):
                    break
                m = re.match(r"^(\w+):\s*(.*)", stripped)
                if m:
                    if current_param:
                        result[current_param] = " ".join(current_desc)
                    current_param = m.group(1)
                    current_desc = [m.group(2)]
                elif stripped and current_param:
                    current_desc.append(stripped)
        if current_param:
            result[current_param] = " ".join(current_desc)
        return result


@dataclass
class ToolDef:
    type: str = "function"
    function: ToolFunction | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.function is not None:
            d["function"] = {
                "name": self.function.name,
                "description": self.function.description,
                "parameters": self.function.parameters,
            }
        return d


@dataclass
class ChatCompletionRequest:
    model: str
    messages: list[Message]
    tools: list[ToolDef] = field(default_factory=list)
    stream: bool = True
    stream_options: dict[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in self.messages],
            "stream": self.stream,
        }
        if self.tools:
            d["tools"] = [t.to_dict() for t in self.tools]
        if self.stream_options is not None:
            d["stream_options"] = self.stream_options
        if self.temperature is not None:
            d["temperature"] = self.temperature
        if self.max_tokens is not None:
            d["max_tokens"] = self.max_tokens
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChatCompletionRequest:
        messages = [Message(**m) for m in data.get("messages", [])]
        tools: list[ToolDef] = []
        for t in data.get("tools", []):
            tf_data = t.get("function", {})
            tf = ToolFunction(**tf_data) if tf_data else None
            tools.append(ToolDef(type=t.get("type", "function"), function=tf))
        return cls(
            model=data.get("model", ""),
            messages=messages,
            tools=tools,
            stream=data.get("stream", True),
            stream_options=data.get("stream_options"),
            temperature=data.get("temperature"),
            max_tokens=data.get("max_tokens"),
        )


@dataclass
class DeltaMessage:
    content: str | None = None
    role: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.content is not None:
            d["content"] = self.content
        if self.role is not None:
            d["role"] = self.role
        if self.reasoning_content is not None:
            d["reasoning_content"] = self.reasoning_content
        if self.tool_calls is not None:
            d["tool_calls"] = self.tool_calls
        return d


@dataclass
class StreamChoice:
    index: int = 0
    delta: DeltaMessage = field(default_factory=DeltaMessage)
    finish_reason: str | None = None


@dataclass
class ChatCompletionChunk:
    id: str = "chatcmpl-unknown"
    object: str = "chat.completion.chunk"
    created: int = 0
    model: str = ""
    choices: list[StreamChoice] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": self.object,
            "created": self.created,
            "model": self.model,
            "choices": [
                {
                    "index": c.index,
                    "delta": c.delta.to_dict(),
                    "finish_reason": c.finish_reason,
                }
                for c in self.choices
            ],
        }

    def to_sse(self) -> str:
        return f"data: {json.dumps(self.to_dict(), ensure_ascii=False)}\n\n"

    @staticmethod
    def sse_done() -> str:
        return "data: [DONE]\n\n"

    @classmethod
    def from_openai_chunk(cls, data: dict[str, Any]) -> list[ChatCompletionChunk] | None:
        choices_data = data.get("choices", [])
        choices: list[StreamChoice] = []
        for c in choices_data:
            delta_data = c.get("delta", {})
            delta = DeltaMessage(
                content=delta_data.get("content"),
                role=delta_data.get("role"),
                reasoning_content=delta_data.get("reasoning_content"),
                tool_calls=delta_data.get("tool_calls"),
            )
            choices.append(
                StreamChoice(
                    index=c.get("index", 0),
                    delta=delta,
                    finish_reason=c.get("finish_reason"),
                )
            )
        return [
            cls(
                id=data.get("id", ""),
                object=data.get("object", ""),
                created=data.get("created", 0),
                model=data.get("model", ""),
                choices=choices,
            )
        ]


@dataclass
class ErrorResponse:
    message: str
    type: str
    code: str

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"message": self.message, "type": self.type, "code": self.code}}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
