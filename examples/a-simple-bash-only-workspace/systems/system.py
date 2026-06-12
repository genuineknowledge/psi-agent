"""Async system configuration for the workspace."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import anyio

# Type aliases
CompleteFn = Callable[[list[dict[str, Any]]], Awaitable[str]]


async def _parse_skill_description(skill_md_path: anyio.Path) -> str | None:
    """Parse SKILL.md to extract description from YAML frontmatter.

    Args:
        skill_md_path: Path to the SKILL.md file.

    Returns:
        Description string if found, None otherwise.
    """
    content = await skill_md_path.read_text()

    # Match YAML frontmatter between --- markers
    frontmatter_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not frontmatter_match:
        return None

    frontmatter = frontmatter_match.group(1)

    # Extract description field
    description_match = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
    if description_match:
        return description_match.group(1).strip()

    return None


def _estimate_tokens(message: dict[str, Any]) -> int:
    """Estimate token count for a message using chars/4 heuristic.

    This is a conservative estimate (overestimates tokens).

    Args:
        message: A conversation message with role and content.

    Returns:
        Estimated token count.
    """
    chars = 0
    content = message.get("content", "")

    if isinstance(content, str):
        chars = len(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    chars += len(block.get("text", ""))
                elif block.get("type") == "image":
                    chars += 4800  # Estimate images as 1200 tokens

    return max(1, chars // 4)


class System:
    """Simple workspace system configuration."""

    def __init__(self, workspace_dir: anyio.Path) -> None:
        """Initialize the System instance.

        Args:
            workspace_dir: Path to the workspace directory.
        """
        self._workspace_dir = workspace_dir

    async def build_system_prompt(self) -> str:
        """Build system prompt by scanning skills directory asynchronously.

        This function scans the skills/ directory for all SKILL.md files,
        parses their YAML frontmatter to extract descriptions, and combines
        them into a system prompt.

        Returns:
            System prompt string containing skill descriptions and guidelines.
        """
        skills_dir = self._workspace_dir / "skills"
        skill_descriptions: list[str] = []

        if await skills_dir.exists():
            async for skill_path in skills_dir.iterdir():
                if await skill_path.is_dir():
                    skill_md = skill_path / "SKILL.md"
                    if await skill_md.exists():
                        description = await _parse_skill_description(skill_md)
                        if description:
                            skill_descriptions.append(f"- {skill_path.name}: {description}")

        workspace_resolved = await self._workspace_dir.resolve()
        skills_dir = workspace_resolved / "skills"
        system_prompt = f"""You are a helpful assistant with access to tools and skills.

## Workspace

Your workspace directory is: {workspace_resolved}
All skills are located in: {skills_dir}

## Available Skills

"""
        if skill_descriptions:
            system_prompt += "\n".join(skill_descriptions)
        else:
            system_prompt += "No skills configured."

        system_prompt += """

## Guidelines

- Use tools when appropriate to accomplish tasks
- When you need a skill's detailed instructions, read the SKILL.md file
- Be helpful, accurate, and concise
"""

        return system_prompt

    async def compact_history(
        self,
        history: list[dict[str, Any]],
        complete_fn: CompleteFn,
        max_tokens: int = 4000,
        keep_recent_tokens: int | None = None,
    ) -> list[dict[str, Any]]:
        """Compact conversation history by keeping recent messages.

        This simple implementation ignores the complete_fn and keep_recent_tokens
        parameters and uses simple truncation. It accepts them for interface
        compatibility with other workspaces.

        Args:
            history: List of conversation messages with role and content.
            complete_fn: Async function for single-turn LLM conversation.
                Ignored in this simple implementation.
            max_tokens: Maximum tokens to keep in history.
            keep_recent_tokens: Ignored in this simple implementation.

        Returns:
            Compacted history list with recent messages only.
        """
        _ = complete_fn  # Ignored: simple workspace uses truncation
        _ = keep_recent_tokens  # Ignored: simple workspace uses truncation

        # Calculate total tokens in history
        total_tokens = sum(_estimate_tokens(msg) for msg in history)

        if total_tokens <= max_tokens:
            return history

        # Walk backwards to find cut point
        accumulated = 0
        cut_index = len(history)

        for i in range(len(history) - 1, -1, -1):
            accumulated += _estimate_tokens(history[i])
            if accumulated >= max_tokens:
                cut_index = i
                break

        return history[cut_index:]
