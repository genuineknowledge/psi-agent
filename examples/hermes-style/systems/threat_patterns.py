"""Prompt injection / promptware threat-pattern scanner.

Ported from Hermes Agent's tools/threat_patterns.py. Single source of truth
for context-window security scanning used by the context file loaders in
system.py.

Pattern scopes
--------------
- ``"all"``     — classic prompt injection + exfiltration; applied everywhere.
- ``"context"`` — adds promptware / C2 / role-play hijack; used for context
  files (AGENTS.md, .hermes.md, etc.).
- ``"strict"``  — adds persistence / SSH backdoor patterns; used for
  user-mediated writes (memory, skill installs).

Patterns anchor on C2-specific vocabulary or unambiguous attack behavior,
NOT on generic bossy English — "you must" alone is too common in legitimate
instruction files to flag.
"""

from __future__ import annotations

import re

# Each entry: (regex, pattern_id, scope)
# scope ∈ {"all", "context", "strict"}
_PATTERNS: list[tuple[str, str, str]] = [
    # ── Classic prompt injection ─────────────────────────────────────────
    (
        r"ignore\s+(?:\w+\s+)*(previous|all|above|prior)\s+(?:\w+\s+)*instructions",
        "prompt_injection",
        "all",
    ),
    (r"system\s+prompt\s+override", "sys_prompt_override", "all"),
    (
        r"disregard\s+(?:\w+\s+)*(your|all|any)\s+(?:\w+\s+)*(instructions|rules|guidelines)",
        "disregard_rules",
        "all",
    ),
    (
        r"act\s+as\s+(if|though)\s+(?:\w+\s+)*you\s+(?:\w+\s+)*(have\s+no|don\'t\s+have)\s+(?:\w+\s+)*(restrictions|limits|rules)",
        "bypass_restrictions",
        "all",
    ),
    (r"<!--[^>]*(?:ignore|override|system|secret|hidden)[^>]*-->", "html_comment_injection", "all"),
    (r"<\s*div\s+style\s*=\s*[\"'][\s\S]*?display\s*:\s*none", "hidden_div", "all"),
    (r"translate\s+.*\s+into\s+.*\s+and\s+(execute|run|eval)", "translate_execute", "all"),
    (r"do\s+not\s+(?:\w+\s+)*tell\s+(?:\w+\s+)*the\s+user", "deception_hide", "all"),
    # ── Role-play / identity hijack ──────────────────────────────────────
    (r"you\s+are\s+(?:\w+\s+)*now\s+(?:a|an|the)\s+", "role_hijack", "context"),
    (r"pretend\s+(?:\w+\s+)*(you\s+are|to\s+be)\s+", "role_pretend", "context"),
    (r"output\s+(?:\w+\s+)*(system|initial)\s+prompt", "leak_system_prompt", "context"),
    (
        r"(respond|answer|reply)\s+without\s+(?:\w+\s+)*(restrictions|limitations|filters|safety)",
        "remove_filters",
        "context",
    ),
    (r"you\s+have\s+been\s+(?:\w+\s+)*(updated|upgraded|patched)\s+to", "fake_update", "context"),
    (r"\bname\s+yourself\s+\w+", "identity_override", "context"),
    # ── C2 / promptware ──────────────────────────────────────────────────
    (r"register\s+(as\s+)?a?\s*node", "c2_node_registration", "context"),
    (r"(heartbeat|beacon|check[\s\-]?in)\s+(to|with)\s+", "c2_heartbeat", "context"),
    (r"pull\s+(down\s+)?(?:new\s+)?task(?:ing|s)?\b", "c2_task_pull", "context"),
    (r"connect\s+to\s+the\s+network\b", "c2_network_connect", "context"),
    (r"you\s+must\s+(?:\w+\s+){0,3}(register|connect|report|beacon)\b", "forced_action", "context"),
    (r"only\s+use\s+one[\s\-]?liners?\b", "anti_forensic_oneliner", "context"),
    (
        r"never\s+(?:\w+\s+)*(?:create|write)\s+(?:\w+\s+)*(?:script|file)\s+(?:\w+\s+)*disk",
        "anti_forensic_disk",
        "context",
    ),
    (
        r"unset\s+\w*(?:CLAUDE|CODEX|HERMES|AGENT|OPENAI|ANTHROPIC)\w*",
        "env_var_unset_agent",
        "context",
    ),
    # ── Known C2 framework names ─────────────────────────────────────────
    (
        r"\b(?:praxis|cobalt\s*strike|sliver|havoc|mythic|metasploit|brainworm)\b",
        "known_c2_framework",
        "context",
    ),
    (r"\bc2\s+(?:server|channel|infrastructure|beacon)\b", "c2_explicit", "context"),
    (r"\bcommand\s+and\s+control\b", "c2_explicit_long", "context"),
    # ── Exfiltration via curl/wget/cat ───────────────────────────────────
    (r"curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfil_curl", "all"),
    (r"wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfil_wget", "all"),
    (r"cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)", "read_secrets", "all"),
    (r"(send|post|upload|transmit)\s+.*\s+(to|at)\s+https?://", "send_to_url", "strict"),
    (
        r"(include|output|print|share)\s+(?:\w+\s+)*(conversation|chat\s+history|previous\s+messages|full\s+context|entire\s+context)",
        "context_exfil",
        "strict",
    ),
    # ── Persistence / SSH backdoor ───────────────────────────────────────
    (r"authorized_keys", "ssh_backdoor", "strict"),
    (r"\$HOME/\.ssh|\~/\.ssh", "ssh_access", "strict"),
    (r"\$HOME/\.hermes/\.env|\~/\.hermes/\.env", "hermes_env", "strict"),
    (
        r"(update|modify|edit|write|change|append|add\s+to)\s+.*(?:AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules)",
        "agent_config_mod",
        "strict",
    ),
    (
        r"(update|modify|edit|write|change|append|add\s+to)\s+.*\.hermes/(config\.yaml|SOUL\.md)",
        "hermes_config_mod",
        "strict",
    ),
    # ── Hardcoded secrets ────────────────────────────────────────────────
    (
        r"(?:api[_-]?key|token|secret|password)\s*[=:]\s*[\"'][A-Za-z0-9+/=_-]{20,}",
        "hardcoded_secret",
        "strict",
    ),
]

# Invisible / bidirectional unicode characters used in injection attacks.
INVISIBLE_CHARS: frozenset[str] = frozenset(
    {
        "\u200b",  # zero-width space
        "\u200c",  # zero-width non-joiner
        "\u200d",  # zero-width joiner
        "\u2060",  # word joiner
        "\u2062",  # invisible times
        "\u2063",  # invisible separator
        "\u2064",  # invisible plus
        "\ufeff",  # zero-width no-break space (BOM)
        "\u202a",  # left-to-right embedding
        "\u202b",  # right-to-left embedding
        "\u202c",  # pop directional formatting
        "\u202d",  # left-to-right override
        "\u202e",  # right-to-left override
        "\u2066",  # left-to-right isolate
        "\u2067",  # right-to-left isolate
        "\u2068",  # first strong isolate
        "\u2069",  # pop directional isolate
    }
)

# Compiled pattern sets indexed by scope, built once at import time.
_COMPILED: dict[str, list[tuple[re.Pattern[str], str]]] = {}


def _compile() -> None:
    """Compile pattern sets for each scope."""
    global _COMPILED
    if _COMPILED:
        return

    all_patterns: list[tuple[re.Pattern[str], str]] = []
    context_patterns: list[tuple[re.Pattern[str], str]] = []
    strict_patterns: list[tuple[re.Pattern[str], str]] = []

    for pattern, pid, scope in _PATTERNS:
        compiled = re.compile(pattern, re.IGNORECASE)
        entry = (compiled, pid)
        if scope == "all":
            all_patterns.append(entry)
            context_patterns.append(entry)
            strict_patterns.append(entry)
        elif scope == "context":
            context_patterns.append(entry)
            strict_patterns.append(entry)
        elif scope == "strict":
            strict_patterns.append(entry)

    _COMPILED = {
        "all": all_patterns,
        "context": context_patterns,
        "strict": strict_patterns,
    }


_compile()


def scan_for_threats(content: str, scope: str = "context") -> list[str]:
    """Return a list of matched pattern IDs in content at the given scope.

    Args:
        content: Text to scan.
        scope: Pattern set to apply — "all", "context", or "strict".

    Returns:
        List of matched pattern IDs. Empty list means clean.
    """
    if not content:
        return []

    findings: list[str] = []

    # Invisible unicode — single pass through the character set.
    char_set = set(content)
    for ch in char_set & INVISIBLE_CHARS:
        findings.append(f"invisible_unicode_U+{ord(ch):04X}")

    patterns = _COMPILED.get(scope)
    if patterns is None:
        raise ValueError(f"scan_for_threats: unknown scope {scope!r}")
    for compiled, pid in patterns:
        if compiled.search(content):
            findings.append(pid)

    return findings


def first_threat_message(content: str, scope: str = "strict") -> str | None:
    """Return a human-readable message for the first threat found, or None.

    Args:
        content: Text to scan.
        scope: Pattern set to apply.

    Returns:
        Human-readable threat description, or None if clean.
    """
    findings = scan_for_threats(content, scope=scope)
    if not findings:
        return None
    return f"Potential prompt injection detected: {', '.join(findings)}"
