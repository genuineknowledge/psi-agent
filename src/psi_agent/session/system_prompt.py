"""System prompt lifecycle — lazy build from workspace, optional rebuild."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
from loguru import logger

if TYPE_CHECKING:
    from psi_agent.session.conversation import Conversation


class SystemPrompt:
    """Manages the system prompt lifecycle — lazy build and optional rebuild.

    ``builder() → str`` is called to construct the system prompt.
    ``checker() → bool`` is called before every agent turn; returning
    ``True`` triggers an in-place rebuild.

    Defaults: if no builder is provided, an empty prompt is used.  If
    no checker is provided, the prompt is never rebuilt.
    """

    @staticmethod
    async def _default_builder() -> str:
        return ""

    @staticmethod
    async def _default_checker() -> bool:
        return False

    def __init__(self, builder: Callable[..., Any] | None = None, checker: Callable[..., Any] | None = None):
        self._builder = builder if builder is not None else self._default_builder
        self._checker = checker if checker is not None else self._default_checker

    @classmethod
    async def from_workspace(cls, workspace_path: Path, session_id: str) -> SystemPrompt:
        """Load the system module.  Defaults are used when builder or checker
        are not found in the workspace."""
        builder, checker = await cls._load_module(workspace_path, session_id)
        return cls(builder=builder, checker=checker)

    async def ensure(self, conversation: Conversation) -> None:
        """Build or rebuild the system prompt if needed."""
        if not conversation.messages:
            try:
                sp = await self._builder()
                conversation.replace_system(sp)
                logger.info(f"System prompt loaded ({len(sp)} chars)")
            except Exception as e:
                logger.error(f"Failed to build system prompt: {e}")
        else:
            try:
                if await self._checker():
                    sp = await self._builder()
                    conversation.replace_system(sp)
                    logger.info(f"System prompt rebuilt ({len(sp)} chars)")
            except Exception as e:
                logger.error(f"Rebuild check or rebuild failed: {e}")

    # -- module loading --------------------------------------------------------

    @staticmethod
    async def _load_module(
        workspace_path: Path, session_id: str
    ) -> tuple[Callable[..., Any] | None, Callable[..., Any] | None]:
        """Import ``system_prompt_builder`` and ``system_prompt_rebuild_checker``
        from ``workspace/systems/system.py``."""
        system_py = workspace_path / "systems" / "system.py"
        ap = anyio.Path(str(system_py))
        try:
            file_bytes = await ap.read_bytes()
        except OSError:
            logger.warning(f"No system.py found at {system_py}")
            return None, None

        file_hash = hashlib.sha256(file_bytes).hexdigest()
        module_name = f"psi_system_{session_id}_{file_hash}"

        try:
            spec = importlib.util.spec_from_file_location(module_name, str(system_py))
            if spec is None or spec.loader is None:
                logger.warning(f"Could not load spec for {system_py}")
                return None, None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as e:
            logger.error(f"Failed to load {system_py}: {e}")
            sys.modules.pop(module_name, None)
            return None, None

        builder = SystemPrompt._extract_async_func(module, "system_prompt_builder")
        checker = SystemPrompt._extract_async_func(module, "system_prompt_rebuild_checker")
        return builder, checker

    @staticmethod
    def _extract_async_func(module: object, name: str) -> Callable[..., Any] | None:
        func = getattr(module, name, None)
        if func is None or not inspect.iscoroutinefunction(func):
            return None
        return func
