from __future__ import annotations

import inspect
import json
import re
import types
import typing
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar


@dataclass(frozen=True)
class SessionToolContext:
    session_id: str | None
    workspace_path: Path | None
    history_path: Path | None
    history_messages: tuple[Mapping[str, Any], ...]
    latest_user_message: Mapping[str, Any]
    ai_socket: str

    _current: ClassVar[ContextVar[SessionToolContext | None]] = ContextVar(
        "SESSION_TOOL_CONTEXT",
        default=None,
    )

    @classmethod
    def current(cls) -> SessionToolContext | None:
        return cls._current.get()

    @classmethod
    def freeze_message(cls, message: Mapping[str, Any]) -> Mapping[str, Any]:
        return cls._freeze(deepcopy(dict(message)))

    @classmethod
    def freeze_messages(cls, messages: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        return tuple(cls.freeze_message(message) for message in messages)

    @contextmanager
    def push(self) -> Iterator[None]:
        token = self._current.set(self)
        try:
            yield
        finally:
            self._current.reset(token)

    @classmethod
    def _freeze(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return types.MappingProxyType({key: cls._freeze(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(cls._freeze(item) for item in value)
        return value


@dataclass
class ToolFunction:
    name: str
    description: str
    parameters: dict[str, Any]

    @classmethod
    def from_callable(cls, func: Any) -> ToolFunction:
        """Build an OpenAI function-calling tool definition from an async Python function.

        Inspects the function's signature, type hints, and docstring to produce
        the JSON Schema that the LLM uses to understand when and how to call it.

        Docstring convention (Google-style):

            async def search(query: str, limit: int = 10) -> str:
                '''Search for something.               ← tool description (everything before Args:)
                Args:
                    query: The search query.           ← parameter description
                    limit: Max results to return.
                '''

        Type hints are resolved via `typing.get_type_hints()` because the project
        uses ``from __future__ import annotations`` which stores all annotations
        as strings.  If get_type_hints() fails (e.g. unresolvable forward
        reference) the exception propagates — the caller skips the tool with a
        warning rather than silently degrading it.
        """
        sig = inspect.signature(func)
        doc = inspect.getdoc(func) or ""
        description = cls._parse_description(doc)
        param_desc = cls._parse_param_descriptions(doc)

        # Resolve string annotations to real types.  `from __future__ import
        # annotations` stores all annotations as strings — get_type_hints()
        # resolves them.  If a forward reference cannot be resolved the call
        # raises and the tool is skipped with a warning by the caller.
        type_hints = typing.get_type_hints(func)

        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                raise TypeError(
                    f"Variadic parameters (*args, **kwargs) are not supported in tools: "
                    f"'{param_name}' in '{func.__name__}'"
                )

            annotation = type_hints.get(param_name)

            # X | None (Optional[X]) — unwrap to X and mark as not-required.
            # Multi-type unions like int | str or int | str | None are
            # rejected — there is no single JSON Schema type for them.
            is_type_optional = False
            if annotation is not None:
                origin = getattr(annotation, "__origin__", None)
                if origin is types.UnionType:
                    args = getattr(annotation, "__args__", ())
                    non_none = [a for a in args if a is not type(None)]
                    if len(non_none) == 1:
                        annotation = non_none[0]
                        is_type_optional = True
                    else:
                        raise TypeError(
                            f"Unsupported union type: {annotation!r}. Use X | None for a single optional type."
                        )

            # Map Python type annotation to JSON Schema type.
            # All branches produce a dict (at minimum {"type": "..."}) so the
            # properties assignment below is uniform.
            if annotation is not None:
                _type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}
                origin = getattr(annotation, "__origin__", None)
                if origin is not None:
                    if origin is not list:
                        raise TypeError(f"Unsupported generic type: {annotation!r}. Only list[X] is supported.")
                    args = getattr(annotation, "__args__", ())
                    item = args[0] if args else str
                    if getattr(item, "__origin__", None) is not None or item not in _type_map:
                        raise TypeError(f"Unsupported list item type: {item!r}. Supported: str, int, float, bool")
                    resolved = {"type": "array", "items": {"type": _type_map[item]}}
                elif annotation not in _type_map:
                    raise TypeError(
                        f"Unsupported parameter type: {annotation!r}. Supported: str, int, float, bool, list[X]"
                    )
                else:
                    resolved = {"type": _type_map[annotation]}
            else:
                resolved = {"type": "string"}

            properties[param_name] = resolved | {"description": param_desc.get(param_name, "")}

            if param.default is inspect.Parameter.empty and not is_type_optional:
                required.append(param_name)

        return cls(
            name=func.__name__,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
            },
        )

    @staticmethod
    def _parse_description(doc: str) -> str:
        """Extract the tool's purpose from the leading lines of a docstring.

        Everything before the first ``Args:``, ``Returns:``, or ``Yields:``
        section is treated as the high-level description sent to the LLM.
        """
        lines = doc.strip().split("\n")
        desc_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("Args:") or stripped.startswith("Returns:") or stripped.startswith("Yields:"):
                break
            if stripped:
                desc_lines.append(stripped)
        return " ".join(desc_lines)

    @staticmethod
    def _parse_param_descriptions(doc: str) -> dict[str, str]:
        """Parse the ``Args:`` section of a Google-style docstring.

        Returns a mapping from parameter name to its description string.
        Lines that follow a parameter name (indented continuation) are
        joined into a single description.
        """
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
                if stripped.startswith("Returns:") or stripped.startswith("Yields:"):
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
    reasoning: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.content is not None:
            d["content"] = self.content
        if self.role is not None:
            d["role"] = self.role
        if self.reasoning is not None:
            d["reasoning"] = self.reasoning
        if self.tool_calls is not None:
            d["tool_calls"] = self.tool_calls
        return d


@dataclass
class StreamChoice:
    index: int = 0
    delta: DeltaMessage = field(default_factory=DeltaMessage)
    finish_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"index": self.index, "delta": self.delta.to_dict()}
        if self.finish_reason is not None:
            d["finish_reason"] = self.finish_reason
        return d


@dataclass
class ChatCompletionChunk:
    id: str = "chatcmpl-unknown"
    object: str = "chat.completion.chunk"
    created: int = 0
    choices: list[StreamChoice] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": self.object,
            "created": self.created,
            "choices": [c.to_dict() for c in self.choices],
        }

    def to_sse(self) -> str:
        return f"data: {json.dumps(self.to_dict(), ensure_ascii=False)}\n\n"
