"""飞书路由判定 —— 群聊/私聊分流与路由键派生。"""

from __future__ import annotations

import pytest

from psi_agent._feishu_routing import GROUP_CHAT_TYPES, is_group_chat, route_key


@pytest.mark.parametrize("chat_type", ["group", "topic"])
def test_group_types_with_chat_id_are_group(chat_type: str) -> None:
    assert is_group_chat("oc_abc", chat_type) is True


@pytest.mark.parametrize("chat_type", ["group", "topic"])
def test_group_types_without_chat_id_fall_back_to_dm(chat_type: str) -> None:
    """chat_id 缺失时不能按群路由, 否则建出 feishu-chat- 这种无主 session。"""
    assert is_group_chat("", chat_type) is False


@pytest.mark.parametrize("chat_type", ["p2p", "", "unknown"])
def test_non_group_types_are_never_group(chat_type: str) -> None:
    assert is_group_chat("oc_abc", chat_type) is False
    assert is_group_chat("", chat_type) is False


def test_group_chat_types_membership() -> None:
    assert frozenset({"group", "topic"}) == GROUP_CHAT_TYPES


@pytest.mark.parametrize("chat_type", ["group", "topic"])
def test_route_key_for_group_uses_chat_id(chat_type: str) -> None:
    assert route_key("ou_sender", "oc_abc", chat_type) == "chat:oc_abc"


@pytest.mark.parametrize(
    ("chat_id", "chat_type"),
    [("", "group"), ("", "topic"), ("oc_abc", "p2p"), ("", "p2p"), ("", "")],
)
def test_route_key_for_dm_uses_bare_open_id(chat_id: str, chat_type: str) -> None:
    assert route_key("ou_sender", chat_id, chat_type) == "ou_sender"


def test_route_key_namespaces_do_not_collide() -> None:
    """chat: 前缀隔离两个命名空间, 免得 chat_id 与 open_id 相撞。"""
    group = route_key("ou_x", "oc_x", "group")
    dm = route_key("oc_x", "", "p2p")
    assert group != dm
