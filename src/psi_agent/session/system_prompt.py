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
    """Manages the system prompt lifecycle — lazy build and optional rebuild.

    ``builder() → str`` is called to construct the system prompt.
    ``checker() → bool`` is called before every agent turn; returning
    ``True`` triggers an in-place rebuild.
    ``after_turn(user_message, assistant_message)`` is called after a
    successful final assistant response has been committed.

    Defaults: if no builder is provided, an empty prompt is used.  If
    no checker is provided, the prompt is never rebuilt.
    """

    @staticmethod
    async def _default_builder() -> str:
        return ""

    @staticmethod
    async def _default_checker() -> bool:
        return False

    @staticmethod
    async def _default_after_turn(_user_message: dict[str, Any], _assistant_message: dict[str, Any]) -> None:
        return None

    def __init__(
        self,
        builder: Callable[..., Any] | None = None,
        checker: Callable[..., Any] | None = None,
        after_turn: Callable[..., Any] | None = None,
    ) -> None:
        self._builder: Callable[..., Any] = builder if builder is not None else self._default_builder
        self._checker: Callable[..., Any] = checker if checker is not None else self._default_checker
        self._after_turn: Callable[..., Any] = after_turn if after_turn is not None else self._default_after_turn

    @classmethod
    async def from_workspace(cls, workspace_path: Path, session_id: str) -> SystemPrompt:
        """Load the system module.  Defaults are used when builder or checker
        are not found in the workspace."""
        builder, checker, after_turn = await cls._load_module(workspace_path, session_id)
        return cls(builder=builder, checker=checker, after_turn=after_turn)

    async def ensure(self, conversation: Conversation, user_message: dict[str, Any] | None = None) -> None:
        """Build or rebuild the system prompt if needed."""
        if not conversation.messages:
            try:
                sp = (
                    await self._builder(user_message) if self._accepts_message(self._builder) else await self._builder()
                )
                logger.info(f"System prompt loaded ({len(sp)} chars)")
                conversation.replace_system(sp)
            except Exception as e:
                logger.error(f"Failed to build system prompt: {e}")
        else:
            try:
                should_rebuild = (
                    await self._checker(user_message) if self._accepts_message(self._checker) else await self._checker()
                )
                if should_rebuild:
                    sp = (
                        await self._builder(user_message)
                        if self._accepts_message(self._builder)
                        else await self._builder()
                    )
                    logger.info(f"System prompt rebuilt ({len(sp)} chars)")
                    conversation.replace_system(sp)
            except Exception as e:
                logger.error(f"Rebuild check or rebuild failed: {e}")

    async def run_after_turn(self, user_message: dict[str, Any], assistant_message: dict[str, Any]) -> None:
        """Run the optional workspace hook after a successful turn.

        Hook failures are recoverable workspace errors: the assistant response
        is already committed and must not be rolled back or hidden from the
        channel.
        """
        try:
            await self._after_turn(user_message, assistant_message)
            logger.debug("System after-turn hook completed")
        except Exception as e:
            logger.warning(f"System after-turn hook failed: {e!r}")

    # -- module loading --------------------------------------------------------

    @staticmethod
    async def _load_module(
        workspace_path: Path, session_id: str
    ) -> tuple[Callable[..., Any] | None, Callable[..., Any] | None, Callable[..., Any] | None]:
        """Load the supported hooks from ``workspace/systems/system.py``."""
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
            after_turn = SystemPrompt._extract_async_func(module, "system_after_turn")
        except Exception as e:
            logger.error(f"Failed to extract functions from {system_py!r}: {e!r}")
            sys.modules.pop(module_name, None)
            return None, None, None
        return builder, checker, after_turn

    @staticmethod
    def _extract_async_func(module: object, name: str) -> Callable[..., Any] | None:
        func = getattr(module, name, None)
        if func is None or not inspect.iscoroutinefunction(func):
            return None
        return func

    @staticmethod
    def _accepts_message(func: Callable[..., Any]) -> bool:
        parameters = inspect.signature(func).parameters.values()
        return any(
            parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD, parameter.VAR_POSITIONAL)
            for parameter in parameters
        )
