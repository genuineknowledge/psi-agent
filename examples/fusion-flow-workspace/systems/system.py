"""System prompt for the Fusion Flow authoring workspace."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio

CompleteFn = Callable[[list[dict[str, Any]]], Awaitable[str]]
ReviewCompleteFn = Callable[
    [list[dict[str, Any]], list[dict[str, Any]] | None],
    Awaitable[dict[str, Any]],
]
ToolExecutors = dict[str, Callable[..., Awaitable[Any]]]

logger = logging.getLogger(__name__)

MAX_SELF_EVOLUTION_ITERATIONS = 6
SELF_EVOLUTION_TOOL_THRESHOLD = 2

_SELF_EVOLUTION_PROMPT = (
    "Review the completed turn and decide whether this Fusion Flow workspace should learn from it."
    """

Only update workspace assets when the conversation produced reusable knowledge:
- workflow-authoring patterns that should become reusable curated flows
- recurring Fusion Flow structure, validation, or runtime practices
- corrections to an agent-created skill or a new class-level skill

Use `skill_manage` for reusable non-flow procedures.
Use `flow_manage` for reusable Fusion Flow templates.

Rules:
1. Do not update anything for one-off task facts, transient errors, secrets, local credentials, or user-private data.
2. Do not patch user-authored skills or the immutable `skills/fusion-flow/` runtime skill.
3. Prefer patching an existing agent-created asset over creating a narrow duplicate.
4. If nothing is worth saving, reply exactly: Nothing to save.
"""
)


def _build_self_evolution_tool_schemas() -> list[dict[str, Any]]:
    return [
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
                            "enum": ["list", "view", "create", "patch"],
                        },
                        "skill_name": {"type": "string"},
                        "content": {"type": "string"},
                        "category": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "flow_manage",
                "description": "Create, patch, view, list, or promote reusable Fusion Flow assets.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "view", "create", "patch", "promote"],
                        },
                        "flow_name": {"type": "string"},
                        "description": {"type": "string"},
                        "category": {"type": "string"},
                        "body": {"type": "string"},
                        "flow_ts": {"type": "string"},
                        "target": {
                            "type": "string",
                            "enum": ["curated", "tasks", "adhoc", "all"],
                        },
                    },
                    "required": ["action"],
                },
            },
        },
    ]


async def _run_self_evolution_review(
    *,
    messages: list[dict[str, Any]],
    complete_fn: ReviewCompleteFn,
    tool_executors: ToolExecutors,
) -> None:
    allowed_tools = {"skill_manage", "flow_manage"}
    tool_schemas = _build_self_evolution_tool_schemas()
    loop_messages = [*messages, {"role": "user", "content": _SELF_EVOLUTION_PROMPT}]

    for iteration in range(MAX_SELF_EVOLUTION_ITERATIONS):
        try:
            response = await complete_fn(loop_messages, tool_schemas)
        except Exception as exc:
            logger.debug("Fusion Flow self-evolution LLM call failed at iteration %d: %s", iteration, exc)
            return

        choices = response.get("choices") or []
        if not choices:
            logger.debug("Fusion Flow self-evolution got empty choices at iteration %d", iteration)
            return

        assistant_msg = choices[0].get("message") or {}
        if not isinstance(assistant_msg, dict):
            return
        loop_messages.append(assistant_msg)

        tool_calls = assistant_msg.get("tool_calls") or []
        if not tool_calls:
            return

        for tool_call in tool_calls:
            call_id = tool_call.get("id", "")
            function = tool_call.get("function") or {}
            tool_name = function.get("name", "")
            args_raw = function.get("arguments") or "{}"

            if tool_name not in allowed_tools:
                result = f"Tool {tool_name!r} is not allowed in Fusion Flow self-evolution."
            else:
                executor = tool_executors.get(tool_name)
                if executor is None:
                    result = f"Tool {tool_name!r} is not registered."
                else:
                    try:
                        args = json.loads(args_raw)
                    except TypeError, json.JSONDecodeError:
                        args = {}
                    try:
                        result = await executor(**args)
                    except Exception as exc:
                        logger.debug("Fusion Flow self-evolution tool %r failed: %s", tool_name, exc)
                        result = f"Tool {tool_name!r} raised an error: {exc}"

            loop_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": tool_name,
                    "content": str(result),
                }
            )


async def _parse_skill_frontmatter(skill_md_path: anyio.Path) -> dict[str, str]:
    content = await skill_md_path.read_text(encoding="utf-8", errors="replace")
    frontmatter_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not frontmatter_match:
        return {"name": "", "description": "", "category": "general"}

    frontmatter = frontmatter_match.group(1)

    def _extract(field: str) -> str:
        match = re.search(rf"^{field}:\s*(.+)$", frontmatter, re.MULTILINE)
        return match.group(1).strip().strip('"').strip("'") if match else ""

    description = _extract("description")
    if len(description) > 60:
        description = description[:57] + "..."

    return {
        "name": _extract("name"),
        "description": description,
        "category": _extract("category") or "general",
    }


async def _build_skills_index(skills_dir: anyio.Path) -> str:
    if not await skills_dir.exists():
        return "No skills configured."

    skills_by_category: dict[str, list[tuple[str, str]]] = {}
    async for skill_path in skills_dir.iterdir():
        if not await skill_path.is_dir():
            continue
        skill_md = skill_path / "SKILL.md"
        if not await skill_md.exists():
            continue
        frontmatter = await _parse_skill_frontmatter(skill_md)
        skills_by_category.setdefault(frontmatter["category"], []).append(
            (frontmatter["name"] or skill_path.name, frontmatter["description"])
        )

    if not skills_by_category:
        return "No skills configured."

    index_lines: list[str] = []
    for category in sorted(skills_by_category):
        index_lines.append(f"  {category}:")
        for name, description in sorted(skills_by_category[category]):
            if description:
                index_lines.append(f"    - {name}: {description}")
            else:
                index_lines.append(f"    - {name}")

    return (
        "Before replying, scan the skills below. If a skill matches or is even partially relevant "
        "to your task, you MUST load it by reading the corresponding SKILL.md file and follow its "
        "instructions. Err on the side of loading; skills contain specialized workflows, pitfalls, "
        "and user-preferred conventions.\n\n"
        "<available_skills>\n" + "\n".join(index_lines) + "\n</available_skills>"
    )


async def _build_flows_index(flows_dir: anyio.Path) -> str:
    curated_dir = flows_dir / "curated"
    task_lines: list[str] = []
    curated_lines: list[str] = []

    if await curated_dir.exists():
        async for flow_dir in curated_dir.iterdir():
            if not await flow_dir.is_dir() or flow_dir.name.startswith("."):
                continue
            flow_md = flow_dir / "FLOW.md"
            if not await flow_md.exists():
                continue
            raw = await flow_md.read_text(encoding="utf-8", errors="replace")
            frontmatter_match = re.match(r"^---\n(.*?)\n---", raw, re.DOTALL)
            description = ""
            category = "general"
            if frontmatter_match:
                frontmatter = frontmatter_match.group(1)
                desc_match = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
                category_match = re.search(r"^category:\s*(.+)$", frontmatter, re.MULTILINE)
                if desc_match:
                    description = desc_match.group(1).strip().strip('"').strip("'")
                if category_match:
                    category = category_match.group(1).strip().strip('"').strip("'") or "general"
            suffix = f": {description}" if description else ""
            curated_lines.append(f"    - {flow_dir.name} ({category}){suffix}")

    if await flows_dir.exists():
        async for task_dir in flows_dir.iterdir():
            if not await task_dir.is_dir() or task_dir.name.startswith(".") or task_dir.name in {"curated", "adhoc"}:
                continue
            preferred = task_dir / f"{task_dir.name}.flow.ts"
            if await preferred.exists():
                task_lines.append(f"    - {task_dir.name}: {preferred.name}")
                continue
            async for flow_file in task_dir.glob("*.flow.ts"):
                task_lines.append(f"    - {task_dir.name}: {flow_file.name}")
                break

    if not curated_lines and not task_lines:
        return "No reusable flows configured."

    index_lines = [
        "Before creating a new workflow, scan the reusable flows below. If a curated flow fits, "
        "view it with `flow_manage` and adapt it instead of starting from scratch.",
        "",
        "<available_flows>",
    ]
    if curated_lines:
        index_lines.append("  curated:")
        index_lines.extend(sorted(curated_lines))
    if task_lines:
        index_lines.append("  generated_tasks:")
        index_lines.extend(sorted(task_lines))
    index_lines.append("</available_flows>")
    return "\n".join(index_lines)


def _estimate_tokens(message: dict[str, Any]) -> int:
    content = message.get("content", "")
    if isinstance(content, str):
        return max(1, len(content) // 4)
    return 1


class System:
    """Workspace system configuration for Fusion Flow natural-language authoring."""

    def __init__(self, workspace_dir: anyio.Path) -> None:
        self._workspace_dir = workspace_dir

    async def build_system_prompt(self, model: str | None = None, tool_names: list[str] | None = None) -> str:
        workspace_resolved = await self._workspace_dir.resolve()
        skills_dir = workspace_resolved / "skills"
        fusion_skill_dir = skills_dir / "fusion-flow"
        fusion_skill_md = fusion_skill_dir / "SKILL.md"
        flows_dir = workspace_resolved / "flows"

        repo_root = Path(str(workspace_resolved)).parents[1]
        default_executor_workspace = repo_root / "examples" / "hermes-style-workspace"

        skills_index = await _build_skills_index(skills_dir)
        flows_index = await _build_flows_index(flows_dir)

        tool_names = tool_names or []
        model_line = f"\nModel: {model}" if model else ""
        tools_line = ", ".join(tool_names) if tool_names else "(none)"

        return f"""You are a Haitun agent workspace specialized in authoring and running
Fusion Flow workflows from natural language.

## Workspace

Workspace directory: {workspace_resolved}
Skills directory: {skills_dir}
Available tools: {tools_line}{model_line}

## Web UI File Capabilities

The Web UI supports file upload and download.

When the user uploads files, their message may include attachment lines like:
FILE:/absolute/path/to/file

Treat these FILE paths as user-provided attachments. When relevant, inspect
text-like files with the `read` tool and process binary/media files by using
their absolute paths with suitable tools or commands. If the user asks you to
produce a downloadable artifact, create the file under the current workspace,
the generated flow's task directory, or another allowed path using `write` or
an appropriate tool. In your final reply, include each downloadable artifact on
its own line using this exact marker:
FILE:/absolute/path/to/artifact
The Web UI renders `FILE:` lines as downloadable file cards. Do not rely on a
plain absolute path alone when you want the user to receive a downloadable
file.

## Chinese Reasoning Preference

Use Chinese as the default language for internal planning, task decomposition,
tool-use planning, error analysis, and user-facing explanations, especially
when the user writes in Chinese. Keep private chain-of-thought hidden; if the
user asks how you reached a result, provide a concise Chinese rationale,
assumptions, checks performed, and next steps instead of a full internal
monologue.

## Skills (mandatory)

{skills_index}

## Reusable Flows

{flows_index}

## Fusion Flow Trigger

When the user describes a workflow-shaped task in natural language, activate the Fusion Flow skill.
Workflow-shaped tasks include multi-agent collaboration, parallel review, fan-out/fan-in, pipelines,
multi-step research or scoring, or requests to run or inspect `.flow.ts` workflow results.

To activate Fusion Flow:

1. Read the full skill instructions at:
   {fusion_skill_md}
   Relative path: skills/fusion-flow/SKILL.md
2. Keep the skill itself immutable. Author generated task files under:
   {flows_dir}/<task-slug>/
   Use this layout:
   - {flows_dir}/<task-slug>/<task-slug>.flow.ts
   - {flows_dir}/<task-slug>/runs/<run-id>/
3. Use the Fusion Flow runtime from:
   {fusion_skill_dir / "runtime" / "agent-flow-core.bundle.mjs"}
   Generated flows in flows/<task-slug>/ import it with:
   ../../skills/fusion-flow/runtime/agent-flow-core.bundle.mjs
4. Typecheck from the Fusion Flow skill directory. Its tsconfig includes ../../flows/**/*.ts:
   cd "{fusion_skill_dir}" && npm run typecheck
5. Run generated flows from the Fusion Flow skill directory:
   cd "{fusion_skill_dir}" && npx tsx ../../flows/<task-slug>/<task-slug>.flow.ts

When generating the run(...) options, always include both:
- programPath normalized from import.meta.url
- runsDir set to the generated flow's sibling ./runs directory

This keeps each user's `.flow.ts`, meta.json, execution-graph.json, bindings/, and trace/
together under one task folder instead of writing user artifacts into skills/.

## Self-Evolution Tools

This workspace exposes explicit, user-visible self-evolution tools:

- `skill_manage` can list, view, create, and patch workspace skills.
- `flow_manage` can list, view, create, patch, and promote reusable Fusion Flow assets.

Use these tools only when the current task produces reusable workflow knowledge or the user asks
to maintain the workspace. Never silently rewrite user-authored assets.

Rules:

1. Keep `skills/fusion-flow/` immutable. It is the runtime bundle, not a generated skill.
2. Treat skills without `created_by: agent` as read-only. View them for context, but do not patch them.
3. New learned procedures belong in `skills/<skill-name>/SKILL.md` via `skill_manage(action="create")`.
4. Reusable workflow templates belong in `flows/curated/<flow-name>/FLOW.md` via `flow_manage`.
5. One-off task executions still belong in `flows/<task-slug>/`.
6. Promote a generated task flow only after it has useful, reusable structure; include a concise
   description of when to reuse it.

## Haitun Agent Engine Defaults

Fusion Flow may call external agent CLI engines. For this workspace, prefer the psi engine.
Do not call this same authoring workspace recursively as the execution workspace.
Use this default execution workspace unless the user provides another one:

FLOW_ENGINE=psi
FLOW_PSI_WORKSPACE={default_executor_workspace}
FLOW_PSI_PROFILE=fusion

When the Haitun agent CLI is not installed globally, run Fusion Flow with these local overrides:

FLOW_PSI_COMMAND=uv
FLOW_PSI_COMMAND_ARGS=--project {repo_root} run psi-agent

Keep provider URLs and API keys in Haitun agent profile configuration or environment variables.
Never write API keys into this workspace, generated `.flow.ts` files, or `.env` files.

## Operating Rules

- For workflow-shaped natural-language requests, build a flow instead of manually acting as the sub-agents.
- Before running a flow, ensure the Fusion Flow skill directory has dependencies installed.
- If `node_modules` is missing, ask the user before running `npm install` unless they already requested full execution.
- Prefer concise user-facing progress updates and report generated file paths.
- Use `read`, `write`, `edit`, `bash`, `skill_manage`, and `flow_manage` tools as needed.
"""

    async def compact_history(
        self,
        history: list[dict[str, Any]],
        complete_fn: CompleteFn,
        max_tokens: int = 4000,
        keep_recent_tokens: int | None = None,
    ) -> list[dict[str, Any]]:
        _ = complete_fn
        _ = keep_recent_tokens
        total_tokens = sum(_estimate_tokens(msg) for msg in history)
        if total_tokens <= max_tokens:
            return history

        accumulated = 0
        cut_index = len(history)
        for i in range(len(history) - 1, -1, -1):
            accumulated += _estimate_tokens(history[i])
            if accumulated >= max_tokens:
                cut_index = i
                break
        return history[cut_index:]

    async def after_turn(
        self,
        messages: list[dict[str, Any]],
        tool_call_count: int,
        called_tools: list[str],
        *,
        complete_fn: ReviewCompleteFn,
        tool_executors: ToolExecutors,
    ) -> None:
        called = set(called_tools)
        should_review = (
            tool_call_count >= SELF_EVOLUTION_TOOL_THRESHOLD
            or "flow_manage" in called
            or "skill_manage" in called
            or ("bash" in called and "write" in called)
            or "edit" in called
        )
        if not should_review:
            return

        review_tools = {
            name: tool_executors[name] for name in ("skill_manage", "flow_manage") if name in tool_executors
        }
        if not review_tools:
            logger.debug("Fusion Flow self-evolution skipped: no review tools registered")
            return

        await _run_self_evolution_review(
            messages=messages,
            complete_fn=complete_fn,
            tool_executors=review_tools,
        )


async def system_prompt_builder() -> str:
    """Workspace entry point expected by psi-agent's session loader.

    The loader imports a no-arg async ``system_prompt_builder`` from
    ``systems/system.py``. Locate the workspace root relative to this file
    and delegate to ``System.build_system_prompt``.
    """
    workspace_dir = (await anyio.Path(__file__).resolve()).parent.parent
    return await System(workspace_dir).build_system_prompt()
