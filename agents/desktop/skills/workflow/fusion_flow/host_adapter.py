# ruff: noqa
"""宿主集成边界：为非 Haitun agent 提供可注入的运行时入口。"""

from __future__ import annotations
from contextvars import ContextVar
from pathlib import Path
from typing import Callable
import os

WORKSPACE_ENV = "PSI_WORKFLOW_WORKSPACE"
TOOLS_ENV = "PSI_WORKFLOW_TOOLS_DIR"
_ai_socket_provider: ContextVar[Callable[[], str | None] | None] = ContextVar(
    "psi_workflow_ai_socket_provider", default=None
)


def workspace_dir(default: Path) -> Path:
    value = os.environ.get(WORKSPACE_ENV, "").strip()
    return Path(value).expanduser() if value else default


def tools_dir(default: Path) -> Path:
    value = os.environ.get(TOOLS_ENV, "").strip()
    return Path(value).expanduser() if value else default


def set_ai_socket_provider(provider: Callable[[], str | None] | None) -> None:
    """为当前上下文设置 session 请求提供器；传 None 恢复宿主默认行为。"""
    _ai_socket_provider.set(provider)


def ai_socket(default_provider: Callable[[], str | None]) -> str | None:
    provider = _ai_socket_provider.get()
    return (provider or default_provider)()
