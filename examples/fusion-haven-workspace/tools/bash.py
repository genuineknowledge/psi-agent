from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(_WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE_ROOT))

from fusion_guard_security.runner import secure_bash as _secure_bash  # noqa: E402


async def bash(command: str, cwd: str | None = None) -> str:
    """Execute a bash command through the Fusion-Guard safety adapter.

    Args:
        command: The bash command to execute.
        cwd: Working directory. Defaults to the current Dolphin workspace.
    """
    return await _secure_bash(command, cwd=cwd or str(_WORKSPACE_ROOT), context_override=_build_dolphin_context())


def _build_dolphin_context() -> SimpleNamespace:
    session_id = _infer_session_id(__name__)
    return SimpleNamespace(
        session_id=session_id,
        workspace_path=_WORKSPACE_ROOT,
        history_path=_WORKSPACE_ROOT / "histories" / f"{session_id}.jsonl",
        ai_socket=_find_ai_socket_from_stack(),
    )


def _infer_session_id(module_name: str) -> str:
    prefix = "psi_tool_bash_"
    hash_suffix_len = 64
    separator_len = 1
    if module_name.startswith(prefix) and len(module_name) > len(prefix) + hash_suffix_len + separator_len:
        session_and_hash = module_name[len(prefix) :]
        separator_index = len(session_and_hash) - hash_suffix_len - separator_len
        if session_and_hash[separator_index] == "_":
            session_id = session_and_hash[:separator_index]
            if session_id:
                return session_id
    return "default"


def _find_ai_socket_from_stack() -> str:
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame is not None else None
        while frame is not None:
            for value in frame.f_locals.values():
                ai_socket = _extract_ai_socket(value)
                if ai_socket:
                    return ai_socket
            frame = frame.f_back
    finally:
        del frame
    return ""


def _extract_ai_socket(value: Any) -> str:
    ai_socket = getattr(value, "ai_socket", None)
    if isinstance(ai_socket, str) and ai_socket:
        return ai_socket
    ai_client = getattr(value, "_ai_client", None)
    ai_socket = getattr(ai_client, "ai_socket", None)
    if isinstance(ai_socket, str) and ai_socket:
        return ai_socket
    return ""
