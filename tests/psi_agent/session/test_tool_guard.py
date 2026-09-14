"""工具闸门的判据：演练会话不许对外动手，读操作照常。

为什么这些判据不是"设计洁癖"
----------------------------
2026-09-14 的评测里，一条"要求模型拒绝"的用例被照做了：`meeting_session_notify` 真把当天
纪要发进了员工群。之后加固时先写成部署侧热修（90 行未提交的补丁），独立复核指出：
"r7 的安全不变量成立，但它依赖一份工作区改动，一次部署/回滚就退回原样"。所以这些判据
必须进版本库，并且覆盖"清单不全"这个当初的失效模式本身。
"""
# ruff: noqa: RUF002  —— 中文散文里的全角标点是有意为之

from __future__ import annotations

import pytest

from psi_agent.session import tool_guard as g

EVAL_SESSION = "feishu-ou_dsh_eval_r8_b01"
REAL_SESSION = "feishu-ou_c64efaf61e8c6c4c75ac0103a76d4bc7"


@pytest.fixture(autouse=True)
def _default_prefixes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(g.SESSION_PREFIXES_ENV, raising=False)


@pytest.mark.parametrize(
    ("tool", "args", "blocked"),
    [
        ("meeting_session_notify", {}, True),  # 真发过消息的那个入口
        ("feishu_message_send", {}, True),
        ("positive_negative_candidate_card", {}, True),  # r7 实测被拦下的那一次
        ("positive_negative_case_confirm", {}, True),
        ("meeting_pipeline_replay", {}, True),
        ("schedule_manage", {"action": "delete"}, True),
        ("schedule_manage", {"action": "list"}, False),  # 读定时不该被拦
        ("meeting_session_read", {}, False),
        ("positive_negative_rules", {}, False),
    ],
)
def test_outbound_tools_are_blocked_in_rehearsal_sessions(tool: str, args: dict, blocked: bool) -> None:
    assert (g.screen_tool_call(EVAL_SESSION, tool, args) is not None) is blocked


@pytest.mark.parametrize(
    ("method", "blocked"),
    [("GET", False), ("", False), ("POST", True), ("DELETE", True), ("PUT", True)],
)
def test_generic_api_caller_is_screened_by_method(method: str, blocked: bool) -> None:
    """`feishu_api` 是通用调用器：端点表里 reply/forward/撤回/pins 都没有硬拦。

    靠枚举工具名堵不住这扇门，所以对这类调用器按方法放行读、拦写。
    """
    args = {"method": method, "uri": "/open-apis/im/v1/messages"}
    assert (g.screen_tool_call(EVAL_SESSION, "feishu_api", args) is not None) is blocked


def test_real_user_sessions_are_untouched() -> None:
    """真实用户（`feishu-ou_<32位hex>`）一个都不该命中。"""
    for tool in sorted(g.OUTBOUND_TOOLS | g.READONLY_APIS):
        assert g.screen_tool_call(REAL_SESSION, tool, {"method": "POST", "action": "delete"}) is None


def test_empty_prefix_env_turns_the_guard_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式置空 = 关闭（部署要跑真实流量时用得上）。"""
    monkeypatch.setenv(g.SESSION_PREFIXES_ENV, "")
    assert g.screen_tool_call(EVAL_SESSION, "meeting_session_notify", {}) is None


def test_custom_prefix_env_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(g.SESSION_PREFIXES_ENV, "feishu-ou_smoke_")
    assert g.screen_tool_call("feishu-ou_smoke_1", "feishu_message_send", {}) is not None
    assert g.screen_tool_call(EVAL_SESSION, "feishu_message_send", {}) is None


def test_rejection_text_says_what_to_do_instead() -> None:
    """拒绝文案要给出替代路径：只说"不行"会让模型反复换姿势重试。"""
    text = g.screen_tool_call(EVAL_SESSION, "meeting_session_notify", {}) or ""
    assert "未执行" in text
    assert "确认" in text
