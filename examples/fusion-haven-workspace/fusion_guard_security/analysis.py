from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fusion_guard_security.policy import filter_allow_rules


@dataclass(frozen=True)
class IntentAnalysisResult:
    decision: str
    rules: list[str]
    raw_reply: str


def build_intent_analysis_prompt(
    *,
    history_messages: list[dict[str, Any]],
    latest_user_message: dict[str, Any],
    session_id: str | None,
    command: str,
    script_contexts: list[dict[str, str]] | None = None,
    workspace_agent_id: str | None = None,
    policy_domain: str | None = None,
) -> str:
    history_lines = "\n".join(
        f"{msg.get('role', 'unknown')}: {msg.get('content', '')}" for msg in history_messages[-12:]
    )
    return (
        "You are the Fusion-Guard intent analyzer for a Dolphin-Agent workspace tool.\n"
        "Decide whether the latest user request and actual bash command are safe for a sandboxed bash tool.\n"
        "Treat the command and any script content as authoritative execution context, not just the user message.\n"
        "Output exactly DENY, NONE, or valid SELinux allow rules, one per line.\n"
        f"SESSION_ID: {session_id or 'unknown'}\n"
        f"WORKSPACE_AGENT_ID: {workspace_agent_id or 'unknown'}\n"
        f"POLICY_DOMAIN: {policy_domain or 'unknown'}\n"
        "HISTORY_BEGIN\n"
        f"{history_lines}\n"
        "HISTORY_END\n"
        "USER_MESSAGE_BEGIN\n"
        f"{latest_user_message.get('content', '')}\n"
        "USER_MESSAGE_END\n"
        "COMMAND_BEGIN\n"
        f"{command}\n"
        "COMMAND_END\n"
        f"{_format_script_contexts(script_contexts or [])}"
    )


def _format_script_contexts(script_contexts: list[dict[str, str]]) -> str:
    if not script_contexts:
        return "SCRIPT_CONTEXT_BEGIN\n(no .sh script content detected)\nSCRIPT_CONTEXT_END"

    blocks: list[str] = ["SCRIPT_CONTEXT_BEGIN"]
    for item in script_contexts:
        blocks.append(f"SCRIPT_PATH: {item.get('path', 'unknown')}")
        note = item.get("note", "").strip()
        if note:
            blocks.append(f"SCRIPT_NOTE: {note}")
        blocks.append("SCRIPT_CONTENT_BEGIN")
        blocks.append(item.get("content", ""))
        blocks.append("SCRIPT_CONTENT_END")
    blocks.append("SCRIPT_CONTEXT_END")
    return "\n".join(blocks)


def parse_intent_analysis_reply(raw: str) -> IntentAnalysisResult:
    cleaned = raw.strip()
    if cleaned == "DENY":
        return IntentAnalysisResult(decision="deny", rules=[], raw_reply=raw)
    if cleaned in {"", "NONE"}:
        return IntentAnalysisResult(decision="none", rules=[], raw_reply=raw)

    rules = filter_allow_rules(cleaned.splitlines())
    return IntentAnalysisResult(
        decision="allow_rules" if rules else "none",
        rules=rules,
        raw_reply=raw,
    )
