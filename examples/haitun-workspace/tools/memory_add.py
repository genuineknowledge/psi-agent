from __future__ import annotations

import hashlib
import json
import sys
import types
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol, cast

TOOLS_DIR = Path(__file__).resolve().parent


class MemoryConfig(Protocol):
    base_url: str
    scope: dict[str, Any]
    timeout_seconds: float


PostJson = Callable[[str, str, dict[str, Any], float], Awaitable[dict[str, Any]]]
FormatErrorResult = Callable[[Exception], str]


def _load_sibling_module(name: str) -> dict[str, Any]:
    path = TOOLS_DIR / f"{name}.py"
    module_name = f"fusion_memory_tool_{name}_{hashlib.sha256(str(path).encode()).hexdigest()[:12]}"
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    source = path.read_text(encoding="utf-8")
    sys.modules[module_name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module.__dict__


_client = _load_sibling_module("_client")
_config = _load_sibling_module("_config")
_format_error_result = cast(FormatErrorResult, _client["format_error_result"])
_post_json = cast(PostJson, _client["post_json"])
CONFIG = cast(MemoryConfig, _config["CONFIG"])


async def memory_add(content: str, source: str = "haitun-tool") -> str:
    """Store durable information in Fusion Memory.

    Args:
        content: The preference, fact, or decision to store.
        source: Optional source label for metadata.

    Returns:
        A JSON string describing the result of the add request.
    """
    try:
        data = await _post_json(
            CONFIG.base_url,
            "/add",
            {
                "input": {"role": "user", "content": content},
                "scope": CONFIG.scope,
                "metadata": {
                    "source": source or "haitun-tool",
                    "write_mode": "explicit_tool",
                    "auto_persisted": False,
                },
            },
            CONFIG.timeout_seconds,
        )
        return json.dumps({"ok": True, "saved": True, "result": data}, ensure_ascii=False)
    except Exception as exc:
        return _format_error_result(exc)
