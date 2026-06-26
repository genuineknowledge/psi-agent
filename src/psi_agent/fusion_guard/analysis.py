from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psi_agent.fusion_guard.policy import filter_allow_rules


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
) -> str:
    history_lines = "\n".join(
        f"{msg.get('role', 'unknown')}: {msg.get('content', '')}" for msg in history_messages[-12:]
    )
    return (
        "You are the Fusion-Guard intent analyzer.\n"
        f"SESSION_ID: {session_id or 'unknown'}\n"
        "HISTORY_BEGIN\n"
        f"{history_lines}\n"
        "HISTORY_END\n"
        "USER_MESSAGE_BEGIN\n"
        f"{latest_user_message.get('content', '')}\n"
        "USER_MESSAGE_END\n"
        "Output exactly DENY, NONE, or SELinux allow rules."
    )


def parse_intent_analysis_reply(raw: str) -> IntentAnalysisResult:
    cleaned = raw.strip()
    if cleaned == "DENY":
        return IntentAnalysisResult(decision="deny", rules=[], raw_reply=raw)
    if cleaned in {"", "NONE"}:
        return IntentAnalysisResult(decision="none", rules=[], raw_reply=raw)

    rules = filter_allow_rules(cleaned.splitlines())
    if not rules:
        return IntentAnalysisResult(decision="none", rules=[], raw_reply=raw)
    return IntentAnalysisResult(decision="allow_rules", rules=rules, raw_reply=raw)
