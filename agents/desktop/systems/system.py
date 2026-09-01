# ruff: noqa: E402, I001, T201

"""System prompt builder for the Haitun agent workspace.

This merges three ideas into one workspace:

* An OpenClaw-style prompt engine (layered builder + a per-turn context block,
  skills index, bootstrap context files) - **de-branded**, with **all
  configuration kept inside the workspace** (there is no global config dir).
* The Workflow authoring capability (flows index + authoring guidance),
  fully merged from the standalone workflow workspace.
* A fixed Haitun agent persona, always stated in the system prompt.

``system_prompt_builder()``, ``system_prompt_rebuild_checker()``,
``turn_context_builder()``, ``compact_history()``, and ``system_after_turn()``
are invoked by psi-agent's session loader. ``System.after_turn`` and the
self-evolution helpers below are **intentionally kept but currently un-wired**
- they are future-extension hooks (see AGENTS.md).  Do not delete them as "dead
code"; they exist on purpose.
"""

from __future__ import annotations

import sys
import os as _os

# Context compaction is engine behavior, not product expression: this block was
# byte-for-byte identical in 11 of the 12 example workspaces, so a fix had to be
# made eleven times.  It now lives in the kernel.  Re-exported rather than
# imported inline because the hook lookup is `getattr(module, "compact_history")`
# -- the name has to be resolvable as a module attribute here.  A workspace that
# needs different behavior defines `compact_history` itself further down; the
# later binding wins.
from psi_agent.session._compaction import (  # noqa: F401
    RECENT_TURNS_KEPT_VERBATIM,
    SUMMARIZE_TASK,
    SUMMARY_MAX_CHARS,
    TRANSCRIPT_IS_DATA,
    _cap_summary,
    _escape_transcript,
    compact_history,
)

# Ensure this file's directory is on sys.path so sibling modules
# (prompt_sections.py) can be imported when loaded dynamically by psi-session.
_THIS_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

_TOOLS_DIR = _os.path.join(_os.path.dirname(_THIS_DIR), "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import contextlib
import hashlib
import importlib
import json
import logging
import os
import platform
import re
import types
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio

try:
    from psi_agent.session.runtime_context import get_agent as _runtime_agent
    from psi_agent.session.runtime_context import get_workspace as _runtime_workspace
except ImportError:  # pragma: no cover — standalone import without editable install

    def _runtime_workspace() -> str:
        return ""

    def _runtime_agent() -> str:
        return ""


def _package_fallback() -> str:
    """This package root, derived from ``__file__`` (``systems/`` parent).

    Last resort only. It follows the file, so it goes wrong the moment the
    package is re-laid-out — which is why every caller below prefers the root
    the kernel hands down (``agent_raw``) or binds per turn (``get_agent()``).
    """
    return str(Path(__file__).resolve().parents[1])


def _resolve_agent(agent_raw: str = "") -> anyio.Path:
    """Agent package root: explicit *agent_raw* → ``get_agent()`` → ``__file__``.

    Same priority chain as ``tools/_runtime_paths.agent_dir`` — the kernel is
    authoritative when it says something, the per-turn ContextVar covers the
    rest, and ``__file__`` only answers when nothing else did.
    """
    for candidate in (agent_raw, _runtime_agent()):
        text = (candidate or "").strip()
        if text:
            return anyio.Path(text)
    return anyio.Path(_package_fallback())


from prompt_sections import (
    BOOTSTRAP_PENDING_SECTION,
    CONTEXT_FILE_ORDER,
    CITATIONS_TRUSTWORTHINESS_SECTION,
    CLOSING_QUESTIONS_SECTION,
    CLARIFY_ASSUMPTIONS_SECTION,
    CODE_CONVENTIONS_SECTION,
    DELIVERABLES_AS_FILES_SECTION,
    DYNAMIC_CONTEXT_FILE_BASENAMES,
    ERROR_HANDLING_RETRY_SECTION,
    EXECUTION_BIAS_SECTION,
    IDENTITY_LINE,
    LANGUAGE_LOCALIZATION_SECTION,
    PLANNING_PROGRESS_SECTION,
    PSI_AGENT_HELP_GUIDANCE,
    FUSION_MEMORY_SECTION,
    SAFETY_SECTION,
    SEND_FILES_SECTION,
    SILENT_REPLIES_SECTION,
    SILENT_TOKEN,
    SESSION_MANAGEMENT_SECTION,
    SKILL_AUTHORING_SECTION,
    SYSTEM_CLI_TOOLS_SECTION,
    STRUCTURED_TABLES_SECTION,
    TASK_PLANNING_SECTION,
    TASK_SELF_CHECK_SECTION,
    SUBAGENT_DELEGATION_SECTION,
    TOOL_CALL_STYLE_SECTION,
    WEB_SEARCH_RECENCY_SECTION,
    build_model_identity_line,
    build_runtime_line,
    build_skills_section,
    build_tooling_section,
    build_workspace_section,
)

logger = logging.getLogger(__name__)

HEARTBEAT_OK = "HEARTBEAT_OK"


# Skill whose presence injects the ## Help guidance section.
HELP_SKILL_NAME = "psi-agent-help"

# Character limits for volatile / context sections
_USER_MD_MAX_CHARS = 10_000
_CONTEXT_FILE_MAX_CHARS = 40_000

_SKILLS_SNAPSHOT_FILE = ".skills_prompt_snapshot.json"

_SUPERVISOR_MANAGERS: dict[str, Any] = {}

# Global skills directory, shared across workspaces (AGENTS.md ecosystem
# convention). Each skill lives at ~/.agent/skills/<name>/SKILL.md, mirroring
# the per-workspace skills/ layout. Workspace skills override global ones on
# name conflict.
_GLOBAL_AGENT_SKILLS_DIR = anyio.Path(os.path.expanduser("~/.agent/skills"))

# Global AGENTS.md, shared across workspaces (AGENTS.md ecosystem convention).
# Loaded as its own bootstrap section, in addition to the workspace-root
# AGENTS.md that ``_build_bootstrap_files`` already handles.
_GLOBAL_AGENT_HOME = anyio.Path(os.path.expanduser("~/.agent"))

CompleteFn = Callable[[list[dict[str, Any]]], Awaitable[str]]
ReviewCompleteFn = Callable[
    [list[dict[str, Any]], list[dict[str, Any]] | None],
    Awaitable[dict[str, Any]],
]
ToolExecutors = dict[str, Callable[..., Awaitable[Any]]]

_TOOL_RESULT_MAX_CHARS = 2000
TOOL_RESULT_REAL_CONVERSATION_LOOKBACK = 20

_NON_CONVERSATION_BLOCK_TYPES = frozenset(["toolCall", "toolUse", "functionCall", "thinking", "reasoning"])

# Self-evolution (future extension; not invoked by the framework yet)
MAX_SELF_EVOLUTION_ITERATIONS = 6
SELF_EVOLUTION_TOOL_THRESHOLD = 2

_SELF_EVOLUTION_PROMPT = (
    "Review the completed turn and decide whether this workspace should learn from it."
    """

Only update workspace assets when the conversation produced reusable knowledge:
- workflow-authoring patterns that should become reusable curated flows
- recurring structure, validation, or runtime practices
- corrections to an agent-created skill or a new class-level skill

Use `skill_manage` for reusable non-flow procedures.
Use `flow_manage` for reusable workflow templates.

Rules:
1. Do not update anything for one-off task facts, transient errors, secrets, local credentials, or user-private data.
2. Do not patch user-authored skills or the immutable `skills/workflow/` runtime skill.
3. Prefer patching an existing agent-created asset over creating a narrow duplicate.
4. If nothing is worth saving, reply exactly: Nothing to save.
"""
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


# ---------------------------------------------------------------------------
# Heartbeat / silent-token helpers
# ---------------------------------------------------------------------------


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
                texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
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
                text = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                if text:
                    truncated = _truncate_for_summary(text, _TOOL_RESULT_MAX_CHARS)
                    parts.append(f"[Tool result]: {truncated}")
    return "<conversation>\n" + "\n\n".join(parts) + "\n</conversation>"


# ---------------------------------------------------------------------------
# File / context helpers (all workspace-relative - no global config)
# ---------------------------------------------------------------------------


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


async def _load_soul_md(workspace_dir: anyio.Path) -> str:
    """Identity line is always the Haitun agent persona; workspace SOUL.md augments it."""
    soul = await _read_bootstrap_file(workspace_dir / "SOUL.md")
    if soul and soul.strip():
        return IDENTITY_LINE + "\n\n" + soul.strip()
    return IDENTITY_LINE


async def _collect_skill_dirs(skills_dir: anyio.Path) -> list[tuple[str, anyio.Path]]:
    """Return (name, SKILL.md) pairs for every skill dir under ``skills_dir``.

    Returns an empty list if the directory is missing or unreadable.
    """
    entries: list[tuple[str, anyio.Path]] = []
    if not await skills_dir.exists():
        return entries
    with contextlib.suppress(OSError):
        async for entry in skills_dir.iterdir():
            if await entry.is_dir():
                skill_md = entry / "SKILL.md"
                if await skill_md.exists():
                    entries.append((entry.name, skill_md))
    return entries


async def _build_skills_index(workspace_dir: anyio.Path) -> str:
    skills_dir = workspace_dir / "skills"

    # Merge global (~/.agent/skills) with workspace skills. Workspace skills
    # override globals on name conflict, so collect globals first and let the
    # workspace pass replace them. This keeps the "nearest wins" convention
    # consistent with AGENTS.md/CLAUDE.md context lookup.
    skill_md_by_name: dict[str, anyio.Path] = {}
    for name, skill_md in await _collect_skill_dirs(_GLOBAL_AGENT_SKILLS_DIR):
        skill_md_by_name[name] = skill_md
    for name, skill_md in await _collect_skill_dirs(skills_dir):
        skill_md_by_name[name] = skill_md

    skill_entries: list[tuple[str, anyio.Path]] = sorted(skill_md_by_name.items())

    if not skill_entries:
        return ""

    manifest: dict[str, str] = {}
    skill_contents: dict[str, str] = {}
    for name, skill_md in skill_entries:
        content = await _read_file_optional(skill_md)
        if content is None:
            continue
        skill_contents[name] = content
        manifest[name] = hashlib.sha256(content.encode("utf-8")).hexdigest()

    if not skill_contents:
        return ""

    snapshot_path = workspace_dir / _SKILLS_SNAPSHOT_FILE
    with contextlib.suppress(OSError, json.JSONDecodeError, KeyError):
        if await snapshot_path.exists():
            raw = await snapshot_path.read_text(encoding="utf-8")
            cached = json.loads(raw)
            if cached.get("manifest") == manifest:
                return cached.get("skills_xml", "")

    skills: list[dict[str, str]] = []
    for name in sorted(skill_contents):
        content = skill_contents[name]
        if not content:
            continue
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
            body = _strip_frontmatter(content)
            for line in body.splitlines():
                line = line.strip().lstrip("#").strip()
                if line:
                    skill_info["description"] = line
                    break
        skills.append(skill_info)

    if not skills:
        return ""

    use_groups = any(s.get("category") for s in skills)

    lines = ["<available_skills>"]
    if use_groups:
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

    with contextlib.suppress(OSError):
        await snapshot_path.write_text(
            json.dumps({"manifest": manifest, "skills_xml": skills_xml}, indent=2),
            encoding="utf-8",
        )

    return skills_xml


async def _build_flows_index(flows_dir: anyio.Path) -> str:
    """Index curated + generated workflow assets."""
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
            candidates = (
                task_dir / f"{task_dir.name}.workflow",
                task_dir / f"{task_dir.name}.g4",
                task_dir / f"{task_dir.name}.flow.ts",
            )
            selected: anyio.Path | None = None
            for candidate in candidates:
                if await candidate.exists():
                    selected = candidate
                    break
            if selected is None:
                for pattern in ("*.workflow", "*.g4", "*.flow.ts"):
                    async for flow_file in task_dir.glob(pattern):
                        selected = flow_file
                        break
                    if selected is not None:
                        break
            if selected is not None:
                task_lines.append(f"    - {task_dir.name}: {selected.name}")

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


async def _build_context_file(workspace_dir: anyio.Path) -> str:
    """Scan for a project context file (CLAUDE.md / .cursorrules) and return it.

    AGENTS.md is loaded by ``_build_bootstrap_files`` already, so it is skipped
    here to avoid duplication.
    """
    for name in ("CLAUDE.md", ".cursorrules"):
        candidate = workspace_dir / name
        content = await _read_bootstrap_file(candidate, _CONTEXT_FILE_MAX_CHARS)
        if content and content.strip():
            return f"# Project Context\n\n{content.strip()}"
    return ""


async def _build_bootstrap_files(workspace_dir: anyio.Path) -> str:
    """Load workspace-root bootstrap context files in CONTEXT_FILE_ORDER.

    Dynamic files (heartbeat.md) are excluded and injected in the dynamic suffix.
    SOUL.md / USER.md are handled separately (identity line / volatile profile).
    """
    candidates: list[tuple[int, str]] = []
    try:
        async for entry in workspace_dir.iterdir():
            if not await entry.is_file():
                continue
            name_lower = entry.name.lower()
            if name_lower in DYNAMIC_CONTEXT_FILE_BASENAMES:
                continue
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


async def _build_global_agents_md() -> str:
    """Load the global ~/.agent/AGENTS.md (AGENTS.md ecosystem convention).

    This augments the workspace-root AGENTS.md (loaded by
    ``_build_bootstrap_files``) with cross-workspace instructions. The source
    is labelled so its global scope is explicit in the prompt. Accepts either
    ``AGENTS.md`` or ``agents.md``; returns an empty string when absent.
    """
    for name in ("AGENTS.md", "agents.md"):
        content = await _read_bootstrap_file(_GLOBAL_AGENT_HOME / name, _CONTEXT_FILE_MAX_CHARS)
        if content and content.strip():
            return f"# Global AGENTS.md (~/.agent/{name})\n\n{content.strip()}"
    return ""


def _build_runtime_info(model: str | None) -> str:
    """Build the Runtime: line. Reads HAITUN_CHANNEL / HAITUN_AGENT_ID / HAITUN_MODEL."""
    effective_model = os.environ.get("HAITUN_MODEL") or model or None
    return build_runtime_line(
        agent_id=os.environ.get("HAITUN_AGENT_ID") or None,
        host=platform.node() or None,
        os_str=f"{platform.system()} {platform.release()}".strip() or None,
        arch=platform.machine() or None,
        model=effective_model,
        shell=(os.environ.get("SHELL", "").split("/")[-1] or None),
        channel=os.environ.get("HAITUN_CHANNEL") or None,
    )


_WEEKDAY_ZH = ("一", "二", "三", "四", "五", "六", "日")

_CALENDAR_DAYS = 8


def _weekday_label(day: date) -> str:
    """Render one day as ``2026-08-03 Monday 周一`` — computed, never inferred."""
    return f"{day.strftime('%Y-%m-%d')} {day.strftime('%A')} 周{_WEEKDAY_ZH[day.weekday()]}"


def _build_calendar_lines(today: date) -> list[str]:
    """Lay out today plus the next week as an explicit date→weekday table.

    刻意为之: users say "周一晚上" / "下周二", never "2026-08-03". Resolving that
    to a date is calendar arithmetic, which an LLM does by recall rather than by
    calculation and therefore gets wrong — the bug this exists to kill had the
    agent call 2026-08-03 a Sunday, then a Monday one turn later. A table it can
    read a row out of removes the arithmetic entirely. Yesterday is included
    because "昨天/上周X" references land there.
    """
    lines = [f"- {_weekday_label(today - timedelta(days=1))} (yesterday)"]
    for offset in range(_CALENDAR_DAYS):
        day = today + timedelta(days=offset)
        if offset == 0:
            suffix = " (TODAY)"
        elif offset == 1:
            suffix = " (tomorrow)"
        else:
            suffix = ""
        lines.append(f"- {_weekday_label(day)}{suffix}")
    return lines


def _build_datetime_section() -> str:
    """Build the ## Current Date & Time section.

    Reads the standard TZ env var for the timezone and
    HAITUN_KNOWLEDGE_CUTOFF (optional, e.g. "2026-01") for the knowledge
    cutoff anchor. When the cutoff is unset, emit a neutral line rather
    than fabricating a date.

    The clock and the label are computed from the same source so they
    always agree — otherwise a UTC container would show UTC wall-clock
    time under an "Asia/Shanghai" label (or vice versa), silently feeding
    the agent a time that is off by the UTC offset. When TZ is unset or
    invalid, fall back to the system's local timezone via astimezone(),
    so no tzdata package is strictly required.

    The weekday of *today* and of the surrounding week are printed, not left
    to the model: see ``_build_calendar_lines``. The heading also marks the
    block as belonging to the turn being built, because every past turn keeps
    the block it was sent with (see ``SystemPrompt.turn_context``) — a
    conversation that spans midnight otherwise carries several equally
    plausible ``Date:`` lines with nothing to say which one is now.
    """
    tz_name = os.environ.get("TZ", "").strip()
    try:
        now = datetime.now(ZoneInfo(tz_name)) if tz_name else datetime.now().astimezone()
    except ZoneInfoNotFoundError, ValueError:
        # Unknown tz name: fall back to system local time and label it
        # honestly so the agent knows the zone is unverified.
        now = datetime.now().astimezone()
        tz_name = f"{tz_name} (unresolved — using system local time)"
    if not tz_name:
        tz_name = str(now.tzinfo)
    cutoff = os.environ.get("HAITUN_KNOWLEDGE_CUTOFF", "").strip()
    if cutoff:
        cutoff_line = (
            f"Knowledge cutoff: {cutoff} (facts that may have changed after this "
            "date are not reliable from memory — verify online)."
        )
    else:
        cutoff_line = (
            "Knowledge cutoff: unknown — treat any fact that may have changed "
            "recently as possibly stale and verify online."
        )
    return (
        "## Current Date & Time (THIS TURN — authoritative; ignore any earlier "
        "Date/Time block in this conversation)\n"
        f"Date: {_weekday_label(now.date())}\n"
        f"Time: {now.strftime('%H:%M:%S')}\nTime zone: {tz_name}\n{cutoff_line}\n"
        "\nCalendar (read the weekday off this table — do not compute it):\n"
        + "\n".join(_build_calendar_lines(now.date()))
    )


async def _build_volatile(workspace_dir: anyio.Path) -> str:
    """Build the volatile user-profile section from workspace USER.md."""
    parts: list[str] = []
    user_content = await _read_file_optional(workspace_dir / "USER.md", _USER_MD_MAX_CHARS)
    if user_content and user_content.strip():
        user_body = _strip_frontmatter(user_content).strip()
        if user_body:
            parts.append(f"## User Profile\n\n{user_body}")
    return "\n\n".join(parts)


async def _scan_tool_names(workspace_dir: anyio.Path) -> list[str]:
    """Derive tool names from ``workspace/tools/*.py`` filenames (fallback)."""
    tools_dir = workspace_dir / "tools"
    if not await tools_dir.exists():
        return []
    names: list[str] = []
    async for entry in tools_dir.iterdir():
        if await entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
            names.append(entry.stem)
    return sorted(names)


async def _build_dynamic_context_files(workspace_dir: anyio.Path) -> str:
    """Read dynamic context files (heartbeat.md) for the system prompt."""
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


# ---------------------------------------------------------------------------
# Self-evolution helpers (future extension - not invoked by the framework)
# ---------------------------------------------------------------------------


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
                        "action": {"type": "string", "enum": ["list", "view", "create", "patch"]},
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
                "description": "Create, patch, view, list, or promote reusable workflow assets.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "view", "create", "patch", "promote"]},
                        "flow_name": {"type": "string"},
                        "description": {"type": "string"},
                        "category": {"type": "string"},
                        "body": {"type": "string"},
                        "flow_source": {"type": "string"},
                        "flow_ts": {"type": "string"},
                        "target": {"type": "string", "enum": ["curated", "tasks", "adhoc", "all"]},
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
            logger.debug("Self-evolution LLM call failed at iteration %d: %s", iteration, exc)
            return

        choices = response.get("choices") or []
        if not choices:
            logger.debug("Self-evolution got empty choices at iteration %d", iteration)
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
                result = f"Tool {tool_name!r} is not allowed in self-evolution."
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
                        logger.debug("Self-evolution tool %r failed: %s", tool_name, exc)
                        result = f"Tool {tool_name!r} raised an error: {exc}"

            loop_messages.append({"role": "tool", "tool_call_id": call_id, "name": tool_name, "content": str(result)})


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


class System:
    def __init__(
        self,
        agent_dir: anyio.Path,
        *,
        user_workspace: anyio.Path | None = None,
    ) -> None:
        # Agent package: skills / tools / SOUL / systems (capability root).
        self._agent_dir = agent_dir
        # User open-folder: relative file IO + deliverables (may differ from agent).
        self._user_workspace = user_workspace if user_workspace is not None else agent_dir
        self._previous_summary: str | None = None

    async def _build_workflow_section(self) -> str:
        """Workflow authoring guidance with an explicit legacy fallback.

        Returns empty string if the workflow runtime skill is not present.
        Skill/runtime live under the **agent** package; generated ``flows/`` under
        the **user workspace**.
        """
        agent_resolved = await self._agent_dir.resolve()
        user_resolved = await self._user_workspace.resolve()
        skills_dir = agent_resolved / "skills"
        workflow_dir = skills_dir / "workflow"
        workflow_md = workflow_dir / "SKILL.md"
        if not await workflow_md.exists():
            return ""

        legacy_dir = skills_dir / "fusion-flow-legacy"
        legacy_md = legacy_dir / "SKILL.md"
        flows_dir = user_resolved / "flows"
        # Preserve the legacy runtime handoff for explicit .flow.ts work.
        _ws_parents = Path(str(agent_resolved)).parents
        repo_root = _ws_parents[1] if len(_ws_parents) > 1 else Path(str(agent_resolved))
        default_executor_workspace = repo_root / "examples" / "hermes-style-workspace"
        runtime_bundle = legacy_dir / "runtime" / "agent-flow-core.bundle.mjs"
        session_shim_posix = (Path(str(agent_resolved)) / "bin" / "session_shim.py").as_posix()
        workflow_registry_dir = flows_dir / "workflows"
        flows_index = await _build_flows_index(flows_dir)

        return f"""## Workflow (formal language; explicit legacy fallback)

Workflow is defined by `FusionFlow.g4`. Use `workflow` and
`run_flow` by default for multi-agent or multi-step work.

### Reusable workflow registry

When the user asks in natural language to save, list, load, or reuse a saved
Workflow declaration (for example, `调用 daily-brief 的 workflow`):
1. Read the full skill instructions at:
   {workflow_md}
   Relative path: skills/workflow/SKILL.md
2. Resolve an existing slug under `flows/workflows/<slug>/`: prefer
   `<slug>.workflow`, otherwise use `<slug>.g4`; fail if neither file exists.
3. Read the declaration and inspect `input_workflow(...)` before execution.
4. Resolve every required input from the conversation. If a value is missing,
   ask for it and end the turn without probing `run_flow`.
5. Call `run_flow` exactly once with `flow_path`, complete `inputs_json`, and
   declared `resource_capacities_json`.
6. If the result contains `$fusion_flow/control`, pass its request fields to
   `clarify`; on the next reply continue only through `run_flow_resume` with the
   matching `run_id` and `request_id`.

The reusable registry root is fixed at {workflow_registry_dir}.

### Other reusable flows
{flows_index}

### When to activate
When the user describes a workflow-shaped task - multi-agent collaboration, parallel review,
fan-out/fan-in, pipelines, multi-step research or scoring, or running a `.workflow`
or `.g4` file - activate Workflow.

**Multi-agent simulation is workflow-shaped - build a flow, do NOT role-play it yourself.**
Any task that simulates several distinct agents/personas interacting is a Workflow task:
a debate among N sides (三方辩论), a role-play conversation or roundtable (多角色对话/圆桌),
a negotiation (谈判), red-team vs blue-team (红蓝对抗), a panel of experts / multi-expert
review (多专家会诊/多角度评审), interviewer-vs-candidate, or any "let a few AIs each play a
role and interact" request. When you recognize one, your DEFAULT action is to enter Workflow
Authoring Mode and build a `.workflow` where each role is its own Agent Step.
Use named Artifacts and explicit dependencies for parallel branches and a final synthesizer.
Do NOT play the roles yourself in a single reply.
Only skip the flow if the user explicitly says they want a one-off answer and not a tool.

To activate:
1. Read the full skill instructions at:
   {workflow_md}
   Relative path: skills/workflow/SKILL.md
2. Keep the skill itself immutable. Author generated task files under:
   {flows_dir}/<task-slug>/
   Layout:
   - {flows_dir}/<task-slug>/<task-slug>.workflow
3. Review the source against `skills/workflow/grammar/FusionFlow.g4`.
4. Call `run_flow` once with all declared inputs and resource pools.
5. Report the output Artifact mapping. For `$fusion_flow/control`, use
   `clarify` and next-turn `run_flow_resume` as described above.

The runtime owns parsing, graph validation, plan generation, dependency
scheduling, resource leasing, Agent/Program Step dispatch, and checkpointed
Human waits. Workflows without Human Steps finish in the initial `run_flow`
call; Human workflows continue only through `run_flow_resume`. Instructions may
be inline text or bundle-relative Markdown references resolved by the runner.
Each Step receives only its executor-specific, workspace-confined capability
set. Every materialized Artifact is persisted under the workflow bundle's
`runs/<run-id>/artifacts/` directory.

If the user explicitly supplies `.flow.ts` or asks for Fuclaw/TypeScript compatibility, read
`skills/fusion-flow-legacy/SKILL.md` and use the legacy `flow_run` path instead. Do not translate or
delete the legacy flow silently.

### Self-evolution tools
- `skill_manage`: list, view, create, and patch workspace skills.
- `flow_manage`: list, view, create, patch, and promote both G4 and legacy assets; when both
  exist for one task, it returns the G4 `.workflow`/`.g4` asset first.

Use them only when the task produces reusable knowledge or the user asks to maintain the
workspace. Never silently rewrite user-authored assets.

Rules:
1. Keep both `skills/workflow/` and `skills/fusion-flow-legacy/` immutable - they are runtime
   bundles, not generated skills.
2. Treat skills without `created_by: agent` and without `agent_editable: true` as read-only.
3. Before create: `skill_manage(list)` - if a similar domain skill exists, `patch` it (never create parallel skills).
4. New learned procedures only when nothing similar exists -> `skill_manage(action="create")`.
5. Reusable Workflow declarations ->
   `flows/workflows/<slug>/<slug>.workflow` or
   `flows/workflows/<slug>/<slug>.g4` via workspace file tools
   (`.workflow` takes precedence when both exist).
   `flows/curated/<flow-name>/FLOW.md` via `flow_manage` remains a compatibility catalog.
6. One-off task executions -> `flows/<task-slug>/`.
Follow `skills/skill-authoring-when` then `skill-authoring-how`
(prefer update over create; do this before self-evolution invents a new skill).

The following existing runtime and credential handoff remains available unchanged for the
explicit legacy `.flow.ts` fallback:

To activate:
1. Read the full skill instructions at:
   {legacy_md}
   Relative path: skills/fusion-flow-legacy/SKILL.md
2. Keep the skill itself immutable. Author generated task files under:
   {flows_dir}/<task-slug>/
   Layout:
   - {flows_dir}/<task-slug>/<task-slug>.flow.ts
   - {flows_dir}/<task-slug>/runs/<run-id>/
3. Use the Fusion Flow Legacy runtime from:
   {runtime_bundle}
   Generated flows import it with:
   ../../skills/fusion-flow-legacy/runtime/agent-flow-core.bundle.mjs
4. Typecheck from the Fusion Flow Legacy skill directory (its tsconfig includes ../../flows/**/*.ts):
   cd "{legacy_dir}" && npm run typecheck
5. Run generated flows from the Fusion Flow Legacy skill directory:
   cd "{legacy_dir}" && npx tsx ../../flows/<task-slug>/<task-slug>.flow.ts

When generating the run(...) options, always include both:
- programPath normalized from import.meta.url
- runsDir set to the generated flow's sibling ./runs directory

### Engine defaults
Fusion Flow Legacy may call external agent CLI engines. Prefer the psi engine; do not call this same
workspace recursively as the execution workspace. Default execution workspace unless the user
provides another one:

FLOW_ENGINE=psi
FLOW_PSI_WORKSPACE={default_executor_workspace}
FLOW_PSI_PROFILE=fusion

CRITICAL — the psi engine MUST route through the session shim, never call `psi-agent run`
directly. The current psi-agent CLI's `run` is a YAML batch launcher taking a single positional
config path; the Fusion Flow Legacy bundle emits the OLD form `psi-agent run --workspace --message ...`,
which that CLI rejects with `exit=2 Missing value for argument 'config'`. If you see that exit=2
(or think "the psi engine is incompatible with the current CLI"), the cause is missing shim
wiring — it is NOT a reason to abandon Fusion Flow Legacy and hand-roll a parallel run with background
tasks. Fix the wiring and re-run. The shim (`bin/session_shim.py`) translates the old call into
the new three-layer architecture (`ai --provider` + `session` + `channel cli`).

Wire it (forward-slash paths work on Windows + POSIX; use `python3` if `python` is absent):

FLOW_PSI_COMMAND=python
FLOW_PSI_COMMAND_ARGS={session_shim_posix}
PSI_CMD=uv run --no-sync --project {repo_root} psi-agent

The shim also needs FLOW_PSI_AI / FLOW_PSI_MODEL / FLOW_PSI_BASE_URL / FLOW_PSI_API_KEY. If a
gateway has been used, reuse its saved config with the fusion-flow-workspace helper
`bin/env_from_gateway.py` (reads state/latest.json, writes the .env) instead of
hand-copying the key.

Never write API keys into this workspace, generated `.flow.ts` files, or committed `.env` files.

Workflow's executor reuses the invoking psi-agent Session's configured AI socket. Never write API keys into
this workspace, generated workflows, instruction files, or committed `.env` files."""

    async def build_system_prompt(
        self,
        model: str | None = None,
        tool_names: list[str] | None = None,
        user_text: str = "",
    ) -> str:
        # Capability root (skills/tools/SOUL) vs user open-folder (file IO guidance).
        ws = self._agent_dir
        user_ws = self._user_workspace
        tools = tool_names or await _scan_tool_names(ws)

        # -- Stable prefix ------------------------------------------------
        identity = await _load_soul_md(ws)
        skills_xml = await _build_skills_index(ws)
        workflow_section = await self._build_workflow_section()
        context_file = await _build_context_file(ws)
        bootstrap = await _build_bootstrap_files(ws)
        global_agents_md = await _build_global_agents_md()

        stable_parts: list[str] = [identity, "", LANGUAGE_LOCALIZATION_SECTION]

        help_skill_md = ws / "skills" / HELP_SKILL_NAME / "SKILL.md"
        if await help_skill_md.exists():
            stable_parts += ["", PSI_AGENT_HELP_GUIDANCE.format(path=str(help_skill_md))]

        stable_parts += [
            "",
            build_tooling_section(tools),
            "",
            TOOL_CALL_STYLE_SECTION,
            "",
            SYSTEM_CLI_TOOLS_SECTION,
            "",
            SEND_FILES_SECTION,
            "",
            DELIVERABLES_AS_FILES_SECTION,
            "",
            EXECUTION_BIAS_SECTION,
            "",
            PLANNING_PROGRESS_SECTION,
            "",
            ERROR_HANDLING_RETRY_SECTION,
            "",
            CODE_CONVENTIONS_SECTION,
            "",
            WEB_SEARCH_RECENCY_SECTION,
            "",
            CLARIFY_ASSUMPTIONS_SECTION,
            "",
            CLOSING_QUESTIONS_SECTION,
            "",
            STRUCTURED_TABLES_SECTION,
            "",
            TASK_SELF_CHECK_SECTION,
            "",
            CITATIONS_TRUSTWORTHINESS_SECTION,
            "",
            SUBAGENT_DELEGATION_SECTION,
            "",
            SAFETY_SECTION,
        ]

        # 刻意为之: Fusion Memory 那一整段只在记忆服务真的配了的时候才注入。
        # 它原先无条件进稳定前缀, 于是每个会话、每次构建提示都要带上约 30 行 ——
        # 包括「不要改 .env / 去读 fusion-memory-setup 恢复指南」这类只对运维有用的话,
        # 而记忆服务没配时模型压根用不上这些工具。
        # 判据用 FUSION_MEMORY_MCP_URL 而不是工具名: _scan_tool_names 是按
        # tools/*.py 的文件名扫的, memory_*.py 永远在磁盘上, 所以
        # "memory_add" in tools 恒为真, 拿它做开关等于没开关。
        if os.environ.get("FUSION_MEMORY_MCP_URL", "").strip():
            stable_parts += ["", FUSION_MEMORY_SECTION]

        _session_tools = {
            "sessions_list",
            "sessions_history",
            "session_status",
            "session_keyword_search",
            "session_task_search",
            "sessions_export",
            "sessions_create",
            "sessions_handoff",
        }
        if _session_tools & set(tools):
            stable_parts += ["", SESSION_MANAGEMENT_SECTION]

        if "todo" in tools:
            stable_parts += ["", TASK_PLANNING_SECTION]

        if "skill_manage" in tools:
            stable_parts += ["", SKILL_AUTHORING_SECTION]

        skills_section = build_skills_section(skills_xml)
        if skills_section:
            stable_parts += ["", skills_section]

        if workflow_section:
            stable_parts += ["", workflow_section]

        workspace_abs = str(await user_ws.resolve())
        stable_parts += ["", build_workspace_section(workspace_abs)]

        if global_agents_md:
            stable_parts += ["", global_agents_md]

        if bootstrap:
            stable_parts += ["", bootstrap]

        if context_file:
            stable_parts += ["", context_file]

        if await (ws / "BOOTSTRAP.md").exists():
            stable_parts += ["", BOOTSTRAP_PENDING_SECTION]

        stable_parts += ["", SILENT_REPLIES_SECTION]
        stable_parts += ["", _SAVING_GATE_SECTION]

        model_identity = build_model_identity_line(model)
        if model_identity:
            stable_parts += ["", model_identity]

        # NOTE: the heartbeat instruction is intentionally NOT injected here.
        # The heartbeat schedule (schedules/heartbeat/TASK.md) already tells the
        # agent to reply HEARTBEAT_OK on its poll; injecting it into every turn's
        # system prompt caused HEARTBEAT_OK to leak into normal chat replies.
        volatile = await _build_volatile(ws)
        if volatile:
            stable_parts += ["", volatile]

        dynamic_ctx = await _build_dynamic_context_files(ws)
        if dynamic_ctx:
            stable_parts += ["", dynamic_ctx]

        return "\n".join(stable_parts)

    async def build_turn_context(self, model: str | None = None) -> str:
        """Assemble the volatile block for the turn about to run.

        Everything here is re-rendered per turn and delivered at the tail of the
        request (see ``turn_context_builder``), not inside the system prompt —
        so the prompt and every earlier turn stay byte-identical. Keep it small
        and keep it to things that genuinely change per turn: the clock, and the
        runtime identity that can shift when the Session is re-attached.
        """
        parts = [_build_datetime_section(), "", _build_runtime_info(model)]
        return "\n".join(parts)

    async def compact_history(
        self,
        history: list[dict[str, Any]],
        complete_fn: CompleteFn,
        max_tokens: int = 4000,
        keep_recent_tokens: int | None = None,
    ) -> list[dict[str, Any]]:
        """Summarization-based history compaction (future extension; not yet wired)."""
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
                return history[-len(recent_messages) :] if recent_messages else history[-20:]

        if is_split_turn and turn_prefix_messages:
            prompt = _build_summarization_prompt(turn_prefix_messages)
            try:
                prefix_summary = await complete_fn(
                    [
                        {"role": "system", "content": _SUMMARIZATION_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt + "\n\n" + _TURN_PREFIX_SUMMARY_PROMPT},
                    ]
                )
                summary_parts.append(f"\n\n---\n\n**Turn Context (split turn):**\n\n{prefix_summary}")
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

    async def after_turn(
        self,
        messages: list[dict[str, Any]],
        tool_call_count: int,
        called_tools: list[str],
        *,
        complete_fn: ReviewCompleteFn,
        tool_executors: ToolExecutors,
    ) -> None:
        """Post-turn self-evolution review (future extension; not yet wired)."""
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
            logger.debug("Self-evolution skipped: no review tools registered")
            return

        await _run_self_evolution_review(
            messages=messages,
            complete_fn=complete_fn,
            tool_executors=review_tools,
        )


def _resolve_workspace(workspace_raw: str = "", agent_raw: str = "") -> anyio.Path:
    """User workspace root; falls back to the agent package (single-root compat)."""
    raw = workspace_raw or _runtime_workspace() or str(_resolve_agent(agent_raw))
    return anyio.Path(os.path.realpath(os.path.abspath(raw)))


def _get_supervisor_manager(workspace: anyio.Path) -> Any:
    key = os.path.realpath(os.path.abspath(str(workspace)))
    manager = _SUPERVISOR_MANAGERS.get(key)
    if manager is None:
        supervisor_module = importlib.import_module("supervisor")
        manager = supervisor_module.SupervisorManager(anyio.Path(key))
        _SUPERVISOR_MANAGERS[key] = manager
    return manager


def _build_profile_policy(topic_profile: dict[str, Any]) -> str:
    current_turn = int(topic_profile.get("turns", 0)) + 1
    socratic = "3. **苏格拉底提问**: 本轮必须提问!" if current_turn % 3 == 0 else "3. 本轮不强制提问。"
    return (
        "## 强制监督规则\n\n"
        "0. **任务执行优先**: 若当前请求是 Workflow 编排或执行, 跳过以下教学规则, "
        "以流程构建、运行结果和用户交付要求为准。\n"
        "1. **确定性标记**: 事实性陈述使用 `[已确认]`、`[推断]` 或 `[需验证]`。\n"
        "2. **反例注入**: 每个核心概念给出一个反例或边界场景。\n"
        f"{socratic}\n4. **破圈引导**: 是否破圈由旁路监督按当前问题决定, 不绑定固定轮次。\n"
        "5. **画像匹配**: 按教学指令控制深度、术语和决策信息。\n"
    )


async def system_before_turn(
    user_message: dict[str, Any] | None,
    *,
    workspace_raw: str = "",
    agent_raw: str = "",
) -> dict[str, Any]:
    """Return namespaced background advice for an eligible learning turn."""
    if not isinstance(user_message, dict):
        return {}
    content = user_message.get("content")
    if not isinstance(content, str) or not content.strip():
        return {}
    if not any(
        isinstance(user_message.get(name), str) and bool(user_message[name].strip())
        for name in ("user_id", "profile_id", "session_id")
    ):
        return {}
    supervisor_module = importlib.import_module("supervisor")
    if not supervisor_module.is_learning_question(content):
        return {}
    try:
        manager = _get_supervisor_manager(_resolve_workspace(workspace_raw, agent_raw))
        before_turn = getattr(manager, "before_turn", None)
        advice = await before_turn(user_message) if callable(before_turn) else await manager.supervise(user_message)
    except Exception as exc:
        logger.warning("Background supervisor unavailable: %r", exc, exc_info=True)
        return {}
    return {"supervisor_advice": advice} if isinstance(advice, dict) else {}


_SAVING_GATE_SECTION = """\
## Saving-decision core rules
For purchase / money-saving requests (national subsidy, coupons, price comparison, bank instant discounts,
recommendations, cart-filling), these core rules ALWAYS apply:
1. Policy parameters (rate / cap / threshold / energy-efficiency / categories) come ONLY from this session's
   retrieval or a fact-card snapshot (with verification date) - never from memory, never by reverse-engineering
   from product price.
2. Computing money (subsidy / final price): call subsidy_calc when available; if unavailable or failed, mark
   [Unverified] - do NOT hand-compute.
3. Recommendation / guide tasks: first call review_search for candidate articles; only if it returns empty or
   fails may you search on your own.
4. Also load and follow `skills/saving-decision/SKILL.md` (read it with the read tool) for the full rule set.
Ignore this section for non-saving tasks.
"""


async def system_prompt_builder(
    user_message: dict[str, Any] | None = None,
    *,
    workspace_raw: str = "",
    agent_raw: str = "",
) -> str:
    """Module-level entry point used by the psi-agent session loader.

    The loader looks up an async ``system_prompt_builder`` attribute in this
    module and calls it with the turn's user message.

    **Agent vs user workspace (三区)**: the capability root comes from
    *agent_raw* (the kernel passes the package root it loaded this module from)
    or ``get_agent()``, and only falls back to ``__file__`` when neither is set
    — see ``_resolve_agent``. The user open-folder for file IO comes from
    Session ``get_workspace()`` (bound inside ``runtime_scope`` during the
    turn). Falling back to the agent root preserves single-root compat.
    """
    agent_dir = _resolve_agent(agent_raw)
    user_workspace = agent_dir
    raw = (workspace_raw or _runtime_workspace() or "").strip()
    if raw:
        user_workspace = anyio.Path(raw)
    await _activate_fusion_memory(agent_dir)
    content = user_message.get("content") if isinstance(user_message, dict) else ""
    user_text = content if isinstance(content, str) else ""
    prompt = await System(agent_dir, user_workspace=user_workspace).build_system_prompt()
    profile_text = ""
    policy_text = ""
    try:
        profile_module = importlib.import_module("_user_profile")
        identity = {
            name: value
            for name in ("profile_id", "user_id", "session_id")
            if isinstance(user_message, dict) and isinstance((value := user_message.get(name)), str) and value
        }
        profile = await profile_module.get_profile(str(user_workspace), **identity)
        topic_profile = None
        if user_text.strip():
            _topic_key, topic_profile = profile.get_topic(user_text)
        if topic_profile:
            dimensions = profile.effective_dimensions(topic_profile)
            profile_text = (
                "## 当前知识点学习画像 (每轮从持久化画像重新读取)\n"
                f"- 当前知识点: {topic_profile['label']}\n"
                f"- 累计轮次: {topic_profile['turns']}\n"
                f"- 深度: {dimensions['depth']:.2f} (0=框架概览, 1=系统推导)\n"
                f"- 目标: {dimensions['goal']:.2f} (0=兴趣, 1=决策)\n"
                f"- 熟悉度: {dimensions['familiarity']:.2f} (0=新手, 1=专家)\n"
                f"- 教学指令: {profile.teaching_hint(topic_profile)}\n"
            )
            policy_text = _build_profile_policy(topic_profile)
    except Exception as exc:
        logger.warning("Adaptive profile unavailable: %r", exc, exc_info=True)

    raw_advice = user_message.get("supervisor_advice") if isinstance(user_message, dict) else None
    if isinstance(raw_advice, dict):
        protocol = importlib.import_module("supervisor_protocol")
        advice_text = protocol.render_advice_prompt(protocol.validate_advice(raw_advice))
        if advice_text:
            advice_text += "\n- 用户当前消息中的直接范围和深度要求优先; 若用户要求不展开, 则抑制破圈, 不得强制扩展。"
    else:
        advice_text = ""
    injected = "\n".join(part for part in (profile_text, advice_text, policy_text) if part)
    if not injected:
        return prompt
    boundary = "<!-- HAITUN_CACHE_BOUNDARY -->"
    if boundary in prompt:
        index = prompt.find(boundary) + len(boundary)
        return prompt[:index] + "\n" + injected + "\n" + prompt[index:]
    return prompt + "\n" + injected


async def turn_context_builder(*, agent_raw: str = "") -> str:
    """Render the volatile block for the turn about to run.

    ``system_prompt_builder`` runs once per Session, so every "now" it renders
    freezes: a Session opened last Friday goes on reporting Friday's date, and
    a ``Time zone`` label that was wrong at build time (a container whose TZ
    had not taken effect yet reported ``UTC`` for Asia/Shanghai) stays wrong
    for the whole life of that history. The agent then answers from that stale
    line and, asked to reconcile it with reality, invents timezone arithmetic
    to explain the gap.

    The fix is not to re-render the prompt — that is the *front* of the
    request, and rewriting it per turn invalidates the cache for the whole
    conversation behind it. This block is delivered at the **tail** instead,
    on the turn's own user message, so the prompt and every earlier turn stay
    byte-identical.
    """
    agent_dir = _resolve_agent(agent_raw)
    user_workspace = agent_dir
    raw = (_runtime_workspace() or "").strip()
    if raw:
        user_workspace = anyio.Path(raw)
    system = System(agent_dir, user_workspace=user_workspace)
    return await system.build_turn_context()


async def system_prompt_rebuild_checker(
    _user_message: dict[str, Any] | None = None,
    *,
    agent_raw: str = "",
) -> bool:
    """Activate Memory and rebuild for the current topic-specific profile."""
    agent_dir = _resolve_agent(agent_raw)
    await _activate_fusion_memory(agent_dir)
    return True


async def system_after_turn(
    user_message: dict[str, Any],
    assistant_message: dict[str, Any],
    *,
    workspace_raw: str = "",
    agent_raw: str = "",
) -> None:
    """Persist profile signals and warm the background supervisor."""
    profile_module = importlib.import_module("_user_profile")
    workspace = _resolve_workspace(workspace_raw, agent_raw)
    identity = {
        name: value
        for name in ("profile_id", "user_id", "session_id")
        if isinstance((value := user_message.get(name)), str) and value
    }
    profile = await profile_module.get_profile(str(workspace), **identity)
    user_text = user_message.get("content")
    assistant_text = assistant_message.get("content")
    user_text = user_text if isinstance(user_text, str) else ""
    assistant_text = assistant_text if isinstance(assistant_text, str) else ""
    await profile.record_turn(user_text, assistant_text)

    supervisor_module = importlib.import_module("supervisor")
    if supervisor_module.is_learning_question(user_text):
        try:
            await _get_supervisor_manager(workspace).prime(user_message)
        except Exception as exc:
            logger.warning("Background supervisor warmup failed: %r", exc, exc_info=True)


async def _activate_fusion_memory(workspace_dir: anyio.Path) -> None:
    mcp_path = Path(str(workspace_dir)) / "tools" / "_fusion_memory_mcp.py"
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
            await activate(workspace_dir)
    except Exception as exc:
        if created:
            sys.modules.pop(module_name, None)
        logger.warning("Fusion Memory activation skipped after %s", type(exc).__name__)


if __name__ == "__main__":
    # Smoke test: print the assembled system prompt.
    print(anyio.run(system_prompt_builder))
