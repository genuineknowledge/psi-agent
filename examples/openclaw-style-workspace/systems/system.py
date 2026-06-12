# ruff: noqa: E402, I001

from __future__ import annotations

import sys
import os as _os

# Ensure this file's directory is on sys.path so sibling modules
# (prompt_sections.py) can be imported when loaded dynamically by psi-session.
_THIS_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import contextlib
import json
import os
import platform
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import anyio
from prompt_sections import (
    BOOTSTRAP_PENDING_SECTION,
    CONTEXT_FILE_ORDER,
    DOCS_SECTION,
    DYNAMIC_CONTEXT_FILE_BASENAMES,
    EXECUTION_BIAS_SECTION,
    HEARTBEATS_SECTION,
    IDENTITY_LINE,
    MEMORY_SECTION,
    MESSAGING_SECTION_MOCK,
    OPENCLAW_CONTROL_SECTION,
    PSI_AGENT_HELP_GUIDANCE,
    SAFETY_SECTION,
    SANDBOX_SECTION_MOCK,
    SILENT_REPLIES_SECTION,
    SILENT_TOKEN,
    SUBAGENT_DELEGATION_SECTION,
    TOOL_CALL_STYLE_SECTION,
    build_authorized_senders_section,
    build_model_aliases_section,
    build_model_identity_line,
    build_reactions_section,
    build_runtime_line,
    build_skills_section,
    build_tooling_section,
    build_tts_section,
    build_workspace_section,
)

HEARTBEAT_OK = "HEARTBEAT_OK"
CACHE_BOUNDARY = "\n<!-- OPENCLAW_CACHE_BOUNDARY -->\n"

_OPENCLAW_HOME = anyio.Path(os.path.expanduser("~/.openclaw"))
_SOUL_MD_PATH = _OPENCLAW_HOME / "SOUL.md"
_USER_MD_PATH = _OPENCLAW_HOME / "USER.md"

# Character limits for volatile sections
_MEMORY_MAX_CHARS = 20_000
_USER_MD_MAX_CHARS = 10_000
_CONTEXT_FILE_MAX_CHARS = 40_000

_SKILLS_SNAPSHOT_FILE = ".skills_prompt_snapshot.json"

CompleteFn = Callable[[list[dict[str, Any]]], Awaitable[str]]

_TOOL_RESULT_MAX_CHARS = 2000
TOOL_RESULT_REAL_CONVERSATION_LOOKBACK = 20

_NON_CONVERSATION_BLOCK_TYPES = frozenset(
    ["toolCall", "toolUse", "functionCall", "thinking", "reasoning"]
)

_SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. "
    "Your task is to read a conversation between a user and an AI assistant, "
    "then produce a structured summary following the exact format specified.\n\n"
    "Do NOT continue the conversation. Do NOT respond to any questions in the "
    "conversation. ONLY output the structured summary."
)

_HISTORY_SUMMARY_PROMPT = """\
The messages above are a conversation to summarize.
Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish?]

## Constraints & Preferences
- [Any constraints or preferences mentioned by user]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]
- [Or "(none)" if not applicable]

Keep each section concise. Preserve exact file paths, function names, and error messages.\
"""

_UPDATE_SUMMARIZATION_PROMPT = """\
The messages above are NEW conversation messages to incorporate into the existing summary \
provided in <previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it

Use the EXACT same format as the existing summary.
Keep each section concise. Preserve exact file paths, function names, and error messages.\
"""

_TURN_PREFIX_SUMMARY_PROMPT = """\
This is the PREFIX of a turn that was too large to keep. The SUFFIX (recent work) is retained.

Summarize the prefix to provide context for the retained suffix:

## Original Request
[What did the user ask for in this turn?]

## Early Progress
- [Key decisions and work done in the prefix]

## Context for Suffix
- [Information needed to understand the retained recent work]

Be concise. Focus on what's needed to understand the kept suffix.\
"""

def strip_heartbeat_token(text: str) -> tuple[str, bool]:
    trimmed = text.strip()
    if not trimmed or HEARTBEAT_OK not in trimmed:
        return trimmed, False

    for wrapper in ["**", "__", "~~", "`"]:
        if trimmed.startswith(wrapper) and trimmed.endswith(wrapper):
            inner = trimmed[len(wrapper) : -len(wrapper)]
            if inner.strip() == HEARTBEAT_OK:
                return "", True
            if inner.startswith(HEARTBEAT_OK):
                return inner[len(HEARTBEAT_OK) :].strip(), True
            if inner.endswith(HEARTBEAT_OK):
                return inner[: -len(HEARTBEAT_OK)].strip(), True

    if trimmed.startswith(HEARTBEAT_OK):
        return trimmed[len(HEARTBEAT_OK) :].strip(), True
    for suffix in ["", ".", "!", "-", "---", "!!!"]:
        candidate = HEARTBEAT_OK + suffix
        if trimmed.endswith(candidate):
            return trimmed[: -len(candidate)].strip(), True

    return trimmed, False


def _has_meaningful_text(text: str) -> bool:
    if not text.strip():
        return False
    if text.strip() == SILENT_TOKEN:
        return False
    remaining, did_strip = strip_heartbeat_token(text.strip())
    if did_strip:
        return len(remaining.strip()) > 0
    return True


def has_meaningful_conversation_content(message: dict[str, Any]) -> bool:
    content = message.get("content")
    if isinstance(content, str):
        return _has_meaningful_text(content)
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            if _has_meaningful_text(block.get("text", "")):
                return True
        elif btype not in _NON_CONVERSATION_BLOCK_TYPES:
            return True
    return False


def is_real_conversation_message(
    message: dict[str, Any],
    history: list[dict[str, Any]],
    index: int,
) -> bool:
    role = message.get("role")
    if role in ("user", "assistant"):
        return has_meaningful_conversation_content(message)
    if role in ("tool", "toolResult", "tool_result"):
        start = max(0, index - TOOL_RESULT_REAL_CONVERSATION_LOOKBACK)
        for i in range(index - 1, start - 1, -1):
            if history[i].get("role") == "user" and has_meaningful_conversation_content(history[i]):
                return True
        return False
    return False


def _contains_real_conversation_messages(history: list[dict[str, Any]]) -> bool:
    return any(is_real_conversation_message(m, history, i) for i, m in enumerate(history))


def _estimate_tokens(message: dict[str, Any]) -> int:
    content = message.get("content", "")
    if isinstance(content, str):
        return len(content) // 4 + 4
    if isinstance(content, list):
        total = 4
        for block in content:
            if isinstance(block, dict):
                total += len(str(block.get("text") or block.get("content") or "")) // 4
        return total
    return 4


def _truncate_for_summary(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n\n[... {len(text) - max_chars} more characters truncated]"


def _find_cut_point(history: list[dict[str, Any]], keep_tokens: int) -> tuple[int, bool]:
    accumulated = 0
    for i in range(len(history) - 1, -1, -1):
        accumulated += _estimate_tokens(history[i])
        if accumulated >= keep_tokens:
            cut = i
            # Check if we cut in the middle of an assistant turn
            is_split = history[cut].get("role") == "assistant" and cut > 0
            return cut, is_split
    return 0, False


def _find_turn_start(history: list[dict[str, Any]], from_index: int) -> int:
    for i in range(from_index - 1, -1, -1):
        if history[i].get("role") == "user":
            return i + 1
    return 0


def _build_summarization_prompt(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            if isinstance(content, str):
                parts.append(f"[User]: {content}")
            elif isinstance(content, list):
                texts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                if texts:
                    parts.append(f"[User]: {' '.join(texts)}")
        elif role == "assistant":
            if isinstance(content, str):
                parts.append(f"[Assistant]: {content}")
            elif isinstance(content, list):
                thinking, texts, calls = [], [], []
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    btype = b.get("type")
                    if btype in ("thinking", "reasoning"):
                        thinking.append(b.get("thinking") or b.get("content") or "")
                    elif btype == "text":
                        texts.append(b.get("text", ""))
                    elif btype in ("tool_use", "function_call"):
                        name = b.get("name") or b.get("function", {}).get("name", "")
                        calls.append(name)
                if thinking:
                    parts.append(f"[Assistant thinking]: {' '.join(thinking)}")
                if texts:
                    parts.append(f"[Assistant]: {' '.join(texts)}")
                if calls:
                    parts.append(f"[Assistant tool calls]: {'; '.join(calls)}")
        elif role in ("tool", "toolResult", "tool_result"):
            if isinstance(content, str):
                truncated = _truncate_for_summary(content, _TOOL_RESULT_MAX_CHARS)
                parts.append(f"[Tool result]: {truncated}")
            elif isinstance(content, list):
                text = " ".join(
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
                if text:
                    truncated = _truncate_for_summary(text, _TOOL_RESULT_MAX_CHARS)
                    parts.append(f"[Tool result]: {truncated}")
    return "<conversation>\n" + "\n\n".join(parts) + "\n</conversation>"


def _strip_frontmatter(content: str) -> str:
    if not content.startswith("---"):
        return content
    end = content.find("\n---", 3)
    if end == -1:
        return content
    return content[end + len("\n---") :].lstrip("\n")


async def _read_file_optional(path: anyio.Path, max_chars: int = 0) -> str | None:
    if not await path.exists():
        return None
    try:
        content = await path.read_text(encoding="utf-8", errors="replace")
        if max_chars > 0 and len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[... truncated at {max_chars} chars]"
        return content
    except OSError:
        return None


async def _read_bootstrap_file(path: anyio.Path, max_chars: int = 0) -> str | None:
    content = await _read_file_optional(path, max_chars)
    if content is None:
        return None
    return _strip_frontmatter(content)


async def _load_soul_md() -> str:
    content = await _read_bootstrap_file(_SOUL_MD_PATH)
    if content and content.strip():
        return content.strip()
    return IDENTITY_LINE


async def _build_skills_index(workspace_dir: anyio.Path) -> str:
    skills_dir = workspace_dir / "skills"
    if not await skills_dir.exists():
        return ""

    # Collect skill dirs and their SKILL.md mtimes for cache invalidation
    skill_entries: list[tuple[str, anyio.Path]] = []
    async for entry in skills_dir.iterdir():
        if await entry.is_dir():
            skill_md = entry / "SKILL.md"
            if await skill_md.exists():
                skill_entries.append((entry.name, skill_md))

    if not skill_entries:
        return ""

    # Build manifest for cache comparison: {skill_name: mtime_ns}
    # (category changes invalidate cache because they affect XML structure)
    manifest: dict[str, int] = {}
    for name, skill_md in skill_entries:
        stat = await skill_md.stat()
        manifest[name] = stat.st_mtime_ns

    # Try cache
    snapshot_path = workspace_dir / _SKILLS_SNAPSHOT_FILE
    with contextlib.suppress(OSError, json.JSONDecodeError, KeyError):
        if await snapshot_path.exists():
            raw = await snapshot_path.read_text(encoding="utf-8")
            cached = json.loads(raw)
            if cached.get("manifest") == manifest:
                return cached.get("skills_xml", "")

    # Parse SKILL.md files
    skills: list[dict[str, str]] = []
    for name, skill_md in sorted(skill_entries):
        content = await _read_file_optional(skill_md)
        if not content:
            continue
        # Parse YAML frontmatter
        skill_info: dict[str, str] = {"name": name, "description": "", "category": ""}
        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end != -1:
                fm = content[3:end]
                for line in fm.splitlines():
                    if ":" in line:
                        key, _, val = line.partition(":")
                        skill_info[key.strip()] = val.strip().strip('"').strip("'")
        if not skill_info["description"]:
            # Fallback: first non-empty non-header line after frontmatter
            body = _strip_frontmatter(content)
            for line in body.splitlines():
                line = line.strip().lstrip("#").strip()
                if line:
                    skill_info["description"] = line
                    break
        skills.append(skill_info)

    if not skills:
        return ""

    # Determine if any skill has a non-empty category → grouped mode
    use_groups = any(s.get("category") for s in skills)

    lines = ["<available_skills>"]
    if use_groups:
        # Group skills by category; skills without category → "general"
        groups: dict[str, list[dict[str, str]]] = {}
        for s in skills:
            cat = s.get("category") or "general"
            groups.setdefault(cat, []).append(s)
        for cat, cat_skills in groups.items():
            lines.append(f'  <category name="{cat}">')
            for s in cat_skills:
                lines.append(f'    <skill name="{s["name"]}"')
                if s.get("description"):
                    lines.append(f'      description="{s["description"]}"')
                lines.append("    />")
            lines.append("  </category>")
    else:
        for s in skills:
            lines.append(f'  <skill name="{s["name"]}"')
            if s.get("description"):
                lines.append(f'    description="{s["description"]}"')
            lines.append("  />")
    lines.append("</available_skills>")
    skills_xml = "\n".join(lines)

    # Save cache (manifest keyed by mtime_ns; category changes force re-parse
    # because mtime updates when SKILL.md is edited)
    with contextlib.suppress(OSError):
        await snapshot_path.write_text(
            json.dumps({"manifest": manifest, "skills_xml": skills_xml}, indent=2),
            encoding="utf-8",
        )

    return skills_xml


async def _build_context_file(workspace_dir: anyio.Path) -> str:
    """Scan workspace for a project context file and return its content.

    Priority order (mirrors OpenClaw's bootstrap file loading):
      1. .hermes.md / HERMES.md  (walk up to git root)
      2. AGENTS.md
      3. CLAUDE.md
      4. .cursorrules

    Args:
        workspace_dir: Path to the workspace directory.

    Returns:
        Formatted '# Project Context' block, or empty string if none found.
    """
    # 1. .hermes.md / HERMES.md — walk up to git root
    search_dir = workspace_dir
    for _ in range(10):  # cap at 10 levels
        for name in (".hermes.md", "HERMES.md"):
            candidate = search_dir / name
            content = await _read_bootstrap_file(candidate, _CONTEXT_FILE_MAX_CHARS)
            if content and content.strip():
                return f"# Project Context\n\n{content.strip()}"
        git_dir = search_dir / ".git"
        if await git_dir.exists():
            break
        parent = search_dir.parent
        if parent == search_dir:
            break
        search_dir = parent

    # 2-3. Workspace-local files - skip files already loaded by _build_bootstrap_files
    # (AGENTS.md is in CONTEXT_FILE_ORDER so it appears in Bootstrap Files block)
    for name in ("CLAUDE.md", ".cursorrules"):
        candidate = workspace_dir / name
        content = await _read_bootstrap_file(candidate, _CONTEXT_FILE_MAX_CHARS)
        if content and content.strip():
            return f"# Project Context\n\n{content.strip()}"

    return ""


async def _build_bootstrap_files(workspace_dir: anyio.Path) -> str:
    """Load OpenClaw-style bootstrap context files from the workspace root.

    Files are loaded in the order defined by CONTEXT_FILE_ORDER (lowercase
    basename → priority). Dynamic files (heartbeat.md) are excluded here
    and injected separately in the dynamic suffix.

    Each file is wrapped as '## FILENAME\\n\\n<content>'.
    Missing files are silently skipped.

    Args:
        workspace_dir: Path to the workspace directory.

    Returns:
        Combined bootstrap block, or empty string if no files found.
    """
    # Collect all candidate files from the workspace root
    candidates: list[tuple[int, str]] = []
    try:
        async for entry in workspace_dir.iterdir():
            if not await entry.is_file():
                continue
            name_lower = entry.name.lower()
            if name_lower in DYNAMIC_CONTEXT_FILE_BASENAMES:
                continue  # handled in dynamic suffix
            if name_lower == "memory.md":
                continue  # memory is volatile, injected in dynamic suffix only
            priority = CONTEXT_FILE_ORDER.get(name_lower)
            if priority is not None:
                candidates.append((priority, entry.name))
    except OSError:
        return ""

    candidates.sort()

    sections: list[str] = []
    for _, filename in candidates:
        content = await _read_bootstrap_file(workspace_dir / filename, _CONTEXT_FILE_MAX_CHARS)
        if content and content.strip():
            sections.append(f"## {filename}\n\n{content.strip()}")

    if not sections:
        return ""
    return "# Bootstrap Files\n\n" + "\n\n".join(sections)


def _build_runtime_info(model: str | None, tool_names: list[str] | None) -> str:
    """Build the Runtime: line.

    Reads OPENCLAW_CHANNEL, OPENCLAW_AGENT_ID, OPENCLAW_MODEL env vars.
    Mirrors OpenClaw's buildRuntimeLine() (system-prompt.ts:1331-1374).

    Args:
        model: Current model name (from session layer).
        tool_names: Active tool names (unused here, kept for API compat).

    Returns:
        Single-line runtime string.
    """
    effective_model = os.environ.get("OPENCLAW_MODEL") or model or None
    return build_runtime_line(
        agent_id=os.environ.get("OPENCLAW_AGENT_ID") or None,
        host=platform.node() or None,
        os_str=f"{platform.system()} {platform.release()}".strip() or None,
        arch=platform.machine() or None,
        model=effective_model,
        shell=(os.environ.get("SHELL", "").split("/")[-1] or None),
        channel=os.environ.get("OPENCLAW_CHANNEL") or None,
    )


def _build_datetime_section() -> str:
    """Build the ## Current Date & Time section with user timezone.

    Reads OPENCLAW_TIMEZONE env var; defaults to UTC.
    """
    tz = os.environ.get("OPENCLAW_TIMEZONE", "UTC")
    now = datetime.now()
    return (
        "## Current Date & Time\n"
        f"Date: {now.strftime('%Y-%m-%d')}\n"
        f"Time: {now.strftime('%H:%M:%S')}\n"
        f"Time zone: {tz}"
    )


# ---------------------------------------------------------------------------
# Dynamic suffix sections
# ---------------------------------------------------------------------------


async def _build_volatile(workspace_dir: anyio.Path) -> str:
    """Build the volatile (dynamic) memory + user profile sections.

    Reads workspace/memory.md and ~/.openclaw/USER.md.
    Truncated at _MEMORY_MAX_CHARS and _USER_MD_MAX_CHARS respectively.

    Args:
        workspace_dir: Path to the workspace directory.

    Returns:
        Combined volatile section string, or empty string.
    """
    parts: list[str] = []

    memory_content = await _read_file_optional(workspace_dir / "memory.md", _MEMORY_MAX_CHARS)
    if memory_content and memory_content.strip():
        parts.append(f"## Memory\n\n{memory_content.strip()}")

    user_content = await _read_file_optional(_USER_MD_PATH, _USER_MD_MAX_CHARS)
    if user_content and user_content.strip():
        user_body = _strip_frontmatter(user_content).strip()
        if user_body:
            parts.append(f"## User Profile\n\n{user_body}")

    return "\n\n".join(parts)


def _build_provider_contribution() -> str:
    """Build provider contribution section.

    Reads OPENCLAW_PROVIDER_HINT env var. If set, injects it.
    Otherwise returns empty (provider contribution is a mock extension point).

    Mirrors OpenClaw's ProviderSystemPromptContribution extension point A.
    """
    hint = os.environ.get("OPENCLAW_PROVIDER_HINT", "").strip()
    if hint:
        return f"## Provider Hints\n{hint}"
    return ""


def _build_authorized_senders_section() -> str:
    """Build authorized senders section from OPENCLAW_AUTHORIZED_SENDERS env var.

    Reads a comma-separated list from the environment variable.
    Returns empty string if not set.
    """
    raw = os.environ.get("OPENCLAW_AUTHORIZED_SENDERS", "").strip()
    if not raw:
        return ""
    senders = [s.strip() for s in raw.split(",") if s.strip()]
    return build_authorized_senders_section(senders)


def _build_model_aliases_section() -> str:
    """Build model aliases section from OPENCLAW_MODEL_ALIASES env var (JSON).

    Returns empty string if not configured.
    """
    raw = os.environ.get("OPENCLAW_MODEL_ALIASES", "").strip()
    if not raw:
        return ""
    try:
        aliases: dict[str, str] = json.loads(raw)
        lines = [alias + " → " + model_id for alias, model_id in aliases.items()]
        return build_model_aliases_section(lines)
    except json.JSONDecodeError:
        return ""


async def _scan_tool_names(workspace_dir: anyio.Path) -> list[str]:
    """Scan workspace/tools/ and return tool names derived from .py filenames.

    Used as fallback when build_system_prompt() is called without tool_names
    (e.g. by psi-session which does not pass tool_names to the System class).

    Args:
        workspace_dir: Path to the workspace directory.

    Returns:
        Sorted list of tool name strings (file stems, excluding private files).
    """
    tools_dir = workspace_dir / "tools"
    if not await tools_dir.exists():
        return []
    names: list[str] = []
    async for entry in tools_dir.iterdir():
        if await entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
            names.append(entry.stem)
    return sorted(names)


async def _build_dynamic_context_files(workspace_dir: anyio.Path) -> str:
    """Read dynamic context files and return combined section string.

    Scans workspace_dir for files in DYNAMIC_CONTEXT_FILE_BASENAMES (case-
    insensitive). Each found file is included as a '## <FILENAME>' block.
    Mirrors OpenClaw's heartbeat.md injection below the cache boundary.

    Args:
        workspace_dir: Path to the workspace directory.

    Returns:
        Combined sections string, or empty string if no files found.
    """
    parts: list[str] = []
    dynamic_names_lower = {n.lower() for n in DYNAMIC_CONTEXT_FILE_BASENAMES}

    async for entry in workspace_dir.iterdir():
        if not await entry.is_file():
            continue
        if entry.name.lower() not in dynamic_names_lower:
            continue
        content = await _read_bootstrap_file(entry, _CONTEXT_FILE_MAX_CHARS)
        if content and content.strip():
            parts.append(f"## {entry.name}\n\n{content.strip()}")

    return "\n\n".join(parts)


class System:
    def __init__(self, workspace_dir: anyio.Path) -> None:
        self._workspace_dir = workspace_dir
        self._previous_summary: str | None = None

    async def build_system_prompt(
        self,
        model: str | None = None,
        tool_names: list[str] | None = None,
        prompt_mode: str = "full",
        help_skill_name: str | None = None,
    ) -> str:
        tools = tool_names or await _scan_tool_names(self._workspace_dir)
        is_minimal = prompt_mode != "full"

        # ── Stable prefix ────────────────────────────────────────────────
        identity = await _load_soul_md()
        if not is_minimal:
            skills_xml = await _build_skills_index(self._workspace_dir)
            context_file = await _build_context_file(self._workspace_dir)
            bootstrap = await _build_bootstrap_files(self._workspace_dir)
        else:
            skills_xml = ""
            context_file = ""
            bootstrap = ""

        stable_parts: list[str] = [identity]

        # Help skill guidance (psi-agent extension) — after identity, before Tooling
        if help_skill_name is not None:
            skill_md = self._workspace_dir / "skills" / help_skill_name / "SKILL.md"
            if await skill_md.exists():
                stable_parts += ["", PSI_AGENT_HELP_GUIDANCE.format(path=str(skill_md))]

        stable_parts += [
            "",
            build_tooling_section(tools),
            "",
            TOOL_CALL_STYLE_SECTION,
            "",
            EXECUTION_BIAS_SECTION,
            "",
            SAFETY_SECTION,
        ]

        if not is_minimal:
            stable_parts += ["", OPENCLAW_CONTROL_SECTION]

        # Sub-agent delegation [mock unless wired] — skip in minimal
        if not is_minimal:
            stable_parts += ["", SUBAGENT_DELEGATION_SECTION]

        # Skills — skip in minimal
        if not is_minimal:
            skills_section = build_skills_section(skills_xml)
            if skills_section:
                stable_parts += ["", skills_section]

        # Memory guidance — skip in minimal
        if not is_minimal:
            stable_parts += ["", MEMORY_SECTION]

        # Workspace (absolute path, mirrors OpenClaw behaviour)
        workspace_abs = str(await self._workspace_dir.resolve())
        stable_parts += ["", build_workspace_section(workspace_abs)]

        # Docs — skip in minimal
        if not is_minimal:
            stable_parts += ["", DOCS_SECTION]

        # Sandbox [mock] — skip in minimal
        if not is_minimal:
            stable_parts += ["", SANDBOX_SECTION_MOCK]

        # Authorized senders (from env) — skip in minimal
        if not is_minimal:
            auth_senders = _build_authorized_senders_section()
            if auth_senders:
                stable_parts += ["", auth_senders]

        # Bootstrap context files (SOUL/IDENTITY/USER/TOOLS/BOOTSTRAP ordered) — skip in minimal
        if not is_minimal and bootstrap:
            stable_parts += ["", bootstrap]

        # Project context (AGENTS.md / CLAUDE.md / .hermes.md) — skip in minimal
        if not is_minimal and context_file:
            stable_parts += ["", context_file]

        # Bootstrap pending — inject if BOOTSTRAP.md still exists in workspace — skip in minimal
        if not is_minimal:
            bootstrap_md_path = self._workspace_dir / "BOOTSTRAP.md"
            if await bootstrap_md_path.exists():
                stable_parts += ["", BOOTSTRAP_PENDING_SECTION]

        # Silent replies hint lives in stable prefix in OpenClaw
        stable_parts += ["", SILENT_REPLIES_SECTION]

        stable_prefix = "\n".join(stable_parts)

        # ── Dynamic suffix ────────────────────────────────────────────────
        dynamic_parts: list[str] = []

        # Webchat canvas / messaging channel guidance
        channel = os.environ.get("OPENCLAW_CHANNEL", "")
        dynamic_parts += [MESSAGING_SECTION_MOCK, ""]

        # TTS / Voice
        tts_hint = os.environ.get("OPENCLAW_TTS_HINT", "").strip()
        if tts_hint:
            tts_section = build_tts_section(tts_hint)
            if tts_section:
                dynamic_parts += [tts_section, ""]

        # Heartbeats
        dynamic_parts += [HEARTBEATS_SECTION, ""]

        # Reactions (OPENCLAW_REACTIONS=minimal|extensive, OPENCLAW_CHANNEL)
        reactions_level = os.environ.get("OPENCLAW_REACTIONS", "").strip().lower()
        if reactions_level in ("minimal", "extensive") and channel:
            dynamic_parts += [build_reactions_section(reactions_level, channel), ""]

        # Model aliases
        aliases_section = _build_model_aliases_section()
        if aliases_section:
            dynamic_parts += [aliases_section, ""]

        # Model identity line
        model_identity = build_model_identity_line(model)
        if model_identity:
            dynamic_parts += [model_identity, ""]

        # Volatile memory + user profile
        volatile = await _build_volatile(self._workspace_dir)
        if volatile:
            dynamic_parts += [volatile, ""]

        # Dynamic context files (heartbeat.md, openclaw.md, etc.)
        dynamic_ctx = await _build_dynamic_context_files(self._workspace_dir)
        if dynamic_ctx:
            dynamic_parts += [dynamic_ctx, ""]

        # Provider contribution (OPENCLAW_PROVIDER_HINT)
        provider = _build_provider_contribution()
        if provider:
            dynamic_parts += [provider, ""]

        # Date/time
        dynamic_parts += [_build_datetime_section(), ""]

        # Runtime info (last — highest churn)
        dynamic_parts.append(_build_runtime_info(model, tools))

        # Remove trailing empty strings
        while dynamic_parts and dynamic_parts[-1] == "":
            dynamic_parts.pop()

        dynamic_suffix = "\n".join(dynamic_parts)

        return stable_prefix + CACHE_BOUNDARY + dynamic_suffix

    async def compact_history(
        self,
        history: list[dict[str, Any]],
        complete_fn: CompleteFn,
        max_tokens: int = 4000,
        keep_recent_tokens: int | None = None,
    ) -> list[dict[str, Any]]:
        if not _contains_real_conversation_messages(history):
            return history

        if keep_recent_tokens is None:
            keep_recent_tokens = max_tokens // 2

        total = sum(_estimate_tokens(m) for m in history)
        if total <= max_tokens:
            return history

        cut_index, is_split_turn = _find_cut_point(history, keep_recent_tokens)

        if cut_index <= 0:
            messages_to_summarize = history
            turn_prefix_messages: list[dict[str, Any]] = []
            recent_messages: list[dict[str, Any]] = []
        elif is_split_turn:
            turn_start = _find_turn_start(history, cut_index)
            messages_to_summarize = history[:turn_start]
            turn_prefix_messages = history[turn_start:cut_index]
            recent_messages = history[cut_index:]
        else:
            messages_to_summarize = history[:cut_index]
            turn_prefix_messages = []
            recent_messages = history[cut_index:]

        summary_parts: list[str] = []

        # Main history summary
        if messages_to_summarize:
            prompt = _build_summarization_prompt(messages_to_summarize)
            if self._previous_summary:
                prompt = (
                    f"<previous-summary>\n{self._previous_summary}\n</previous-summary>\n\n"
                    + prompt
                    + "\n\n"
                    + _UPDATE_SUMMARIZATION_PROMPT
                )
            else:
                prompt = prompt + "\n\n" + _HISTORY_SUMMARY_PROMPT

            try:
                history_summary = await complete_fn(
                    [
                        {"role": "system", "content": _SUMMARIZATION_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ]
                )
                summary_parts.append(history_summary)
            except Exception:
                # Fallback: truncate instead of summarise
                return history[-len(recent_messages) :] if recent_messages else history[-20:]

        # Split-turn prefix summary
        if is_split_turn and turn_prefix_messages:
            prompt = _build_summarization_prompt(turn_prefix_messages)
            try:
                prefix_summary = await complete_fn(
                    [
                        {"role": "system", "content": _SUMMARIZATION_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt + "\n\n" + _TURN_PREFIX_SUMMARY_PROMPT},
                    ]
                )
                summary_parts.append(
                    f"\n\n---\n\n**Turn Context (split turn):**\n\n{prefix_summary}"
                )
            except Exception:
                pass

        if summary_parts:
            combined = "".join(summary_parts)
            self._previous_summary = combined
            summary_msg: dict[str, Any] = {
                "role": "assistant",
                "content": f"[Conversation Summary]\n{combined}",
            }
            return [summary_msg, *recent_messages]

        return recent_messages
