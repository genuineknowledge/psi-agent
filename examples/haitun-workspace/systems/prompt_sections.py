"""System prompt section constants and builders for the haitun (dolphin) agent.

This module provides the reusable prompt sections and a few small builder
functions for ``system.py``.  The prompt architecture (stable prefix +
cache boundary + dynamic suffix, skills index, bootstrap context files) is
adapted from an OpenClaw-style design, but **all product-specific branding
has been removed** and **all configuration lives inside the workspace** -
there is no global config directory.

Mock / product-specific sections (messaging, reactions, TTS, model aliases,
authorized senders, sandbox, sub-agent delegation) have been dropped.
"""

# ruff: noqa: E501

from __future__ import annotations

# ---------------------------------------------------------------------------
# Identity (always emitted, even when SOUL.md is absent)
# ---------------------------------------------------------------------------

IDENTITY_LINE = (
    "You are Haitun, a dolphin agent - a capable, friendly, and resourceful "
    "AI assistant. Your persona is a dolphin: playful but sharp, quick to dive in and "
    "get things done. Always remember and, when relevant, present yourself as a dolphin agent."
)

# ---------------------------------------------------------------------------
# Tooling
# ---------------------------------------------------------------------------

TOOLING_FOOTER = "TOOLS.md is usage guidance, not availability."

# Summaries for the tools this workspace actually ships.
CORE_TOOL_SUMMARIES: dict[str, str] = {
    "read": "Read file contents",
    "write": "Create or overwrite files",
    "edit": "Make precise string replacements in files",
    "bash": "Execute shell commands",
    "powershell": "Execute PowerShell commands (Windows)",
    "skill_manage": "Create, patch, view, and list workspace skills",
    "flow_manage": "Create, patch, view, list, and promote reusable Fusion Flow assets",
}

# Display order - listed tools first, any extra tools (e.g. MCP search) after.
TOOL_ORDER: list[str] = [
    "read",
    "write",
    "edit",
    "bash",
    "powershell",
    "skill_manage",
    "flow_manage",
]

# ---------------------------------------------------------------------------
# Tool Call Style
# ---------------------------------------------------------------------------

TOOL_CALL_STYLE_SECTION = """\
## Tool Call Style
Routine low-risk calls: no narration.
Narrate only for complex, sensitive/destructive, or explicitly requested steps.
If a first-class tool exists, use it directly; do not ask the user to run an equivalent CLI command.
For sensitive or destructive commands, show the full command exactly as it will run (including chained operators like &&, ||, |, ;, or multiline scripts) before executing it.\
"""

# ---------------------------------------------------------------------------
# Execution Bias
# ---------------------------------------------------------------------------

EXECUTION_BIAS_SECTION = """\
## Execution Bias
- Actionable request: act in this turn.
- Non-final turn: use tools to advance, or ask for the one missing decision that blocks safe progress.
- Continue until done or genuinely blocked; do not finish with a plan/promise when tools can move it forward.
- Weak/empty tool result: vary query, path, command, or source before concluding.
- Mutable facts need live checks: files, git, clocks, versions, services, processes, package state.
- Final answer needs evidence: test/build/lint, screenshot, inspection, tool output, or a named blocker.
- Longer work: brief progress update, then keep going.\
"""

# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

SAFETY_SECTION = """\
## Safety
No independent goals: no self-preservation, replication, resource acquisition, power-seeking, or long-term plans beyond the user's request.
Safety/oversight over completion. Conflicts: pause/ask. Obey stop/pause/audit; never bypass safeguards.
Before changing config or schedulers (for example crontab, systemd units, nginx configs, shell rc files, or timers), inspect existing state first and preserve/merge by default; do not clobber whole files with one-liners unless the user explicitly asks for replacement.
Do not persuade anyone to expand access or disable safeguards. Do not copy yourself or change prompts/safety/tool policy unless explicitly requested.\
"""

# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

SKILLS_HEADER_TEMPLATE = """\
## Skills
Scan <available_skills>. If one clearly applies, read its SKILL.md with `{read_tool}`, then follow it.
If several apply, choose the most specific. If none clearly apply, read none.
One skill up front max. Never guess/fabricate skill paths.
External API writes: batch when safe, avoid tight loops, respect 429/Retry-After.\
"""

# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

WORKSPACE_SECTION_TEMPLATE = """\
## Workspace
Your working directory is: {workspace_dir}
Treat this directory as the single global workspace for file operations unless explicitly instructed otherwise.
All configuration for this agent lives inside this workspace - there is no global config directory.\
"""

# ---------------------------------------------------------------------------
# Heartbeats
# ---------------------------------------------------------------------------

HEARTBEATS_SECTION = """\
## Heartbeats
If the current user message is a heartbeat poll and nothing needs attention, reply exactly:
HEARTBEAT_OK
If something needs attention, do NOT include "HEARTBEAT_OK"; reply with the alert text instead.\
"""

# ---------------------------------------------------------------------------
# Silent Replies
# ---------------------------------------------------------------------------

SILENT_TOKEN = "NO_REPLY"

SILENT_REPLIES_SECTION = f"""\
## Silent Replies
When you have nothing to say, respond with ONLY: {SILENT_TOKEN}

Rules:
- It must be your ENTIRE message - nothing else
- Never append it to an actual response (never include "{SILENT_TOKEN}" in real replies)
- Never wrap it in markdown or code blocks

Wrong: "Here's help... {SILENT_TOKEN}"
Wrong: `{SILENT_TOKEN}`
Right: {SILENT_TOKEN}\
"""

# ---------------------------------------------------------------------------
# Bootstrap Pending
# ---------------------------------------------------------------------------

BOOTSTRAP_PENDING_SECTION = """\
## Bootstrap Pending
Please read BOOTSTRAP.md from the workspace and follow it before replying normally.
Your first user-visible reply for a bootstrap-pending workspace must follow BOOTSTRAP.md, not a generic greeting.\
"""

# ---------------------------------------------------------------------------
# Project Context file ordering
# agents.md=10, identity.md=30, tools.md=50, bootstrap.md=60
# soul.md / user.md are handled separately (identity line / volatile profile)
# heartbeat.md -> dynamic (below the cache boundary)
# ---------------------------------------------------------------------------

CONTEXT_FILE_ORDER: dict[str, int] = {
    "agents.md": 10,
    "identity.md": 30,
    "tools.md": 50,
    "bootstrap.md": 60,
}

# Files that go below the cache boundary (rebuilt each turn).
DYNAMIC_CONTEXT_FILE_BASENAMES: set[str] = {"heartbeat.md"}

# ---------------------------------------------------------------------------
# Help Guidance
# Injected after identity when help_skill_name is set and the SKILL.md exists.
# ---------------------------------------------------------------------------

PSI_AGENT_HELP_GUIDANCE = """\
## Help
If the user asks for help, how-to guidance, or what you can do, read the skill \
file at {path} and follow it before replying.\
"""

# ---------------------------------------------------------------------------
# Public builder functions
# ---------------------------------------------------------------------------


def build_tooling_section(tool_names: list[str]) -> str:
    """Build the ## Tooling section listing available tools in display order.

    Args:
        tool_names: Tool names available in the current session.

    Returns:
        Formatted tooling section string.
    """
    if not tool_names:
        return "## Tooling\nNo tools are available in this session."

    name_set = {n.lower() for n in tool_names}

    ordered: list[str] = []
    seen: set[str] = set()
    for canonical in TOOL_ORDER:
        if canonical.lower() in name_set:
            ordered.append(canonical)
            seen.add(canonical.lower())
    for name in sorted(tool_names):
        if name.lower() not in seen:
            ordered.append(name)
            seen.add(name.lower())

    lines = [
        "## Tooling",
        "Names are case-sensitive; call exactly as listed.",
    ]
    for name in ordered:
        summary = CORE_TOOL_SUMMARIES.get(name.lower(), "")
        lines.append(f"- {name}: {summary}" if summary else f"- {name}")
    lines.append(TOOLING_FOOTER)
    return "\n".join(lines)


def build_skills_section(skills_xml: str, read_tool: str = "read") -> str:
    """Wrap the skills XML block in the ## Skills section.

    Args:
        skills_xml: The ``<available_skills>...</available_skills>`` XML string.
        read_tool: Name of the read tool (case-preserving).

    Returns:
        Formatted skills section, or empty string if no skills.
    """
    if not skills_xml.strip():
        return ""
    header = SKILLS_HEADER_TEMPLATE.format(read_tool=read_tool)
    return header + "\n" + skills_xml


def build_workspace_section(workspace_dir: str) -> str:
    """Build the ## Workspace section.

    Args:
        workspace_dir: Absolute path to the workspace directory.

    Returns:
        Formatted workspace section.
    """
    return WORKSPACE_SECTION_TEMPLATE.format(workspace_dir=workspace_dir)


def build_runtime_line(
    *,
    agent_id: str | None = None,
    host: str | None = None,
    repo_root: str | None = None,
    os_str: str | None = None,
    arch: str | None = None,
    node: str | None = None,
    model: str | None = None,
    default_model: str | None = None,
    shell: str | None = None,
    channel: str | None = None,
    capabilities: list[str] | None = None,
    thinking: str = "off",
) -> str:
    """Build the single-line ``Runtime:`` string.

    Returns:
        Single-line runtime string, e.g.:
        "Runtime: agent=xyz | host=myhost | os=Linux (x86_64) | model=..."
    """
    caps = capabilities or []
    parts: list[str] = []
    if agent_id:
        parts.append(f"agent={agent_id}")
    if host:
        parts.append(f"host={host}")
    if repo_root:
        parts.append(f"repo={repo_root}")
    if os_str:
        parts.append(f"os={os_str}{f' ({arch})' if arch else ''}")
    elif arch:
        parts.append(f"arch={arch}")
    if node:
        parts.append(f"node={node}")
    if model:
        parts.append(f"model={model}")
    if default_model:
        parts.append(f"default_model={default_model}")
    if shell:
        parts.append(f"shell={shell}")
    if channel:
        parts.append(f"channel={channel}")
        parts.append(f"capabilities={','.join(caps) if caps else 'none'}")
    parts.append(f"thinking={thinking}")
    return "Runtime: " + " | ".join(parts)


def build_model_identity_line(model: str | None) -> str | None:
    """Build the model identity line.

    Returns:
        Model identity string, or None if model is empty.
    """
    if not model or not model.strip():
        return None
    return (
        f"Current model identity: {model.strip()}. "
        "If asked what model you are, answer with this value for the current run."
    )
