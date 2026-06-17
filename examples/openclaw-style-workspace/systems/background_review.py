"""Background review — async post-turn learning for hermes-style-workspace.

After each user turn, ``BackgroundReview.maybe_spawn()`` checks two counters:
- memory review: fires every 10 user turns
- skill review: fires when the current turn had >= 10 tool calls
- combined: fires when both conditions are true simultaneously

Reviews run as isolated asyncio Tasks using a mini-ReAct loop driven by
``complete_fn``.  All exceptions are swallowed and logged at DEBUG level so
review failures never affect the main session.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_REVIEW_ITERATIONS = 10

# Minimum number of flow primitives (agent/parallel/pipeline/phase calls) in a
# .flow.ts file for it to qualify for background flow review.
FLOW_PRIMITIVE_THRESHOLD = 3


def _count_flow_primitives(flow_ts_path: str) -> int:
    """Count flow primitive calls in a .flow.ts file.

    Counts occurrences of agent(), parallel(), pipeline(), and phase() calls
    as a proxy for flow complexity.

    Args:
        flow_ts_path: Path to the .flow.ts file.

    Returns:
        Number of primitive call sites found.
    """
    import re

    try:
        text = open(flow_ts_path).read()
    except OSError:
        return 0
    return len(re.findall(r"\b(agent|parallel|pipeline|phase|session|pmap)\s*\(", text))

_MEMORY_REVIEW_PROMPT = (
    "Review the conversation above and consider saving to memory if appropriate.\n\n"
    "Focus on:\n"
    "1. Has the user revealed things about themselves — their persona, desires, "
    "preferences, or personal details worth remembering?\n"
    "2. Has the user expressed expectations about how you should behave, their work "
    "style, or ways they want you to operate?\n\n"
    "If something stands out, save it using the memory tool. "
    "If nothing is worth saving, just say 'Nothing to save.' and stop."
)

_SKILL_REVIEW_PROMPT = (
    "Review the conversation above and update the skill library. Be "
    "ACTIVE — most sessions produce at least one skill update, even if "
    "small. A pass that does nothing is a missed learning opportunity, "
    "not a neutral outcome.\n\n"
    "Target shape of the library: CLASS-LEVEL skills, each with a rich "
    "SKILL.md. Not a long flat list of narrow one-session-one-skill entries.\n\n"
    "Signals to look for (any one of these warrants action):\n"
    "  • User corrected your style, tone, format, legibility, or verbosity. "
    "Frustration signals like 'stop doing X', 'this is too verbose', "
    "'don't format like this', or 'just give me the answer' are FIRST-CLASS "
    "skill signals. Update the relevant skill(s) to embed the preference.\n"
    "  • User corrected your workflow, approach, or sequence of steps.\n"
    "  • Non-trivial technique, fix, workaround, debugging path, or "
    "tool-usage pattern emerged that a future session would benefit from.\n"
    "  • A skill consulted this session turned out to be wrong, missing a "
    "step, or outdated. Patch it NOW.\n\n"
    "Preference order:\n"
    "  1. PATCH a currently-loaded skill if it covers the new learning.\n"
    "  2. PATCH an existing umbrella skill (use skill_manage action=list + view).\n"
    "  3. CREATE a new class-level umbrella skill when nothing exists.\n"
    "     Name at the class level — NOT a PR number, error string, or "
    "'fix-X / debug-Y' session artifact.\n\n"
    "IMPORTANT constraints:\n"
    "  • Only PATCH or CREATE skills with `created_by: agent` in their frontmatter.\n"
    "  • Skills WITHOUT `created_by: agent` (user-authored skills like fusion-flow) "
    "are READ-ONLY — you may view them but must NOT patch or overwrite them.\n"
    "  • Before patching, always call skill_manage(action='view') first to read the "
    "full current content, then pass the complete updated content to patch.\n\n"
    "Do NOT capture environment-dependent failures, missing binaries, or "
    "transient errors that resolved before the conversation ended.\n\n"
    "'Nothing to save.' is a real option but should NOT be the default."
)

_FLOW_REVIEW_PROMPT = (
    "New .flow.ts file(s) were just written this turn (paths provided below). "
    "Your job: read each file, assess it, then save it and decide whether to promote it.\n\n"
    "Step 1 — Save to adhoc (always do this first):\n"
    "  Use flow_manage(action='create', target='adhoc', flow_name=<slug>, ...) to save the flow.\n"
    "  The slug should be derived from the file name or task description (kebab-case).\n\n"
    "Step 2 — Assess quality for promotion:\n"
    "  A flow is worth promoting to curated/ when ALL of these hold:\n"
    "  • It has 5+ flow primitives (agent/parallel/pipeline/phase calls)\n"
    "  • It implements a clear reusable multi-step pipeline\n"
    "  • The same sequence would likely be valuable in future sessions\n"
    "  • It has meaningful inputs/outputs and is not a one-off task\n\n"
    "Step 3 — Promote if worthy:\n"
    "  If the flow meets the criteria above, call:\n"
    "  flow_manage(action='promote', flow_name=<slug>, description=<one-line desc>, category=<tag>)\n"
    "  This moves it from flows/adhoc/ to flows/curated/.\n\n"
    "  If the flow does NOT meet the criteria, leave it in adhoc and say "
    "'Saved to adhoc, not promoted.' and stop.\n\n"
    "If nothing is worth saving at all, say 'Nothing to capture.' and stop."
)

_COMBINED_REVIEW_PROMPT = (
    "Review the conversation above for two purposes:\n\n"
    "1. MEMORY — save anything the user revealed about themselves, their "
    "preferences, or how they want you to behave. Use the memory tool.\n\n"
    "2. SKILLS — update the skill library with techniques, corrections, or "
    "workflow improvements discovered this session. Use the skill_manage tool.\n\n"
    "Be ACTIVE on both fronts. A pass that does nothing on either is a missed "
    "learning opportunity.\n\n"
    "For skills, prefer patching existing class-level skills over creating new "
    "narrow ones. Do NOT capture transient or environment-specific failures.\n\n"
    "'Nothing to save.' is a real option but should NOT be the default for skills."
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

    Maintains turn and tool-call counters and spawns isolated async review
    tasks to write memory and skills without blocking the main session.

    On initialisation, spawns a ``maybe_run_curator()`` task with
    ``idle_for_seconds=inf`` (startup = fully idle), matching hermes-agent
    behaviour.

    Attributes:
        MEMORY_INTERVAL: Number of user turns between memory reviews.
        SKILL_TOOL_THRESHOLD: Minimum tool calls in a turn to trigger skill review.
    """

    MEMORY_INTERVAL: int = 10
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
            tool_executors: Mapping of tool name → async callable.
                Only tools in this map AND in the per-review whitelist will
                be executed. Defaults to empty dict (no tools available).
            workspace_dir: Workspace root path. When provided, a curator check
                is spawned on startup (idle_for_seconds=inf).
        """
        self._complete_fn = complete_fn
        self._tool_executors: ToolExecutors = tool_executors or {}
        self._turn_count: int = 0
        self._workspace_dir: anyio.Path | None = (
            anyio.Path(str(workspace_dir)) if workspace_dir is not None else None
        )
        # Startup curator check — runs after event loop is available
        if self._workspace_dir is not None:
            asyncio.create_task(
                self._startup_curator_check(),
                name="startup-curator-check",
            )

    async def _startup_curator_check(self) -> None:
        """Spawn curator check on startup with idle_for_seconds=inf."""
        try:
            from curator import maybe_run_curator

            await maybe_run_curator(
                self._workspace_dir,
                self._simple_complete_fn(),
                idle_for_seconds=float("inf"),
            )
        except Exception as exc:
            logger.warning("BackgroundReview: startup curator check failed: {}", exc)

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
        new_flow_files: list[str] | None = None,
    ) -> None:
        """Check counters and spawn a background review task if triggered.

        Must be called AFTER ``increment_turn()`` for the current turn.

        Args:
            messages_snapshot: Full conversation history up to and including
                the current turn's assistant reply. Will be deep-copied.
            tool_call_count: Number of tool calls made during the current turn.
            new_flow_files: Optional list of new .flow.ts file paths detected
                this turn; triggers a flow review regardless of tool_call_count.
        """
        do_memory = self._turn_count % self.MEMORY_INTERVAL == 0
        do_skills = tool_call_count >= self.SKILL_TOOL_THRESHOLD

        # Explicit flow files always trigger a flow review (independent task)
        if new_flow_files:
            await self.maybe_spawn_flow_review(messages_snapshot, new_flow_files)

        # Memory review: independent task, fires every MEMORY_INTERVAL turns
        if do_memory:
            snapshot = copy.deepcopy(messages_snapshot)
            logger.debug(
                "BackgroundReview: spawning memory review (turn={}, tool_calls={})",
                self._turn_count,
                tool_call_count,
            )
            asyncio.create_task(
                self._run_review(snapshot, _MEMORY_REVIEW_PROMPT, {"memory"}),
                name=f"background-review-memory-turn{self._turn_count}",
            )

        # Skill review: independent task, fires when tool_call_count >= threshold
        if do_skills:
            snapshot = copy.deepcopy(messages_snapshot)
            logger.debug(
                "BackgroundReview: spawning skill review (turn={}, tool_calls={})",
                self._turn_count,
                tool_call_count,
            )
            asyncio.create_task(
                self._run_review(snapshot, _SKILL_REVIEW_PROMPT, {"skill_manage", "read", "bash", "grep"}),
                name=f"background-review-skill-turn{self._turn_count}",
            )

    async def maybe_spawn_flow_review(
        self,
        messages_snapshot: list[dict[str, Any]],
        new_flow_files: list[str],
    ) -> None:
        """Spawn a flow review task for newly detected .flow.ts files.

        Called by the session layer (after_turn) when bash/write tool calls
        produce new .flow.ts files.  Fires unconditionally — the caller is
        responsible for deciding when to trigger.

        Args:
            messages_snapshot: Full conversation history up to current turn.
            new_flow_files: Paths to newly written .flow.ts files.
        """
        if not new_flow_files:
            return

        file_list = "\n".join(f"  - {p}" for p in new_flow_files)
        prompt = (
            f"{_FLOW_REVIEW_PROMPT}\n\n"
            f"The following new .flow.ts file(s) were just written this turn:\n{file_list}\n\n"
            "Read each file, assess whether it is worth preserving as a reusable flow, "
            "and if so call flow_manage to save it."
        )
        logger.debug(
            "BackgroundReview: spawning flow review for {} new flow file(s)",
            len(new_flow_files),
        )
        asyncio.create_task(
            self._run_review(
                copy.deepcopy(messages_snapshot),
                prompt,
                {"flow_manage", "bash", "read"},
            ),
            name=f"background-review-flow-files-turn{self._turn_count}",
        )

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
            logger.warning("BackgroundReview: review task failed: {}", exc, exc_info=True)

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
        loop_messages = messages + [{"role": "user", "content": prompt}]

        for iteration in range(MAX_REVIEW_ITERATIONS):
            try:
                response = await self._complete_fn(loop_messages, tool_schemas)
            except Exception as exc:
                logger.debug(
                    "BackgroundReview: LLM call failed at iteration {}: {}", iteration, exc
                )
                return

            # Extract assistant message
            choices = response.get("choices") or []
            if not choices:
                logger.debug("BackgroundReview: empty choices at iteration {}", iteration)
                return
            assistant_msg = choices[0].get("message", {})
            loop_messages.append(assistant_msg)

            # Check for tool calls
            tool_calls = assistant_msg.get("tool_calls") or []
            if not tool_calls:
                content_preview = (assistant_msg.get("content") or "")[:120]
                logger.debug(
                    "BackgroundReview: review finished at iteration {} (no tool call) reply={!r}",
                    iteration,
                    content_preview,
                )
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
                    logger.debug(
                        "BackgroundReview: blocked tool call '{}' (not in whitelist)", tool_name
                    )
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
                            logger.debug("BackgroundReview: tool '{}' error: {}", tool_name, exc)

                loop_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": str(result),
                    }
                )

        logger.debug(
            "BackgroundReview: reached MAX_REVIEW_ITERATIONS ({}), stopping", MAX_REVIEW_ITERATIONS
        )


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

    if "memory" in allowed_tools:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "memory",
                    "description": "Read, write, append, or clear workspace/memory.md.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["read", "write", "append", "clear"],
                                "description": "Operation to perform.",
                            },
                            "content": {
                                "type": "string",
                                "description": "Content to write or append (write/append only).",
                            },
                        },
                        "required": ["action"],
                    },
                },
            }
        )

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

    if "bash" in allowed_tools:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "Run a shell command and return stdout+stderr.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "Shell command to execute.",
                            },
                        },
                        "required": ["command"],
                    },
                },
            }
        )

    if "read" in allowed_tools:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file and return its contents.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Absolute or workspace-relative path to the file.",
                            },
                        },
                        "required": ["file_path"],
                    },
                },
            }
        )

    if "flow_manage" in allowed_tools:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "flow_manage",
                    "description": "Create, view, list, promote, or delete reusable flows in the workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["create", "view", "list", "promote", "delete"],
                                "description": "Operation to perform.",
                            },
                            "flow_name": {
                                "type": "string",
                                "description": "Flow directory name (kebab-case). Required for create/view/promote/delete.",
                            },
                            "target": {
                                "type": "string",
                                "enum": ["adhoc", "curated"],
                                "description": "Storage target for create (default: adhoc). For list: 'all', 'adhoc', or 'curated'.",
                            },
                            "body": {
                                "type": "string",
                                "description": "FLOW.md markdown body content (create only).",
                            },
                            "flow_ts": {
                                "type": "string",
                                "description": "TypeScript flow.ts source code (create only).",
                            },
                            "description": {
                                "type": "string",
                                "description": "Short one-line description (create/promote).",
                            },
                            "category": {
                                "type": "string",
                                "description": "Flow category tag (create/promote).",
                            },
                        },
                        "required": ["action"],
                    },
                },
            }
        )

    return schemas
