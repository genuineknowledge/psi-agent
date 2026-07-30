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
    ``after_turn(user_message, assistant_message)`` runs after a successful
    final assistant response has been committed.
    ``compaction_fn(history, complete_fn) → str`` summarises the
    conversation history when the token budget is exceeded.
    ``turn_context_fn() → str`` is called before every agent turn to render
    the *volatile* block for that turn (wall-clock time, runtime info). It goes
    to the tail of the request, not into the prompt — see ``turn_context``.

    Defaults: if no builder is provided, an empty prompt is used.  If
    no checker is provided, the prompt is never rebuilt.  If no
    compaction_fn is provided, compaction is silently skipped.  If no
    turn_context_fn is provided, no volatile block is injected.
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

    @staticmethod
    async def _default_before_turn(_user_message: dict[str, Any]) -> dict[str, Any]:
        return {}

    def __init__(
        self,
        builder: Callable[..., Any] | None = None,
        checker: Callable[..., Any] | None = None,
        compaction_fn: Callable[..., Any] | None = None,
        turn_context_fn: Callable[..., Any] | None = None,
        before_turn: Callable[..., Any] | None = None,
        after_turn: Callable[..., Any] | None = None,
        before_turn_timeout_seconds: float = 30.0,
    ) -> None:
        self._builder: Callable[..., Any] = builder if builder is not None else self._default_builder
        self._checker: Callable[..., Any] = checker if checker is not None else self._default_checker
        self._compaction_fn: Callable[..., Any] | None = compaction_fn
        self._turn_context_fn: Callable[..., Any] | None = turn_context_fn
        self._before_turn: Callable[..., Any] = before_turn if before_turn is not None else self._default_before_turn
        self._after_turn: Callable[..., Any] = after_turn if after_turn is not None else self._default_after_turn
        self._before_turn_timeout_seconds = before_turn_timeout_seconds

    @property
    def compaction_fn(self) -> Callable[..., Any] | None:
        return self._compaction_fn

    @classmethod
    async def from_workspace(cls, workspace_path: Path, session_id: str) -> SystemPrompt:
        """Load the system module.  Defaults are used when builder, checker,
        compaction_fn, turn_context_builder, or lifecycle hooks are not found."""
        builder, checker, compaction_fn, turn_context_fn, before_turn, after_turn = await cls._load_module(
            workspace_path, session_id
        )
        return cls(
            builder=builder,
            checker=checker,
            compaction_fn=compaction_fn,
            turn_context_fn=turn_context_fn,
            before_turn=before_turn,
            after_turn=after_turn,
        )

    async def ensure(self, conversation: Conversation, user_message: dict[str, Any] | None = None) -> None:
        """Build or rebuild the system prompt.

        Two paths, in order of precedence:

        1. Empty history → build the whole prompt.
        2. ``checker()`` says yes → rebuild the whole prompt.

        Otherwise the prompt is left exactly as it was. Anything in it that
        describes **now** therefore stays frozen for the life of the history —
        which is why volatile content does not belong here at all, but in
        ``turn_context()``.
        """
        if not conversation.messages:
            try:
                sp = (
                    await self._builder(user_message) if self._accepts_message(self._builder) else await self._builder()
                )
                logger.info(f"System prompt loaded ({len(sp)} chars)")
                conversation.replace_system(sp)
            except Exception as e:
                logger.error(f"Failed to build system prompt: {e}")
            return

        try:
            should_rebuild = (
                await self._checker(user_message) if self._accepts_message(self._checker) else await self._checker()
            )
            if should_rebuild:
                sp = (
                    await self._builder(user_message) if self._accepts_message(self._builder) else await self._builder()
                )
                logger.info(f"System prompt rebuilt ({len(sp)} chars)")
                conversation.replace_system(sp)
        except Exception as e:
            logger.error(f"Rebuild check or rebuild failed: {e}")

    async def run_after_turn(self, user_message: dict[str, Any], assistant_message: dict[str, Any]) -> None:
        """Run the optional recoverable workspace hook after a committed turn."""
        try:
            await self._after_turn(user_message, assistant_message)
            logger.debug("System after-turn hook completed")
        except Exception as e:
            logger.warning(f"System after-turn hook failed: {e!r}")

    async def turn_context(self) -> str:
        """Render this turn's volatile block, or ``""`` if the workspace has none.

        The prompt is built once and reused for the life of the history, which
        freezes everything in it that describes **now**: a Session opened on
        Monday kept telling users it was Monday all week, and a ``Time zone``
        label that was wrong at build time stayed wrong for as long as the
        Session lived.

        Re-rendering the prompt each turn would fix the clock at the cost of
        rebuilding it — a full workspace rescan, ~110ms and ~150KB for haitun —
        and it would permanently rule out prompt caching. Upstream caches by
        prefix, and the system prompt is the *front* of the request, so a prompt
        that changes every turn can never be cached however the cache is
        configured. (Caching is not enabled here today: Anthropic's is opt-in
        and nothing in ``src/`` sets ``cache_control``. Keeping the prefix
        stable is what makes enabling it possible later, not an optimization
        that is already paying off.)

        So the volatile block is not part of the prompt at all: it rides on the
        current turn's user message, at the **tail** of the request, where the
        change is confined to that one turn. The prompt and every earlier turn
        project byte-identically.

        A workspace opts in by exposing ``turn_context_builder()``; those that
        don't get no block. A builder that raises or returns a non-string is
        likewise treated as "no block", because losing a clock line is a far
        smaller problem than losing the turn.
        """
        if self._turn_context_fn is None:
            return ""
        try:
            block = await self._turn_context_fn()
        except Exception as e:
            logger.error(f"Turn context build failed: {e}")
            return ""
        if not isinstance(block, str) or not block.strip():
            return ""
        logger.info(f"Turn context built ({len(block)} chars)")
        return block

    async def run_before_turn(self, user_message: dict[str, Any]) -> dict[str, Any]:
        """Run the optional bounded workspace hook before an agent turn."""
        try:
            with anyio.fail_after(self._before_turn_timeout_seconds):
                result = await self._before_turn(user_message)
        except TimeoutError:
            logger.warning(f"System before-turn hook timed out after {self._before_turn_timeout_seconds:.1f}s")
            return {}
        except Exception as e:
            logger.warning(f"System before-turn hook failed: {e!r}")
            return {}
        if not isinstance(result, dict):
            logger.warning(f"System before-turn hook returned {type(result).__name__}, expected dict")
            return {}
        logger.debug("System before-turn hook completed")
        return result

    # -- module loading --------------------------------------------------------

    @staticmethod
    async def _load_module(
        workspace_path: Path, session_id: str
    ) -> tuple[
        Callable[..., Any] | None,
        Callable[..., Any] | None,
        Callable[..., Any] | None,
        Callable[..., Any] | None,
        Callable[..., Any] | None,
        Callable[..., Any] | None,
    ]:
        """Import ``system_prompt_builder``, ``system_prompt_rebuild_checker``,
        ``compact_history``, and ``turn_context_builder`` from
        ``workspace/systems/system.py``."""
        system_py = workspace_path / "systems" / "system.py"
        ap = anyio.Path(str(system_py))
        try:
            file_bytes = await ap.read_bytes()
        except OSError:
            logger.warning(f"No system.py found at {system_py}")
            return None, None, None, None, None, None

        file_hash = hashlib.sha256(file_bytes).hexdigest()
        module_name = f"psi_system_{session_id}_{file_hash}"

        try:
            source = file_bytes.decode("utf-8")
            compiled = compile(source, str(system_py), "exec")
        except Exception as e:
            logger.error(f"Failed to read or compile {system_py!r}: {e!r}")
            return None, None, None, None, None, None

        module = types.ModuleType(module_name)
        module.__file__ = str(system_py)
        sys.modules[module_name] = module
        try:
            exec(compiled, module.__dict__)
        except Exception as e:
            logger.error(f"Failed to execute system module {system_py!r}: {e!r}")
            sys.modules.pop(module_name, None)
            return None, None, None, None, None, None
        except BaseException:
            sys.modules.pop(module_name, None)
            raise

        try:
            builder = SystemPrompt._extract_async_func(module, "system_prompt_builder")
            checker = SystemPrompt._extract_async_func(module, "system_prompt_rebuild_checker")
            compaction_fn = SystemPrompt._extract_async_func(module, "compact_history")
            turn_context_fn = SystemPrompt._extract_async_func(module, "turn_context_builder")
            before_turn = SystemPrompt._extract_async_func(module, "system_before_turn")
            after_turn = SystemPrompt._extract_async_func(module, "system_after_turn")
        except Exception as e:
            logger.error(f"Failed to extract functions from {system_py!r}: {e!r}")
            sys.modules.pop(module_name, None)
            return None, None, None, None, None, None
        return builder, checker, compaction_fn, turn_context_fn, before_turn, after_turn

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
