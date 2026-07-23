from __future__ import annotations

import hashlib
import inspect
import logging
import sys
import types
from pathlib import Path

import anyio

from psi_agent._yaml import parse_yaml_header

logger = logging.getLogger(__name__)


async def system_prompt_builder() -> str:
    """Build the system prompt for Fusion Memory tools."""
    current_file = anyio.Path(inspect.getfile(system_prompt_builder))
    workspace_root = current_file.parent.parent
    await _activate_fusion_memory(workspace_root)
    skills_dir = workspace_root / "skills"
    skills = await _load_workspace_skills(skills_dir)
    skills_text = "\n".join(skills) if skills else "(None)"

    return (
        "You have access to durable Fusion Memory through a remote MCP service via four tools:\n"
        "- memory_add: store a stable user preference, project fact, or decision\n"
        "- memory_search: retrieve raw evidence by keyword\n"
        "- memory_answer_context: retrieve a query-grounded context pack\n"
        "- memory_health: check authenticated MCP connectivity for the current user\n\n"
        "Use memory_answer_context when answering questions about the user's history, preferences, or prior context. "
        "Use memory_search when you need raw supporting evidence. "
        "Use memory_add only for durable, reusable facts, not transient conversation.\n\n"
        "The process starter configures the operator-owned token map before launch. "
        "A mapped user's first message automatically starts MCP health checking and passive history writing. "
        "The bearer token identifies the user; that user's Sessions share memory, "
        "while different users remain isolated. "
        "An unmapped user can continue chatting but has no durable memory. "
        "Use memory_health for status; never inspect model-visible Feishu context for authentication, "
        "edit .env, ask for a token, or expose credentials. "
        "Use the fusion-memory-setup skill to inspect the remote MCP deployment, "
        "but do not install, start, or replace it with an HTTP memory API.\n\n"
        f"## Workspace Skills\nLocation: {skills_dir}\n\nAvailable:\n{skills_text}"
    )


async def system_prompt_rebuild_checker() -> bool:
    """Activate Memory on the first turn after restoring an existing Session."""
    current_file = anyio.Path(inspect.getfile(system_prompt_rebuild_checker))
    await _activate_fusion_memory(current_file.parent.parent)
    return False


async def _activate_fusion_memory(workspace_root: anyio.Path) -> None:
    mcp_path = Path(str(workspace_root)) / "tools" / "_fusion_memory_mcp.py"
    module_name = f"fusion_memory_tool__fusion_memory_mcp_{hashlib.sha256(str(mcp_path).encode()).hexdigest()[:12]}"
    module = sys.modules.get(module_name)
    created = False
    try:
        if module is None:
            source = await anyio.Path(str(mcp_path)).read_text(encoding="utf-8")
            module = types.ModuleType(module_name)
            module.__file__ = str(mcp_path)
            sys.modules[module_name] = module
            created = True
            exec(compile(source, str(mcp_path), "exec"), module.__dict__)
        client = module.__dict__.get("CLIENT")
        activate = getattr(client, "activate_current_session", None)
        if activate is not None:
            await activate(workspace_root)
    except Exception as exc:
        if created:
            sys.modules.pop(module_name, None)
        logger.warning("Fusion Memory activation skipped after %s", type(exc).__name__)


async def _load_workspace_skills(skills_dir: anyio.Path) -> list[str]:
    skills: list[str] = []
    if not await skills_dir.is_dir():
        return skills
    skill_dirs = sorted([p async for p in skills_dir.iterdir()], key=lambda p: p.name)
    for skill_dir in skill_dirs:
        if not await skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not await skill_md.exists():
            continue
        header, _ = parse_yaml_header(await skill_md.read_text(encoding="utf-8"))
        if header and header.get("name") and header.get("description"):
            skills.append(f"- {header['name']}: {header['description']}")
    return skills
