from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from psi_agent.gateway.feishu._feishu_manager import FeishuManager
from psi_agent.gateway.feishu._identity import (
    is_group_session,
    owns_session,
    visible_sessions,
)
from psi_agent.runtime._session_manager import SessionManager

_NO_SM = cast(SessionManager, None)


@dataclass
class _S:
    """最小 SessionLike 替身 —— 判定只看 id 与 workspace。"""

    id: str
    workspace: str


def test_is_group_session() -> None:
    assert is_group_session("feishu-chat-oc_room") is True
    # 私聊不能被当成群聊: 转义后是 ``feishu-chat_oc_x``, 只差一个字符。
    assert is_group_session("feishu-chat_oc_x") is False
    assert is_group_session("feishu-ou_alice") is False
    assert is_group_session("3f2a1b0c-uuid") is False


def test_owns_own_bot_session(tmp_path: Path) -> None:
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    ws = fm.workspace_for("ou_alice")
    assert owns_session("ou_alice", "feishu-ou_alice", ws, fm) is True


def test_does_not_own_others_bot_session(tmp_path: Path) -> None:
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    ws_bob = fm.workspace_for("ou_bob")
    assert owns_session("ou_alice", "feishu-ou_bob", ws_bob, fm) is False


def test_owns_web_uuid_session_by_workspace(tmp_path: Path) -> None:
    """网页新建的 uuid session 认不出主人, 靠 workspace 归属认。"""
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    ws = fm.workspace_for("ou_alice")
    assert owns_session("ou_alice", "3f2a1b0c-uuid", ws, fm) is True
    assert owns_session("ou_bob", "3f2a1b0c-uuid", ws, fm) is False


def test_group_session_never_owned(tmp_path: Path) -> None:
    """群聊第一版不显示 —— 即便 workspace 在自己名下也不算自己的。

    第二条断言传的是 **alice 本人的** workspace, 这才是真正吃劲的那条: 传群自己的
    workspace 时 ``_same_path`` 早就返回 False 了, 群过滤根本没成为决定因素(实测:
    把 ``is_group_session`` 分支删掉, 只有本人 workspace 这条会红)。
    可达性: ``/feishu/route`` 接受 body 里的 ``workspace``, 群会话的 workspace 是可以
    落到某人目录下的 —— 那时挡住多人群聊上下文的就只剩这行过滤。
    """
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    ws = fm.workspace_for("chat:oc_room")
    assert owns_session("ou_alice", "feishu-chat-oc_room", ws, fm) is False
    assert owns_session("ou_alice", "feishu-chat-oc_room", fm.workspace_for("ou_alice"), fm) is False


def test_empty_open_id_owns_nothing(tmp_path: Path) -> None:
    """未登录(空身份)不得命中任何东西 —— 否则空 open_id 会变成万能钥匙。"""
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    assert owns_session("", "feishu-ou_alice", fm.workspace_for("ou_alice"), fm) is False
    assert owns_session("", "", "", fm) is False


def test_org_session_is_readable_by_any_authenticated_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """配置了组织共享 workspace 后, 其调度 Session 对全体已登录用户只读可见。"""
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    org_workspace = str(tmp_path / ".org-session")
    monkeypatch.setenv("PSI_SEED_SCHEDULES_WORKSPACE", org_workspace)

    assert owns_session("ou_alice", "scheduler-meeting", org_workspace, fm) is True
    assert owns_session("ou_bob", "scheduler-meeting", org_workspace, fm) is True
    assert owns_session("", "scheduler-meeting", org_workspace, fm) is False


def test_org_visibility_is_workspace_driven_not_id_driven(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """判定认 workspace 不认固定 id: 同一 id 落在别的 workspace 就不可见。

    旧实现按 ``meeting-session`` 字符串放行; 通用化后任何 ``scheduler-*`` id 只要
    落在配置的 workspace 上即组织共享, 否则一律隐藏。
    """
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    org_workspace = str(tmp_path / ".org-session")
    other_workspace = str(tmp_path / "other")
    monkeypatch.setenv("PSI_SEED_SCHEDULES_WORKSPACE", org_workspace)

    assert owns_session("ou_alice", "scheduler-meeting", org_workspace, fm) is True
    assert owns_session("ou_alice", "scheduler-meeting", other_workspace, fm) is False


def test_visible_sessions_includes_org_session_but_not_other_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    alice_workspace = fm.workspace_for("ou_alice")
    org_workspace = str(tmp_path / ".org-session")
    monkeypatch.setenv("PSI_SEED_SCHEDULES_WORKSPACE", org_workspace)
    rows = [
        _S("scheduler-org", org_workspace),
        _S("scheduler-other", alice_workspace),
    ]

    assert [s.id for s in visible_sessions("ou_alice", rows, fm)] == ["scheduler-org"]


def test_scheduler_session_is_hidden_without_org_config(tmp_path: Path) -> None:
    """未配置组织共享 workspace 时, 调度 Session 一律隐藏 (默认)。"""
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    assert owns_session("ou_alice", "scheduler-anything", fm.workspace_for("ou_alice"), fm) is False


def test_visible_sessions_filters(tmp_path: Path) -> None:
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    ws_a, ws_b = fm.workspace_for("ou_alice"), fm.workspace_for("ou_bob")
    ws_room = fm.workspace_for("chat:oc_room")
    rows = [
        _S("feishu-ou_alice", ws_a),
        _S("uuid-1", ws_a),
        _S("feishu-ou_bob", ws_b),
        _S("feishu-chat-oc_room", ws_room),
        # 群会话的 workspace 落在 alice 名下: 唯一挡它的就是群过滤那行。少了这行,
        # 上面那条 ws_room 的群会话靠 workspace 不等也会被滤掉, 于是过滤形同虚设。
        _S("feishu-chat-oc_inroom", ws_a),
    ]
    got = [s.id for s in visible_sessions("ou_alice", rows, fm)]
    assert got == ["feishu-ou_alice", "uuid-1"]


def test_path_comparison_is_normalized(tmp_path: Path) -> None:
    """workspace 比对必须归一化: 尾斜杠/大小写(Windows)/相对段不该改变归属。"""
    fm = FeishuManager(_sm=_NO_SM, _workspace_root=str(tmp_path))
    ws = fm.workspace_for("ou_alice")
    assert owns_session("ou_alice", "uuid-1", ws + "/", fm) is True
    assert owns_session("ou_alice", "uuid-1", ws + "/./", fm) is True
