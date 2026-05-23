from __future__ import annotations

import contextlib
import inspect
import json
import re
import typing
from dataclasses import dataclass, field
from typing import Any


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
        with contextlib.suppress(Exception):
            type_hints = typing.get_type_hints(func)
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


@dataclass
class ErrorResponse:
    message: str
    type: str
    code: str

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"message": self.message, "type": self.type, "code": self.code}}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
