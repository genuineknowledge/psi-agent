"""System prompt lifecycle — lazy build from workspace, optional rebuild."""

from __future__ import annotations

import hashlib
import inspect
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
from loguru import logger

if TYPE_CHECKING:
    from psi_agent.session.conversation import Conversation


class SystemPrompt:
    """Manages the system prompt lifecycle — lazy build, optional rebuild,
    and compaction.

    ``builder() → str`` is called to construct the system prompt.
    ``checker() → bool`` is called before every agent turn; returning
    ``True`` triggers an in-place rebuild.
    ``compaction_fn(history, complete_fn) → str`` summarises the
    conversation history when the token budget is exceeded.

    Defaults: if no builder is provided, an empty prompt is used.  If
    no checker is provided, the prompt is never rebuilt.  If no
    compaction_fn is provided, compaction is silently skipped.
    """

    @staticmethod
    async def _default_builder() -> str:
        return ""

    @staticmethod
    async def _default_checker() -> bool:
        return False

    def __init__(
        self,
        builder: Callable[..., Any] | None = None,
        checker: Callable[..., Any] | None = None,
        compaction_fn: Callable[..., Any] | None = None,
    ):
        self._builder = builder if builder is not None else self._default_builder
        self._checker = checker if checker is not None else self._default_checker
        self._compaction_fn = compaction_fn

    @property
    def compaction_fn(self) -> Callable[..., Any] | None:
        return self._compaction_fn

    @classmethod
    async def from_workspace(cls, workspace_path: Path, session_id: str) -> SystemPrompt:
        """Load the system module.  Defaults are used when builder, checker,
        or compaction_fn are not found in the workspace."""
        builder, checker, compaction_fn = await cls._load_module(workspace_path, session_id)
        return cls(builder=builder, checker=checker, compaction_fn=compaction_fn)

    async def ensure(self, conversation: Conversation) -> None:
        """Build or rebuild the system prompt if needed."""
        if not conversation.messages:
            try:
                sp = await self._builder()
                logger.info(f"System prompt loaded ({len(sp)} chars)")
                conversation.replace_system(sp)
            except Exception as e:
                logger.error(f"Failed to build system prompt: {e}")
        else:
            try:
                if await self._checker():
                    sp = await self._builder()
                    logger.info(f"System prompt rebuilt ({len(sp)} chars)")
                    conversation.replace_system(sp)
            except Exception as e:
                logger.error(f"Rebuild check or rebuild failed: {e}")

    # -- module loading --------------------------------------------------------

    @staticmethod
    async def _load_module(
        workspace_path: Path, session_id: str
    ) -> tuple[Callable[..., Any] | None, Callable[..., Any] | None, Callable[..., Any] | None]:
        """Import ``system_prompt_builder``, ``system_prompt_rebuild_checker``,
        and ``compact_history`` from ``workspace/systems/system.py``."""
        system_py = workspace_path / "systems" / "system.py"
        ap = anyio.Path(str(system_py))
        try:
            file_bytes = await ap.read_bytes()
        except OSError:
            logger.warning(f"No system.py found at {system_py}")
            return None, None, None

        file_hash = hashlib.sha256(file_bytes).hexdigest()
        module_name = f"psi_system_{session_id}_{file_hash}"

        try:
            source = file_bytes.decode("utf-8")
            compiled = compile(source, str(system_py), "exec")
        except Exception as e:
            logger.error(f"Failed to read or compile {system_py!r}: {e!r}")
            return None, None, None

        module = types.ModuleType(module_name)
        module.__file__ = str(system_py)
        sys.modules[module_name] = module
        try:
            exec(compiled, module.__dict__)
        except Exception as e:
            logger.error(f"Failed to execute system module {system_py!r}: {e!r}")
            sys.modules.pop(module_name, None)
            return None, None, None
        except BaseException:
            sys.modules.pop(module_name, None)
            raise

        try:
            builder = SystemPrompt._extract_async_func(module, "system_prompt_builder")
            checker = SystemPrompt._extract_async_func(module, "system_prompt_rebuild_checker")
            compaction_fn = SystemPrompt._extract_async_func(module, "compact_history")
        except Exception as e:
            logger.error(f"Failed to extract functions from {system_py!r}: {e!r}")
            sys.modules.pop(module_name, None)
            return None, None, None
        return builder, checker, compaction_fn

    @staticmethod
    def _extract_async_func(module: object, name: str) -> Callable[..., Any] | None:
        func = getattr(module, name, None)
        if func is None or not inspect.iscoroutinefunction(func):
            return None
        return func
