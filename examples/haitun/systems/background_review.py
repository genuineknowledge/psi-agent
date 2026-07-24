"""Background review - async post-turn learning for hermes-style-workspace.

After each user turn, ``BackgroundReview.maybe_spawn()`` checks one counter:
- skill review: fires when the current turn had >= 10 tool calls

Reviews run as isolated asyncio Tasks using a mini-ReAct loop driven by
``complete_fn``.  All exceptions are swallowed and logged at DEBUG level so
review failures never affect the main session.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio

try:
    from systems.curator import maybe_run_curator
except ModuleNotFoundError:
    _CURATOR_PATH = Path(__file__).with_name("curator.py")
    _CURATOR_SPEC = importlib.util.spec_from_file_location("hermes_curator", _CURATOR_PATH)
    if _CURATOR_SPEC is None or _CURATOR_SPEC.loader is None:
        raise
    _CURATOR_MODULE = importlib.util.module_from_spec(_CURATOR_SPEC)
    _CURATOR_SPEC.loader.exec_module(_CURATOR_MODULE)
    maybe_run_curator = _CURATOR_MODULE.maybe_run_curator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_REVIEW_ITERATIONS = 10

_SKILL_REVIEW_PROMPT = (
    "Review the conversation above and update the skill library. Be "
    "ACTIVE - most sessions produce at least one skill update, even if "
    "small. A pass that does nothing is a missed learning opportunity, "
    "not a neutral outcome.\n\n"
    "Target shape of the library: CLASS-LEVEL skills, each with a rich "
    "SKILL.md. Not a long flat list of narrow one-session-one-skill entries.\n\n"
    "Signals to look for (any one of these warrants action):\n"
    "  - User corrected your style, tone, format, legibility, or verbosity. "
    "Frustration signals like 'stop doing X', 'this is too verbose', "
    "'don't format like this', or 'just give me the answer' are FIRST-CLASS "
    "skill signals. Update the relevant skill(s) to embed the preference.\n"
    "  - User corrected your workflow, approach, or sequence of steps.\n"
    "  - Non-trivial technique, fix, workaround, debugging path, or "
    "tool-usage pattern emerged that a future session would benefit from.\n"
    "  - A skill consulted this session turned out to be wrong, missing a "
    "step, or outdated. Patch it NOW.\n\n"
    "Preference order:\n"
    "  1. PATCH a currently-loaded skill if it covers the new learning.\n"
    "  2. PATCH an existing umbrella skill (use skill_manage action=list + view).\n"
    "  3. CREATE a new class-level umbrella skill when nothing exists.\n"
    "     Name at the class level - NOT a PR number, error string, or "
    "'fix-X / debug-Y' session artifact.\n\n"
    "Do NOT capture environment-dependent failures, missing binaries, or "
    "transient errors that resolved before the conversation ended.\n\n"
    "'Nothing to save.' is a real option but should NOT be the default."
)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

CompleteFn = Callable[[list[dict[str, Any]], list[dict[str, Any]]], Awaitable[dict[str, Any]]]
ToolExecutors = dict[str, Callable[..., Awaitable[str]]]


# ---------------------------------------------------------------------------
# BackgroundReview class
# ---------------------------------------------------------------------------


class BackgroundReview:
    """Post-turn learning engine for hermes-style-workspace.

    Maintains a tool-call counter and spawns isolated async review
    tasks to write skills without blocking the main session.

    On initialisation, spawns a ``maybe_run_curator()`` task with
    ``idle_for_seconds=inf`` (startup = fully idle), matching hermes-agent
    behaviour.

    Attributes:
        SKILL_TOOL_THRESHOLD: Minimum tool calls in a turn to trigger skill review.
    """

    SKILL_TOOL_THRESHOLD: int = 10

    def __init__(
        self,
        complete_fn: CompleteFn,
        tool_executors: ToolExecutors | None = None,
        workspace_dir: anyio.Path | str | None = None,
    ) -> None:
        """Initialise BackgroundReview.

        Args:
            complete_fn: Async function that calls the LLM.
                Signature: ``async (messages, tools) -> response_dict``
                where response_dict follows OpenAI chat completion format.
            tool_executors: Mapping of tool name -> async callable.
                Only tools in this map AND in the per-review whitelist will
                be executed. Defaults to empty dict (no tools available).
            workspace_dir: Workspace root path. When provided, a curator check
                is spawned on startup (idle_for_seconds=inf).
        """
        self._complete_fn = complete_fn
        self._tool_executors: ToolExecutors = tool_executors or {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._turn_count: int = 0
        self._workspace_dir: anyio.Path | None = anyio.Path(str(workspace_dir)) if workspace_dir is not None else None
        # Startup curator check - runs after event loop is available
        if self._workspace_dir is not None:
            task = asyncio.create_task(
                self._startup_curator_check(),
                name="startup-curator-check",
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def _startup_curator_check(self) -> None:
        """Spawn curator check on startup with idle_for_seconds=inf."""
        try:
            workspace_dir = self._workspace_dir
            if workspace_dir is None:
                return

            await maybe_run_curator(
                workspace_dir,
                self._simple_complete_fn(),
                idle_for_seconds=float("inf"),
            )
        except Exception as exc:
            logger.debug("BackgroundReview: startup curator check failed: %s", exc)

    def _simple_complete_fn(self) -> Any:
        """Build a simple complete_fn adapter for curator (messages -> str).

        Returns:
            Async callable compatible with curator's CompleteFn signature.
        """

        async def _complete(messages: list[dict[str, Any]]) -> str:
            response = await self._complete_fn(messages, [])
            choices = response.get("choices") or []
            if not choices:
                return ""
            return choices[0].get("message", {}).get("content", "") or ""

        return _complete

    def increment_turn(self) -> None:
        """Increment the user-turn counter.

        Call once per completed user turn.
        """
        self._turn_count += 1

    async def maybe_spawn(
        self,
        messages_snapshot: list[dict[str, Any]],
        tool_call_count: int = 0,
    ) -> None:
        """Check counters and spawn a background review task if triggered.

        Must be called AFTER ``increment_turn()`` for the current turn.

        Args:
            messages_snapshot: Full conversation history up to and including
                the current turn's assistant reply. Will be deep-copied.
            tool_call_count: Number of tool calls made during the current turn.
        """
        do_skills = tool_call_count >= self.SKILL_TOOL_THRESHOLD

        if not do_skills:
            return

        snapshot = copy.deepcopy(messages_snapshot)

        prompt = _SKILL_REVIEW_PROMPT
        allowed = {"skill_manage"}
        label = "skill"

        logger.debug(
            "BackgroundReview: spawning %s review (turn=%d, tool_calls=%d)",
            label,
            self._turn_count,
            tool_call_count,
        )
        task = asyncio.create_task(
            self._run_review(snapshot, prompt, allowed),
            name=f"background-review-{label}-turn{self._turn_count}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run_review(
        self,
        messages: list[dict[str, Any]],
        prompt: str,
        allowed_tools: set[str],
    ) -> None:
        """Execute a mini-ReAct loop for the review agent.

        Args:
            messages: Conversation snapshot (already deep-copied).
            prompt: Review instruction appended as final user message.
            allowed_tools: Set of tool names the review agent may call.
        """
        try:
            await self._mini_react(messages, prompt, allowed_tools)
        except Exception as exc:
            logger.debug("BackgroundReview: review task failed: %s", exc, exc_info=True)

    async def _mini_react(
        self,
        messages: list[dict[str, Any]],
        prompt: str,
        allowed_tools: set[str],
    ) -> None:
        """Run the mini-ReAct loop.

        Args:
            messages: Base conversation messages.
            prompt: Final user message injected as review instruction.
            allowed_tools: Whitelist of permitted tool names.
        """
        # Build tool schemas for whitelisted tools only
        tool_schemas = _build_tool_schemas(allowed_tools)

        # Append review prompt as final user message
        loop_messages = [*messages, {"role": "user", "content": prompt}]

        for iteration in range(MAX_REVIEW_ITERATIONS):
            try:
                response = await self._complete_fn(loop_messages, tool_schemas)
            except Exception as exc:
                logger.debug("BackgroundReview: LLM call failed at iteration %d: %s", iteration, exc)
                return

            # Extract assistant message
            choices = response.get("choices") or []
            if not choices:
                logger.debug("BackgroundReview: empty choices at iteration %d", iteration)
                return
            assistant_msg = choices[0].get("message", {})
            loop_messages.append(assistant_msg)

            # Check for tool calls
            tool_calls = assistant_msg.get("tool_calls") or []
            if not tool_calls:
                # No tool call -> review complete
                logger.debug("BackgroundReview: review finished at iteration %d (no tool call)", iteration)
                return

            # Execute each tool call
            for tc in tool_calls:
                tc_id = tc.get("id", "")
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                args_raw = func.get("arguments", "{}")

                if tool_name not in allowed_tools:
                    result = (
                        f"Tool '{tool_name}' is not available in this review context. "
                        f"Only {sorted(allowed_tools)} are permitted."
                    )
                    logger.debug("BackgroundReview: blocked tool call '%s' (not in whitelist)", tool_name)
                else:
                    executor = self._tool_executors.get(tool_name)
                    if executor is None:
                        result = f"Tool '{tool_name}' is not registered in tool_executors."
                    else:
                        try:
                            args = json.loads(args_raw) if args_raw else {}
                            result = await executor(**args)
                        except Exception as exc:
                            result = f"Tool '{tool_name}' raised an error: {exc}"
                            logger.debug("BackgroundReview: tool '%s' error: %s", tool_name, exc)

                loop_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": str(result),
                    }
                )

        logger.debug("BackgroundReview: reached MAX_REVIEW_ITERATIONS (%d), stopping", MAX_REVIEW_ITERATIONS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_tool_schemas(allowed_tools: set[str]) -> list[dict[str, Any]]:
    """Build minimal OpenAI-format tool schemas for the allowed tools.

    Args:
        allowed_tools: Set of tool names to build schemas for.

    Returns:
        List of tool schema dicts in OpenAI function-call format.
    """
    schemas: list[dict[str, Any]] = []

    if "skill_manage" in allowed_tools:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "skill_manage",
                    "description": "Create, patch, view, or list workspace skills.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["create", "patch", "view", "list"],
                                "description": "Operation to perform.",
                            },
                            "skill_name": {
                                "type": "string",
                                "description": "Skill directory name (kebab-case).",
                            },
                            "content": {
                                "type": "string",
                                "description": "Skill body content (create/patch).",
                            },
                            "category": {
                                "type": "string",
                                "description": "Skill category (create only).",
                            },
                            "description": {
                                "type": "string",
                                "description": "Short skill description (create only).",
                            },
                        },
                        "required": ["action"],
                    },
                },
            }
        )

    return schemas
