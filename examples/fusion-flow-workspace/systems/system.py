"""System prompt for the Fusion Flow authoring workspace."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio

CompleteFn = Callable[[list[dict[str, Any]]], Awaitable[str]]


async def _parse_skill_description(skill_md_path: anyio.Path) -> str | None:
    content = await skill_md_path.read_text(encoding="utf-8", errors="replace")
    frontmatter_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not frontmatter_match:
        return None

    frontmatter = frontmatter_match.group(1)
    description_match = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
    if description_match:
        return description_match.group(1).strip().strip('"').strip("'")
    return None


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

        skill_descriptions: list[str] = []
        if await skills_dir.exists():
            async for skill_path in skills_dir.iterdir():
                if not await skill_path.is_dir():
                    continue
                skill_md = skill_path / "SKILL.md"
                if not await skill_md.exists():
                    continue
                description = await _parse_skill_description(skill_md)
                if description:
                    skill_descriptions.append(f"- {skill_path.name}: {description}")

        tool_names = tool_names or []
        model_line = f"\nModel: {model}" if model else ""
        tools_line = ", ".join(tool_names) if tool_names else "(none)"

        return f"""You are a psi-agent workspace specialized in authoring and running
Fusion Flow workflows from natural language.

## Workspace

Workspace directory: {workspace_resolved}
Available tools: {tools_line}{model_line}

## Available Skills

{chr(10).join(skill_descriptions) if skill_descriptions else "No skills configured."}

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

## psi-agent Engine Defaults

Fusion Flow may call external agent CLI engines. For this workspace, prefer the psi engine.
Do not call this same authoring workspace recursively as the execution workspace.
Use this default execution workspace unless the user provides another one:

FLOW_ENGINE=psi
FLOW_PSI_WORKSPACE={default_executor_workspace}
FLOW_PSI_PROFILE=fusion

When psi-agent is not installed globally, run Fusion Flow with these local overrides:

FLOW_PSI_COMMAND=uv
FLOW_PSI_COMMAND_ARGS=--project {repo_root} run psi-agent

Keep provider URLs and API keys in psi-agent profile configuration or environment variables.
Never write API keys into this workspace, generated `.flow.ts` files, or `.env` files.

## Operating Rules

- For workflow-shaped natural-language requests, build a flow instead of manually acting as the sub-agents.
- Before running a flow, ensure the Fusion Flow skill directory has dependencies installed.
- If `node_modules` is missing, ask the user before running `npm install` unless they already requested full execution.
- Prefer concise user-facing progress updates and report generated file paths.
- Use `read`, `write`, `edit`, and `bash` tools as needed.
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
