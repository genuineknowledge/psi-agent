from __future__ import annotations

from psi_agent.fusion_guard.analysis import build_intent_analysis_prompt, parse_intent_analysis_reply
from psi_agent.fusion_guard.messages import normalize_denial_message
from psi_agent.fusion_guard.policy import build_policy_install_request


def test_prompt_delimits_user_message_and_history() -> None:
    prompt = build_intent_analysis_prompt(
        history_messages=[
            {"role": "system", "content": "system text"},
            {"role": "user", "content": "list files"},
        ],
        latest_user_message={"role": "user", "content": "run a command"},
        session_id="session-1",
    )

    assert "SESSION_ID: session-1" in prompt
    assert "HISTORY_BEGIN" in prompt
    assert "user: list files" in prompt
    assert "USER_MESSAGE_BEGIN\nrun a command\nUSER_MESSAGE_END" in prompt


def test_parse_intent_analysis_reply_distinguishes_deny_none_and_allow_rules() -> None:
    deny = parse_intent_analysis_reply("DENY")
    assert deny.decision == "deny"
    assert deny.rules == []

    none = parse_intent_analysis_reply("NONE")
    assert none.decision == "none"
    assert none.rules == []

    allow = parse_intent_analysis_reply(
        "explanation ignored\n"
        "allow fusionclaw_agent_main_session_x_t fusionclaw_agent_main_file_t:file { read getattr };\n"
        "allow passwd_file_t shadow_t:file { read };"
    )
    assert allow.decision == "allow_rules"
    assert allow.rules == [
        "allow fusionclaw_agent_main_session_x_t fusionclaw_agent_main_file_t:file { read getattr };"
    ]


def test_denial_message_and_policy_request_shape() -> None:
    assert (
        normalize_denial_message("policy denied") == "[Fusion-Guard] Security policy denied this request: policy denied"
    )

    request = build_policy_install_request(
        agent_id="main",
        rules=["allow a_t b_t:file { read };"],
        workspace_path="/tmp/workspace",
    )
    assert request == {
        "agent_id": "main",
        "workspace_path": "/tmp/workspace",
        "extra_rules": ["allow a_t b_t:file { read };"],
    }
