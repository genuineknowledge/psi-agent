from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from psi_agent.session.conversation import Conversation


class SystemPrompt:
    """Manages the system prompt lifecycle — lazy build and optional rebuild.

    *First call*: build the prompt and append it to the conversation.
    *Subsequent calls*: if a rebuild checker exists and returns True,
    replace the existing system message in-place.
    """

    def __init__(self, builder: Callable[..., Any] | None = None, checker: Callable[..., Any] | None = None):
        self._builder = builder
        self._checker = checker

    @classmethod
    def from_workspace(cls, workspace_path: Path, session_id: str) -> SystemPrompt | None:
        """Try to load the system module.  Returns None if no builder is found."""
        builder, checker = cls._load_module(workspace_path, session_id)
        if builder is None:
            return None
        return cls(builder=builder, checker=checker)

    async def ensure(self, conversation: Conversation) -> None:
        """Build or rebuild the system prompt if needed."""
        if self._builder is None:
            return

        if not conversation.messages:
            await self._build(conversation)
        elif self._checker is not None:
            try:
                if await self._checker():
                    logger.info("Rebuild checker returned True — rebuilding system prompt")
                    assert self._builder is not None
                    conversation.replace_system(await self._builder())
            except Exception as e:
                logger.error(f"Rebuild check or rebuild failed: {e}")

    async def _build(self, conversation: Conversation) -> None:
        assert self._builder is not None
        try:
            sp = await self._builder()
            conversation.add({"role": "system", "content": sp})
            logger.info(f"System prompt loaded ({len(sp) if sp else 0} chars)")
        except Exception as e:
            logger.error(f"Failed to build system prompt: {e}")

    # -- module loading --------------------------------------------------------

    @staticmethod
    def _load_module(
        workspace_path: Path, session_id: str
    ) -> tuple[Callable[..., Any] | None, Callable[..., Any] | None]:
        """Import ``system_prompt_builder`` and ``system_prompt_rebuild_checker``
        from ``workspace/systems/system.py``."""
        system_py = workspace_path / "systems" / "system.py"
        try:
            file_bytes = system_py.read_bytes()
        except OSError:
            logger.warning(f"No system.py found at {system_py}")
            return None, None

        file_hash = hashlib.sha256(file_bytes).hexdigest()
        module_name = f"psi_system_{session_id}_{file_hash[:12]}"

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

        builder = _extract_async_func(module, "system_prompt_builder")
        checker = _extract_async_func(module, "system_prompt_rebuild_checker")
        return builder, checker


def _extract_async_func(module: object, name: str) -> Callable[..., Any] | None:
    func = getattr(module, name, None)
    if func is None or not inspect.iscoroutinefunction(func):
        return None
    return func
