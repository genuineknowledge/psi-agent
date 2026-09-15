# ruff: noqa
"""宿主适配边界。PSI 路径只有显式 PSI capability 才可解析。"""

from __future__ import annotations
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, Mapping
from contextlib import contextmanager
from typing import Iterator

import os


class HostKind(StrEnum):
    PSI = "psi-agent"
    CODEX = "codex"
    OPENCLAW = "openclaw"
    HERMES = "hermes"
    EXTERNAL = "external"


@dataclass(frozen=True)
class AgentContext:
    kind: HostKind
    instance_id: str | None = None


@dataclass(frozen=True)
class WorkflowPaths:
    workspace: Path
    tools_dir: Path
    state_dir: Path


class PsiPathProvider:
    def __init__(self, context: AgentContext, environ: Mapping[str, str] | None = None):
        self.context = context
        self.environ = environ if environ is not None else os.environ

    def _check(self) -> None:
        if self.context.kind is not HostKind.PSI:
            raise PermissionError("PSI workflow paths are available only to psi-agent")

    def _get(self, name: str) -> Path | None:
        self._check()
        value = self.environ.get(name, "").strip()
        return Path(value).expanduser() if value else None

    def workflow_workspace(self) -> Path | None:
        return self._get("PSI_WORKFLOW_WORKSPACE")

    def workflow_tools_dir(self) -> Path | None:
        return self._get("PSI_WORKFLOW_TOOLS_DIR")

    def workflow_state_dir(self) -> Path | None:
        return self._get("PSI_WORKFLOW_STATE_DIR")


class HostAdapter:
    def __init__(
        self, context: AgentContext, *, project_root: Path | None = None, psi_paths: PsiPathProvider | None = None
    ):
        self.context = context
        self.project_root = project_root or Path.cwd()
        self.psi_paths = psi_paths

    def workflow_paths(self, defaults: WorkflowPaths) -> WorkflowPaths:
        if self.context.kind is HostKind.PSI:
            provider = self.psi_paths or PsiPathProvider(self.context)
            return WorkflowPaths(
                provider.workflow_workspace() or defaults.workspace,
                provider.workflow_tools_dir() or defaults.tools_dir,
                provider.workflow_state_dir() or defaults.state_dir,
            )
        if self.context.kind is HostKind.CODEX:
            root = Path(os.environ.get("CODEX_WORKSPACE") or self.project_root)
            return WorkflowPaths(
                root,
                Path(os.environ.get("CODEX_TOOLS_DIR") or root / ".agents" / "skills"),
                root / ".codex" / "workflow",
            )
        if self.context.kind is HostKind.OPENCLAW:
            root = Path(os.environ.get("OPENCLAW_WORKSPACE") or self.project_root)
            return WorkflowPaths(
                root, Path(os.environ.get("OPENCLAW_TOOLS_DIR") or root / "skills"), root / ".openclaw" / "workflow"
            )
        if self.context.kind is HostKind.HERMES:
            root = Path(os.environ.get("HERMES_WORKSPACE") or self.project_root)
            return WorkflowPaths(
                root, Path(os.environ.get("HERMES_TOOLS_DIR") or root / "skills"), root / ".hermes" / "workflow"
            )
        return defaults


WORKSPACE_ENV = "PSI_WORKFLOW_WORKSPACE"
TOOLS_ENV = "PSI_WORKFLOW_TOOLS_DIR"
STATE_ENV = "PSI_WORKFLOW_STATE_DIR"
_ai_socket_provider: ContextVar[Callable[[], str | None] | None] = ContextVar(
    "psi_workflow_ai_socket_provider", default=None
)
_agent_factory: ContextVar[Callable[[object], object] | None] = ContextVar("psi_workflow_agent_factory", default=None)


def workspace_dir(default: Path) -> Path:
    return Path(os.environ.get(WORKSPACE_ENV) or default).expanduser()


def tools_dir(default: Path) -> Path:
    return Path(os.environ.get(TOOLS_ENV) or default).expanduser()


def state_dir(default: Path) -> Path:
    return Path(os.environ.get(STATE_ENV) or default).expanduser()


def set_ai_socket_provider(provider):
    """Set provider for this async context and return a reset token."""
    return _ai_socket_provider.set(provider)


def reset_ai_socket_provider(token) -> None:
    _ai_socket_provider.reset(token)


@contextmanager
def ai_socket_provider(provider) -> Iterator[None]:
    token = set_ai_socket_provider(provider)
    try:
        yield
    finally:
        reset_ai_socket_provider(token)


def ai_socket(default_provider):
    provider = _ai_socket_provider.get()
    value = provider() if provider is not None else None
    return value if value is not None else default_provider()


def set_agent_factory(factory):
    _agent_factory.set(factory)


def agent_handle(config, default_factory):
    return (_agent_factory.get() or default_factory)(config)
