from __future__ import annotations

import hashlib
import inspect
import json
import os
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
    history_messages = _find_history_messages_from_stack()
    history_path = _host_history_path(session_id)
    if history_messages:
        _write_history_snapshot(history_path, history_messages)
    return SimpleNamespace(
        session_id=session_id,
        workspace_path=_WORKSPACE_ROOT,
        history_path=history_path,
        workspace_history_path=_WORKSPACE_ROOT / "histories" / f"{session_id}.jsonl",
        history_messages=history_messages,
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


def _find_history_messages_from_stack() -> list[dict[str, str]]:
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame is not None else None
        while frame is not None:
            for value in frame.f_locals.values():
                messages = _extract_history_messages(value)
                if messages:
                    return messages
            frame = frame.f_back
    finally:
        del frame
    return []


def _extract_history_messages(value: Any) -> list[dict[str, str]]:
    if isinstance(value, list):
        return _normalize_history_messages(value)
    if isinstance(value, dict):
        return _normalize_history_messages(value.get("messages"))
    conversation = getattr(value, "_conversation", None)
    messages = getattr(conversation, "messages", None)
    normalized = _normalize_history_messages(messages)
    if normalized:
        return normalized
    return _normalize_history_messages(getattr(value, "messages", None))


def _normalize_history_messages(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    messages: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("speaker") or "").strip()
        content = _message_content(item.get("content"))
        if role and content:
            messages.append({"role": role, "content": content})
    return messages


def _message_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    return str(value).strip()


def _host_history_path(session_id: str) -> Path:
    root = os.environ.get("DOLPHIN_FUSION_GUARD_HISTORY_DIR", "").strip()
    base = Path(root).expanduser() if root else Path.home() / ".dolphin" / "security" / "fusion-guard-history"
    workspace_key = hashlib.sha256(str(_WORKSPACE_ROOT).encode("utf-8")).hexdigest()[:16]
    return base / workspace_key / f"{session_id}.jsonl"


def _write_history_snapshot(path: Path, messages: list[dict[str, str]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(json.dumps(message, ensure_ascii=False) for message in messages) + "\n"
        tmp_path = path.with_suffix(".jsonl.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
    except OSError:
        return
