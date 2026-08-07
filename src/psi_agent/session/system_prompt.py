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

from psi_agent.session.exposure import check_skill_exposure, check_tool_exposure, enforce

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
        advertised_tools_fn: Callable[..., Any] | None = None,
        indexed_skills_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._builder: Callable[..., Any] = builder if builder is not None else self._default_builder
        self._checker: Callable[..., Any] = checker if checker is not None else self._default_checker
        self._compaction_fn: Callable[..., Any] | None = compaction_fn
        self._turn_context_fn: Callable[..., Any] | None = turn_context_fn
        self._before_turn: Callable[..., Any] = before_turn if before_turn is not None else self._default_before_turn
        self._after_turn: Callable[..., Any] = after_turn if after_turn is not None else self._default_after_turn
        self._before_turn_timeout_seconds = before_turn_timeout_seconds
        self._advertised_tools_fn: Callable[..., Any] | None = advertised_tools_fn
        self._indexed_skills_fn: Callable[..., Any] | None = indexed_skills_fn

    @property
    def compaction_fn(self) -> Callable[..., Any] | None:
        return self._compaction_fn

    @classmethod
    async def from_workspace(cls, workspace_path: Path, session_id: str) -> SystemPrompt:
        """Load the system module.  Defaults are used when builder, checker,
        compaction_fn, turn_context_builder, or lifecycle hooks are not found."""
        hooks = await cls._load_module(workspace_path, session_id)
        return cls(
            builder=hooks.get("system_prompt_builder"),
            checker=hooks.get("system_prompt_rebuild_checker"),
            compaction_fn=hooks.get("compact_history"),
            turn_context_fn=hooks.get("turn_context_builder"),
            before_turn=hooks.get("system_before_turn"),
            after_turn=hooks.get("system_after_turn"),
            advertised_tools_fn=hooks.get("advertised_tool_names"),
            indexed_skills_fn=hooks.get("indexed_skill_entries"),
        )

    async def check_exposure(self, *, registered: set[str], load_failures: dict[str, str] | None = None) -> None:
        """Assert the prompt side and the runtime agree, at Session startup.

        The workspace opts in by exposing ``advertised_tool_names()`` and/or
        ``indexed_skill_entries()``. A workspace that exposes neither is not
        checked — there is nothing to compare against, and refusing to start
        would break every workspace that predates these hooks.

        Called from ``SessionAgent.create``, before any socket is bound or task
        group opened, so raising here leaks nothing. Standalone
        (``psi-agent session``) the error propagates out of ``Session.run()`` and
        the process exits; under Gateway, ``SessionManager`` catches it per
        Session — that Session is logged at ERROR and dropped from the registry
        while its siblings keep running. Blast radius is deliberately one
        Session: a broken agent package should not take down unrelated ones, and
        the one it breaks must not go on serving a prompt that lies.

        Args:
            registered: Tool names ``ToolRegistry`` can actually dispatch.
            load_failures: File name → import error, from the registry.

        Raises:
            ExposureMismatchError: When the two sides disagree and the escape hatch
                (``PSI_ALLOW_EXPOSURE_MISMATCH``) is not set.
        """
        if self._advertised_tools_fn is None and self._indexed_skills_fn is None:
            logger.debug("Exposure check skipped: workspace exposes no advertise hooks")
            return

        problems: list[str] = []
        checked: list[str] = []

        advertised = await self._hook_result(self._advertised_tools_fn, "advertised_tool_names")
        if advertised is not None:
            problems += check_tool_exposure(
                {str(name) for name in advertised},
                registered,
                load_failures=load_failures,
            )
            checked.append(f"{len(registered)} tool(s)")

        entries = await self._hook_result(self._indexed_skills_fn, "indexed_skill_entries")
        if entries is not None:
            problems += await check_skill_exposure(entries)
            checked.append(f"{len(list(entries))} skill(s)")

        enforce(problems, context="Session startup")
        if checked:
            logger.info(f"Exposure check passed: {' and '.join(checked)} consistent")

    @staticmethod
    async def _hook_result(hook: Callable[..., Any] | None, name: str) -> list[Any] | None:
        """Call an exposure hook and return its entries, or ``None`` to skip it.

        A hook that is undefined, raises, or returns something that is not a list or
        tuple yields ``None`` and a WARNING. The check is a safety net; a broken net
        must not become a worse failure than the thing it looks for — and the caller
        must be able to tell "checked and agreed" from "could not check", so that the
        success log never claims a check that did not run.
        """
        if hook is None:
            return None
        try:
            result = await hook()
        except Exception as e:
            logger.warning(f"{name}() failed, skipping that half of the exposure check: {e!r}")
            return None
        if not isinstance(result, list | tuple):
            logger.warning(f"{name}() returned {type(result).__name__}, expected a sequence; skipping")
            return None
        return list(result)

    async def ensure(
        self,
        conversation: Conversation,
        user_message: dict[str, Any] | None = None,
        tool_names: list[str] | None = None,
    ) -> None:
        """Build or rebuild the system prompt.

        Two paths, in order of precedence:

        1. Empty history → build the whole prompt.
        2. ``checker()`` says yes → rebuild the whole prompt.

        Otherwise the prompt is left exactly as it was. Anything in it that
        describes **now** therefore stays frozen for the life of the history —
        which is why volatile content does not belong here at all, but in
        ``turn_context()``.

        *tool_names* is the registry's own list of dispatchable tool names. It is
        passed to builders that declare a ``tool_names`` parameter, so the prompt
        can name tools from the same source that executes them instead of
        re-deriving them from filenames. Builders that don't declare it are called
        exactly as before.
        """
        if not conversation.messages:
            try:
                sp = await self._call_builder(user_message, tool_names)
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
                sp = await self._call_builder(user_message, tool_names)
                logger.info(f"System prompt rebuilt ({len(sp)} chars)")
                conversation.replace_system(sp)
        except Exception as e:
            logger.error(f"Rebuild check or rebuild failed: {e}")

    async def _call_builder(self, user_message: dict[str, Any] | None, tool_names: list[str] | None) -> str:
        """Invoke the builder, passing only the arguments its signature declares."""
        kwargs: dict[str, Any] = {}
        if tool_names is not None and self._accepts_kwarg(self._builder, "tool_names"):
            kwargs["tool_names"] = tool_names
        if self._accepts_message(self._builder):
            return await self._builder(user_message, **kwargs)
        return await self._builder(**kwargs)

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

    HOOK_NAMES = (
        "system_prompt_builder",
        "system_prompt_rebuild_checker",
        "compact_history",
        "turn_context_builder",
        "system_before_turn",
        "system_after_turn",
        "advertised_tool_names",
        "indexed_skill_entries",
    )
    """Optional async functions read from ``workspace/systems/system.py``."""

    @staticmethod
    async def _load_module(workspace_path: Path, session_id: str) -> dict[str, Callable[..., Any]]:
        """Extract the optional async hooks from ``workspace/systems/system.py``.

        Returns a name → callable mapping containing only the hooks the workspace
        actually defines; an unreadable or broken ``system.py`` yields ``{}``.
        """
        system_py = workspace_path / "systems" / "system.py"
        ap = anyio.Path(str(system_py))
        try:
            file_bytes = await ap.read_bytes()
        except OSError:
            logger.warning(f"No system.py found at {system_py}")
            return {}

        file_hash = hashlib.sha256(file_bytes).hexdigest()
        module_name = f"psi_system_{session_id}_{file_hash}"

        try:
            source = file_bytes.decode("utf-8")
            compiled = compile(source, str(system_py), "exec")
        except Exception as e:
            logger.error(f"Failed to read or compile {system_py!r}: {e!r}")
            return {}

        module = types.ModuleType(module_name)
        module.__file__ = str(system_py)
        sys.modules[module_name] = module
        try:
            exec(compiled, module.__dict__)
        except Exception as e:
            logger.error(f"Failed to execute system module {system_py!r}: {e!r}")
            sys.modules.pop(module_name, None)
            return {}
        except BaseException:
            sys.modules.pop(module_name, None)
            raise

        try:
            hooks = {
                name: func
                for name in SystemPrompt.HOOK_NAMES
                if (func := SystemPrompt._extract_async_func(module, name)) is not None
            }
        except Exception as e:
            logger.error(f"Failed to extract functions from {system_py!r}: {e!r}")
            sys.modules.pop(module_name, None)
            return {}
        return hooks

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

    @staticmethod
    def _accepts_kwarg(func: Callable[..., Any], name: str) -> bool:
        """True when *func* declares keyword *name* (or ``**kwargs``).

        A builder that predates the argument keeps its old signature and is called
        without it, so adding one never breaks an existing workspace.
        """
        try:
            parameters = inspect.signature(func).parameters
        except TypeError, ValueError:
            return False
        if any(p.kind is p.VAR_KEYWORD for p in parameters.values()):
            return True
        parameter = parameters.get(name)
        return parameter is not None and parameter.kind in (
            parameter.KEYWORD_ONLY,
            parameter.POSITIONAL_OR_KEYWORD,
        )
