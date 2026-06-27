# ruff: noqa: E402, ASYNC240

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import pathlib
import platform
import re
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import anyio

# Ensure the workspace root (parent of systems/) is on sys.path so that
# `from systems.X import ...` resolves correctly regardless of cwd.
_SYSTEMS_DIR = pathlib.Path(__file__).parent
_WORKSPACE_ROOT = str(_SYSTEMS_DIR.parent)
if _WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, _WORKSPACE_ROOT)

from systems.prompt_constants import (
    COMPUTER_USE_GUIDANCE,
    CONTEXT_FILE_MAX_CHARS,
    CONTEXT_TRUNCATE_HEAD_RATIO,
    CONTEXT_TRUNCATE_TAIL_RATIO,
    DEFAULT_AGENT_IDENTITY,
    GOOGLE_MODEL_OPERATIONAL_GUIDANCE,
    KANBAN_GUIDANCE,
    OPENAI_MODEL_EXECUTION_GUIDANCE,
    PLATFORM_HINTS,
    PSI_AGENT_HELP_GUIDANCE,
    SESSION_SEARCH_GUIDANCE,
    SKILLS_GUIDANCE,
    TASK_COMPLETION_GUIDANCE,
    TOOL_USE_ENFORCEMENT_GUIDANCE,
    TOOL_USE_ENFORCEMENT_MODELS,
)
from systems.threat_patterns import scan_for_threats

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

CompleteFn = Callable[[list[dict[str, Any]]], Awaitable[str]]


def _find_git_root(start: anyio.Path) -> anyio.Path | None:
    current = pathlib.Path(str(start)).resolve()
    for directory in [current, *current.parents]:
        if (directory / ".git").exists():
            return anyio.Path(str(directory))
    return None


def _truncate_content(content: str, filename: str, max_chars: int = CONTEXT_FILE_MAX_CHARS) -> str:
    if len(content) <= max_chars:
        return content
    head_chars = int(max_chars * CONTEXT_TRUNCATE_HEAD_RATIO)
    tail_chars = int(max_chars * CONTEXT_TRUNCATE_TAIL_RATIO)
    head = content[:head_chars]
    tail = content[-tail_chars:]
    marker = (
        f"\n\n[...truncated {filename}: kept {head_chars}+{tail_chars} of "
        f"{len(content)} chars. Use file tools to read the full file.]\n\n"
    )
    return head + marker + tail


def _strip_yaml_frontmatter(content: str) -> str:
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            body = content[end + 4 :].lstrip("\n")
            return body if body else content
    return content


async def _load_soul_md() -> str | None:
    soul_path = anyio.Path(os.path.expanduser("~/.hermes/SOUL.md"))
    if not await soul_path.exists():
        return None
    try:
        content = (await soul_path.read_text(encoding="utf-8")).strip()
        if not content:
            return None
        return _truncate_content(content, "SOUL.md")
    except Exception:
        return None


async def _parse_skill_frontmatter(skill_md_path: anyio.Path) -> dict[str, str]:
    try:
        content = await skill_md_path.read_text(encoding="utf-8")
    except Exception:
        return {"name": "", "description": "", "category": ""}

    frontmatter_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not frontmatter_match:
        return {"name": "", "description": "", "category": ""}

    fm = frontmatter_match.group(1)

    def _extract(field: str) -> str:
        m = re.search(rf"^{field}:\s*(.+)$", fm, re.MULTILINE)
        return m.group(1).strip().strip("'\"") if m else ""

    name = _extract("name")
    description = _extract("description")
    category = _extract("category") or "general"

    # Cap description at 60 chars (Hermes convention)
    if len(description) > 60:
        description = description[:57] + "..."

    return {"name": name, "description": description, "category": category}


async def _build_skills_index(workspace_dir: anyio.Path) -> str:
    skills_dir = workspace_dir / "skills"
    if not await skills_dir.exists():
        return ""

    # --- Build manifest from SKILL.md content ---
    # Content hashes keep the local snapshot stable across checkout/rebase
    # operations that change mtimes without changing skill instructions.
    manifest: dict[str, str] = {}
    skill_dirs: list[anyio.Path] = []
    async for skill_path in skills_dir.iterdir():
        if not await skill_path.is_dir():
            continue
        skill_md = skill_path / "SKILL.md"
        if not await skill_md.exists():
            continue
        skill_dirs.append(skill_path)
        try:
            content = await skill_md.read_text(encoding="utf-8")
            manifest[str(skill_md)] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        except Exception:
            manifest[str(skill_md)] = ""

    if not manifest:
        return ""

    # --- Try loading snapshot from disk ---
    snapshot_path = workspace_dir / ".skills_prompt_snapshot.json"
    snapshot_version = 1

    async def _load_snapshot() -> dict[str, Any] | None:
        if not await snapshot_path.exists():
            return None
        try:
            raw = await snapshot_path.read_text(encoding="utf-8")
            data: dict[str, Any] = json.loads(raw)
            if data.get("version") != snapshot_version:
                return None
            if data.get("manifest") != manifest:
                return None
            return data
        except Exception:
            return None

    async def _save_snapshot(
        skills_list: list[dict[str, str]],
    ) -> None:
        try:
            data: dict[str, Any] = {
                "version": snapshot_version,
                "manifest": manifest,
                "skills": skills_list,
            }
            await snapshot_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    snapshot = await _load_snapshot()
    if snapshot is not None:
        skills_list: list[dict[str, str]] = snapshot["skills"]
    else:
        # Full scan
        skills_list = []
        for skill_path in skill_dirs:
            skill_md = skill_path / "SKILL.md"
            fm = await _parse_skill_frontmatter(skill_md)
            skills_list.append(
                {
                    "name": fm["name"] or skill_path.name,
                    "description": fm["description"],
                    "category": fm["category"],
                }
            )
        await _save_snapshot(skills_list)

    # --- Group by category and build index ---
    skills_by_category: dict[str, list[tuple[str, str]]] = {}
    for entry in skills_list:
        skills_by_category.setdefault(entry["category"], []).append((entry["name"], entry["description"]))

    if not skills_by_category:
        return ""

    index_lines: list[str] = []
    for category in sorted(skills_by_category):
        index_lines.append(f"  {category}:")
        for name, desc in sorted(skills_by_category[category]):
            if desc:
                index_lines.append(f"    - {name}: {desc}")
            else:
                index_lines.append(f"    - {name}")

    return (
        "## Skills (mandatory)\n"
        "Before replying, scan the skills below. If a skill matches or is even partially relevant "
        "to your task, you MUST load it by reading the corresponding SKILL.md file and follow its "
        "instructions. Err on the side of loading — it is always better to have context you don't "
        "need than to miss critical steps, pitfalls, or established workflows.\n"
        "Skills contain specialized knowledge — tool-specific commands and proven workflows that "
        "outperform general-purpose approaches. Skills also encode the user's preferred approach, "
        "conventions, and quality standards.\n"
        "\n"
        "<available_skills>\n" + "\n".join(index_lines) + "\n"
        "</available_skills>\n"
        "\n"
        "Only proceed without loading a skill if genuinely none are relevant to the task."
    )


async def _detect_wsl() -> bool:
    """Detect if running inside WSL by reading /proc/version.

    Returns:
        True if running inside WSL, False otherwise.
    """
    proc_version = anyio.Path("/proc/version")
    with contextlib.suppress(OSError, Exception):
        if await proc_version.exists():
            content = await proc_version.read_text(encoding="utf-8", errors="ignore")
            if "microsoft" in content.lower():
                return True
    return False


async def _probe_tool_versions() -> list[str]:
    """Probe python3, uv, and pip version strings.

    Returns:
        List of non-empty version hint strings.
    """
    lines: list[str] = []
    tools = [
        ("python3", ["python3", "--version"]),
        ("uv", ["uv", "--version"]),
        ("pip", ["pip", "--version"]),
    ]
    for label, cmd in tools:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3)
            output = (stdout or stderr).decode(errors="replace").strip()
            first_line = output.splitlines()[0] if output else ""
            if first_line:
                lines.append(f"{label}: {first_line}")
        except Exception:
            pass
    return lines


async def _build_environment_hints() -> str:
    """Return environment-specific hints for the system prompt.

    Returns:
        Multi-line string describing the execution environment.
    """
    hints: list[str] = []

    host_lines: list[str] = []
    if sys.platform == "win32":
        host_lines.append(f"Host: Windows ({platform.release()})")
    elif sys.platform == "darwin":
        mac_ver = platform.mac_ver()[0]
        host_lines.append(f"Host: macOS ({mac_ver or platform.release()})")
    else:
        host_lines.append(f"Host: {platform.system()} ({platform.release()})")

    host_lines.append(f"User home directory: {os.path.expanduser('~')}")
    with contextlib.suppress(OSError):
        host_lines.append(f"Current working directory: {os.getcwd()}")

    hints.append("\n".join(host_lines))

    # WSL detection
    if await _detect_wsl():
        hints.append(
            "You are running inside WSL (Windows Subsystem for Linux). "
            "The Windows host filesystem is mounted under /mnt/ — "
            "/mnt/c/ is the C: drive, /mnt/d/ is D:, etc. "
            "The user's Windows files are typically at "
            "/mnt/c/Users/<username>/Desktop/, Documents/, Downloads/, etc. "
            "When the user references Windows paths or desktop files, translate "
            "to the /mnt/c/ equivalent. You can list /mnt/c/Users/ to discover "
            "the Windows username if needed."
        )

    # Tool version probe
    tool_lines = await _probe_tool_versions()
    if tool_lines:
        hints.append("\n".join(tool_lines))

    return "\n\n".join(hints)


async def _scan_tool_names(workspace_dir: anyio.Path) -> frozenset[str]:
    """Scan workspace/tools/ and return the set of .py file stems.

    Args:
        workspace_dir: Path to the workspace root directory.

    Returns:
        frozenset of tool name strings (filename without .py extension),
        or empty frozenset if tools/ does not exist.
    """
    tools_dir = workspace_dir / "tools"
    if not await tools_dir.exists():
        return frozenset()
    names: list[str] = []
    async for path in tools_dir.iterdir():
        if path.suffix == ".py":
            names.append(path.stem)
    return frozenset(names)


async def _build_stable(
    workspace_dir: anyio.Path,
    model: str | None = None,
    help_skill_name: str | None = None,
    tool_names: frozenset[str] = frozenset(),
) -> str:
    """Build the stable tier of the system prompt.

    Args:
        workspace_dir: Path to the workspace root directory.
        model: Optional model name used for conditional guidance injection.
        help_skill_name: Optional name of the help skill directory under
            ``workspace/skills/``. When set and the corresponding SKILL.md
            exists, injects a guidance line after the identity block.
        tool_names: Set of tool stem names present in workspace/tools/.
            Used for conditional tool-specific guidance injection.

    Returns:
        Stable tier string.
    """
    parts: list[str] = []

    # 1. Identity: SOUL.md or default
    soul = await _load_soul_md()
    if soul:
        parts.append(soul)
    else:
        parts.append(DEFAULT_AGENT_IDENTITY)

    # 2. Help guidance (optional, injected after identity, before task guidance)
    if help_skill_name is not None:
        skill_md = workspace_dir / "skills" / help_skill_name / "SKILL.md"
        if await skill_md.exists():
            parts.append(PSI_AGENT_HELP_GUIDANCE.format(path=str(skill_md)))

    # 3. Task completion guidance (universal)
    parts.append(TASK_COMPLETION_GUIDANCE)

    # 4. Tool-specific guidance (injected after task completion, before enforcement)
    tool_guidance: list[str] = []
    if "session_search" in tool_names:
        tool_guidance.append(SESSION_SEARCH_GUIDANCE)
    if "skill_manage" in tool_names:
        tool_guidance.append(SKILLS_GUIDANCE)
    if "kanban_show" in tool_names:
        tool_guidance.append(KANBAN_GUIDANCE)
    if "computer_use" in tool_names:
        tool_guidance.append(COMPUTER_USE_GUIDANCE)
    if tool_guidance:
        parts.append(" ".join(tool_guidance))

    # 5. Tool-use enforcement (conditional on model name)
    if model:
        model_lower = model.lower()
        if any(p in model_lower for p in TOOL_USE_ENFORCEMENT_MODELS):
            parts.append(TOOL_USE_ENFORCEMENT_GUIDANCE)
            # Model-family-specific guidance
            if "gemini" in model_lower or "gemma" in model_lower:
                parts.append(GOOGLE_MODEL_OPERATIONAL_GUIDANCE)
            if "gpt" in model_lower or "codex" in model_lower or "grok" in model_lower:
                parts.append(OPENAI_MODEL_EXECUTION_GUIDANCE)

    # 4. Skills index
    skills_index = await _build_skills_index(workspace_dir)
    if skills_index:
        parts.append(skills_index)

    # 5. Environment hints (async: includes WSL detection + tool version probe)
    env_hints = await _build_environment_hints()
    if env_hints:
        parts.append(env_hints)

    # 6. Platform hint (from HERMES_PLATFORM env var)
    platform_key = os.environ.get("HERMES_PLATFORM", "").strip().lower()
    if platform_key:
        hint = PLATFORM_HINTS.get(platform_key)
        if hint:
            parts.append(hint)

    return "\n\n".join(p.strip() for p in parts if p and p.strip())


# ---------------------------------------------------------------------------
# Context tier helpers
# ---------------------------------------------------------------------------


async def _load_hermes_md(cwd: anyio.Path) -> str:
    """Load .hermes.md or HERMES.md, walking from cwd up to git root.

    Search order: cwd first, then each parent up to (and including) the git
    root. Returns the first match found, stripping YAML frontmatter.

    Args:
        cwd: Current working directory path.

    Returns:
        Formatted content string, or empty string if not found.
    """
    stop_at = _find_git_root(cwd)
    current = pathlib.Path(str(cwd)).resolve()

    for directory in [current, *current.parents]:
        dir_path = anyio.Path(str(directory))
        for name in (".hermes.md", "HERMES.md"):
            candidate = dir_path / name
            if await candidate.exists():
                try:
                    content = (await candidate.read_text(encoding="utf-8")).strip()
                    if content:
                        content = _strip_yaml_frontmatter(content)
                        threats = scan_for_threats(content, scope="context")
                        if threats:
                            return (
                                f"[BLOCKED: {name} contained potential prompt injection "
                                f"({', '.join(threats)}). Content not loaded.]"
                            )
                        result = f"## {name}\n\n{content}"
                        return _truncate_content(result, name)
                except Exception:
                    pass
        # Stop walking at the git root (inclusive)
        if stop_at is not None and str(directory) == str(stop_at):
            break
    return ""


async def _load_agents_md(cwd: anyio.Path) -> str:
    """Load AGENTS.md or agents.md, walking from cwd up to git root.

    Search order: cwd first, then each parent up to (and including) the git
    root. Returns the first match found.

    Args:
        cwd: Current working directory path.

    Returns:
        Formatted content string, or empty string if not found.
    """
    stop_at = _find_git_root(cwd)
    current = pathlib.Path(str(cwd)).resolve()

    for directory in [current, *current.parents]:
        dir_path = anyio.Path(str(directory))
        for name in ("AGENTS.md", "agents.md"):
            candidate = dir_path / name
            if await candidate.exists():
                try:
                    content = (await candidate.read_text(encoding="utf-8")).strip()
                    if content:
                        threats = scan_for_threats(content, scope="context")
                        if threats:
                            return (
                                f"[BLOCKED: {name} contained potential prompt injection "
                                f"({', '.join(threats)}). Content not loaded.]"
                            )
                        result = f"## {name}\n\n{content}"
                        return _truncate_content(result, name)
                except Exception:
                    pass
        if stop_at is not None and str(directory) == str(stop_at):
            break
    return ""


async def _load_claude_md(cwd: anyio.Path) -> str:
    """Load CLAUDE.md or claude.md, walking from cwd up to git root.

    Search order: cwd first, then each parent up to (and including) the git
    root. Returns the first match found.

    Args:
        cwd: Current working directory path.

    Returns:
        Formatted content string, or empty string if not found.
    """
    stop_at = _find_git_root(cwd)
    current = pathlib.Path(str(cwd)).resolve()

    for directory in [current, *current.parents]:
        dir_path = anyio.Path(str(directory))
        for name in ("CLAUDE.md", "claude.md"):
            candidate = dir_path / name
            if await candidate.exists():
                try:
                    content = (await candidate.read_text(encoding="utf-8")).strip()
                    if content:
                        threats = scan_for_threats(content, scope="context")
                        if threats:
                            return (
                                f"[BLOCKED: {name} contained potential prompt injection "
                                f"({', '.join(threats)}). Content not loaded.]"
                            )
                        result = f"## {name}\n\n{content}"
                        return _truncate_content(result, name)
                except Exception:
                    pass
        if stop_at is not None and str(directory) == str(stop_at):
            break
    return ""


async def _load_cursorrules(cwd: anyio.Path) -> str:
    """Load .cursorrules and/or .cursor/rules/*.mdc from cwd.

    Args:
        cwd: Current working directory path.

    Returns:
        Formatted content string, or empty string if not found.
    """
    cursorrules_content = ""

    cursorrules_file = cwd / ".cursorrules"
    if await cursorrules_file.exists():
        try:
            content = (await cursorrules_file.read_text(encoding="utf-8")).strip()
            if content:
                threats = scan_for_threats(content, scope="context")
                if threats:
                    cursorrules_content += (
                        f"[BLOCKED: .cursorrules contained potential prompt injection "
                        f"({', '.join(threats)}). Content not loaded.]\n\n"
                    )
                else:
                    cursorrules_content += f"## .cursorrules\n\n{content}\n\n"
        except Exception:
            pass

    cursor_rules_dir = cwd / ".cursor" / "rules"
    if await cursor_rules_dir.exists() and await cursor_rules_dir.is_dir():
        mdc_files: list[anyio.Path] = []
        async for f in cursor_rules_dir.iterdir():
            if str(f).endswith(".mdc"):
                mdc_files.append(f)
        for mdc_file in sorted(mdc_files, key=lambda p: str(p)):
            try:
                content = (await mdc_file.read_text(encoding="utf-8")).strip()
                if content:
                    threats = scan_for_threats(content, scope="context")
                    if threats:
                        cursorrules_content += (
                            f"[BLOCKED: .cursor/rules/{mdc_file.name} contained potential "
                            f"prompt injection ({', '.join(threats)}). Content not loaded.]\n\n"
                        )
                    else:
                        cursorrules_content += f"## .cursor/rules/{mdc_file.name}\n\n{content}\n\n"
            except Exception:
                pass

    if not cursorrules_content:
        return ""
    return _truncate_content(cursorrules_content.strip(), ".cursorrules")


async def _build_context(workspace_dir: anyio.Path | None = None) -> str:
    """Build the context tier by scanning for project context files.

    Scans workspace_dir if provided, otherwise falls back to cwd.
    Priority (first non-empty wins):
      1. .hermes.md / HERMES.md  (walks up to git root)
      2. AGENTS.md / agents.md   (directory only)
      3. CLAUDE.md / claude.md   (directory only)
      4. .cursorrules / .cursor/rules/*.mdc  (directory only)

    Args:
        workspace_dir: Workspace directory to scan. Falls back to cwd if None.

    Returns:
        Context tier string wrapped in '# Project Context' header,
        or empty string if no context files found.
    """
    search_dir = workspace_dir if workspace_dir is not None else anyio.Path(os.getcwd())

    project_context = (
        await _load_hermes_md(search_dir)
        or await _load_agents_md(search_dir)
        or await _load_claude_md(search_dir)
        or await _load_cursorrules(search_dir)
    )

    if not project_context:
        return ""

    return (
        "# Project Context\n\n"
        "The following project context files have been loaded and should be followed:\n\n" + project_context
    )


# ---------------------------------------------------------------------------
# Volatile tier
# ---------------------------------------------------------------------------


async def _load_user_md() -> str:
    """Load ~/.hermes/USER.md if it exists, with threat scan.

    Scans content with scope="strict" before injecting into system prompt.
    A threat hit returns a BLOCKED placeholder; the file is never modified.

    Returns:
        Formatted user profile section string, BLOCKED placeholder, or empty string.
    """
    user_path = anyio.Path(os.path.expanduser("~/.hermes/USER.md"))
    if not await user_path.exists():
        return ""
    try:
        content = (await user_path.read_text(encoding="utf-8")).strip()
        if not content:
            return ""
        threats = scan_for_threats(content, scope="strict")
        if threats:
            return (
                f"## User Profile\n\n[BLOCKED: USER.md contained threat pattern(s): "
                f"{', '.join(threats)}. Content not loaded into system prompt.]"
            )
        content = _truncate_content(content, "USER.md")
        return f"## User Profile\n\n{content}"
    except Exception:
        return ""


async def _build_volatile(
    workspace_dir: anyio.Path,
    model: str | None = None,
    user_snapshot: str | None = None,
) -> str:
    """Build the volatile tier with user profile, date and optional model info.

    User profile is injected first; date/session/model/provider last.
    Uses date-only precision (not minute) to keep the date line byte-stable
    for the full day, maximizing prefix cache hits on the stable/context tiers.
    Session ID is read from PSI_SESSION_ID env var; provider from PSI_AI_PROVIDER.

    When user_snapshot is provided (frozen by the System class), it is used
    directly instead of reading from disk, ensuring prefix cache stability
    within a session.

    Args:
        workspace_dir: Path to the workspace root directory.
        model: Optional model name to include in the volatile block.
        user_snapshot: Pre-built user profile section string (frozen snapshot).
            If None, reads from ~/.hermes/USER.md directly.

    Returns:
        Volatile tier string.
    """
    parts: list[str] = []

    # 1. User profile (~/.hermes/USER.md) — use frozen snapshot if provided
    if user_snapshot is not None:
        user_profile = user_snapshot
    else:
        user_profile = await _load_user_md()
    if user_profile:
        parts.append(user_profile)

    # 2. Date + session_id + model + provider (date-only for prefix cache stability)
    now = datetime.now(tz=UTC)
    date_line = f"Conversation started: {now.strftime('%A, %B %d, %Y')}"
    session_id = os.environ.get("PSI_SESSION_ID")
    if session_id:
        date_line += f"\nSession ID: {session_id}"
    if model:
        date_line += f"\nModel: {model}"
    provider = os.environ.get("PSI_AI_PROVIDER")
    if provider:
        date_line += f"\nProvider: {provider}"
    parts.append(date_line)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def _estimate_tokens(message: dict[str, Any]) -> int:
    """Estimate token count for a message using chars/4 heuristic.

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
                    chars += 4800  # ~1200 tokens estimate
    return max(1, chars // 4)


# ---------------------------------------------------------------------------
# System class
# ---------------------------------------------------------------------------


class System:
    """Hermes-style workspace system configuration.

    Implements three-tier system prompt assembly:
    - stable: identity, guidance, skills index, environment hints
    - context: project context files from cwd
    - volatile: user profile, current date, model name

    The assembled prompt is cached for the lifetime of the session.
    USER.md is frozen at first build time (frozen snapshot) so
    mid-session file writes do not disturb the prefix cache.
    Call ``invalidate()`` after context compression to force a rebuild.
    """

    def __init__(
        self,
        workspace_dir: anyio.Path,
        help_skill_name: str | None = "psi-agent-help",
    ) -> None:
        """Initialize the System instance.

        Args:
            workspace_dir: Path to the workspace directory.
            help_skill_name: Name of the help skill directory under
                ``workspace/skills/``. When non-None and the corresponding
                ``SKILL.md`` exists, a guidance line is injected into the
                stable tier pointing the LLM to that file. Set to ``None``
                to disable help guidance entirely.
        """
        self._workspace_dir = workspace_dir
        self._help_skill_name = help_skill_name
        self._cached_prompt: str | None = None
        self._user_snapshot: str | None = None

    async def _ensure_snapshots(self) -> None:
        """Build and freeze the USER.md snapshot if not yet done.

        Called once per session (or after invalidate()). The snapshot is
        frozen here so mid-session writes to USER.md do not change the
        system prompt, keeping the prefix cache stable.
        """
        if self._user_snapshot is None:
            self._user_snapshot = await _load_user_md()

    def invalidate(self) -> None:
        """Invalidate the cached system prompt, forcing a rebuild on next call.

        Clears the prompt cache and frozen USER.md snapshot so the next call
        to ``build_system_prompt()`` re-reads files from disk.
        Call this after context compression events.
        """
        self._cached_prompt = None
        self._user_snapshot = None

    async def build_system_prompt(self, model: str | None = None) -> str:
        """Build the three-tier system prompt.

        The result is cached after the first call. Subsequent calls return
        the cached value without rebuilding. Call ``invalidate()`` to force
        a rebuild (e.g., after context compression).

        Args:
            model: Optional model name for conditional tool-use enforcement
                   guidance and volatile tier model line.

        Returns:
            Complete system prompt string with all non-empty tiers joined by
            double newlines.
        """
        if self._cached_prompt is not None:
            return self._cached_prompt

        await self._ensure_snapshots()

        stable = await _build_stable(
            self._workspace_dir,
            model=model,
            help_skill_name=self._help_skill_name,
            tool_names=await _scan_tool_names(self._workspace_dir),
        )
        context = await _build_context(workspace_dir=self._workspace_dir)
        volatile = await _build_volatile(
            self._workspace_dir,
            model=model,
            user_snapshot=self._user_snapshot,
        )

        self._cached_prompt = "\n\n".join(p for p in (stable, context, volatile) if p)
        return self._cached_prompt

    async def compact_history(
        self,
        history: list[dict[str, Any]],
        complete_fn: CompleteFn,
        max_tokens: int = 4000,
        keep_recent_tokens: int | None = None,
    ) -> list[dict[str, Any]]:
        """Compact conversation history using LLM summarisation.

        When total token count exceeds max_tokens, calls complete_fn to
        summarise older messages, then prepends the summary as a system
        message. Falls back to simple truncation if complete_fn fails.

        Args:
            history: List of conversation messages with role and content.
            complete_fn: Async function for single-turn LLM conversation.
            max_tokens: Maximum tokens to keep in history.
            keep_recent_tokens: Tokens of recent messages to preserve verbatim.
                Defaults to max_tokens // 2.

        Returns:
            Compacted history list.
        """
        total_tokens = sum(_estimate_tokens(msg) for msg in history)
        if total_tokens <= max_tokens:
            return history

        keep_budget = keep_recent_tokens or (max_tokens // 2)

        # Walk backwards to find the cut point for recent messages
        accumulated = 0
        cut_index = len(history)
        for i in range(len(history) - 1, -1, -1):
            accumulated += _estimate_tokens(history[i])
            if accumulated >= keep_budget:
                cut_index = i + 1
                break
        else:
            cut_index = 0

        recent = history[cut_index:]
        older = history[:cut_index]

        if not older:
            return recent

        # Try LLM summarisation
        try:

            def _msg_text(m: dict[str, Any]) -> str:
                role = m.get("role", "unknown")
                content = m.get("content", "")
                text = content if isinstance(content, str) else "[non-text content]"
                return f"{role}: {text}"

            older_text = "\n".join(_msg_text(m) for m in older)
            summary_prompt = [
                {
                    "role": "user",
                    "content": (
                        "Please summarise the following conversation history concisely, "
                        "preserving all key facts, decisions, and context:\n\n" + older_text
                    ),
                }
            ]
            summary = await complete_fn(summary_prompt)
            summary_message: dict[str, Any] = {
                "role": "system",
                "content": f"[Summary of earlier conversation]\n{summary}",
            }
            return [summary_message, *recent]
        except Exception:
            # Fallback: simple truncation
            return recent


async def system_prompt_builder() -> str:
    """Module-level entry point used by the psi-agent session loader.

    The loader looks up an async ``system_prompt_builder`` attribute in this
    module and calls it with no arguments. We resolve the workspace root from
    this file's location and delegate to the ``System`` class.
    """
    workspace_dir = anyio.Path(__file__).parent.parent
    return await System(workspace_dir).build_system_prompt()

