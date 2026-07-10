from __future__ import annotations

import hashlib
import sys
import types
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol
from typing import cast as _cast

TOOLS_DIR = Path(__file__).resolve().parent


class MemoryConfig(Protocol):
    allow_cross_session: bool
    base_url: str
    scope: dict[str, Any]
    timeout_seconds: float


class FormatContextPack(Protocol):
    def __call__(self, pack: dict[str, Any], limit: int = 8) -> str: ...


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


_client = _load_sibling_module("_fusion_memory_client")
_config = _load_sibling_module("_fusion_memory_config")
_format_context_pack = _cast(FormatContextPack, _client["format_context_pack"])
_format_error_result = _cast(FormatErrorResult, _client["format_error_result"])
_post_json = _cast(PostJson, _client["post_json"])
CONFIG = _cast(MemoryConfig, _config["CONFIG"])


async def memory_search(query: str, limit: int = 8) -> str:
    """Search Fusion Memory for raw evidence.

    Args:
        query: The search query.
        limit: Maximum number of evidence items to return.

    Returns:
        A formatted context pack string or an unavailable message.
    """
    try:
        limit = max(1, min(32, int(limit)))
        data = await _post_json(
            CONFIG.base_url,
            "/search",
            {
                "query": query,
                "scope": CONFIG.scope,
                "options": {"limit": limit, "allow_cross_session": CONFIG.allow_cross_session},
            },
            CONFIG.timeout_seconds,
        )
        return _format_context_pack(data, limit=limit)
    except Exception as exc:
        return _format_error_result(exc)
