"""飞书私聊/群聊路由判定 —— Gateway 与 Channel 共用。

判定曾在三处各写一遍 (``gateway/feishu/_feishu_manager.py`` 的 ``_is_group``、
``channel/feishu/client.py`` 的 ``_cache_key`` 与一处内联判定), 群聊类型常量
另在两处各定义一遍。判定漂移会让两个陌生人共享同一份上下文与 workspace ——
是**隐私事故**而非美观问题, 故收敛到此处唯一定义。

放在 ``psi_agent`` 顶层 (与 ``_appdata`` / ``_sockets`` 同级) 而非任一组件内,
避免在 Gateway 与 Channel 之间新造一条跨组件依赖。

``session_id`` / workspace 目录派生时的 ``-`` → ``_`` 转义**不在此处**: 那只
服务 Gateway 侧, Channel 不派生这些, 上提会把单方职责伪装成共享契约。
"""

from __future__ import annotations

GROUP_CHAT_TYPES = frozenset({"group", "topic"})


def is_group_chat(chat_id: str, chat_type: str) -> bool:
    """群聊判定: 类型是 group/topic **且** ``chat_id`` 非空。

    ``chat_id`` 缺失时不能按群路由 (否则会建出 ``feishu-chat-`` 这种无主
    session), 故退回按发送者 open_id —— 宁可不隔离, 也不建垃圾 session。
    """
    return chat_type in GROUP_CHAT_TYPES and bool(chat_id)


def route_key(open_id: str, chat_id: str, chat_type: str) -> str:
    """路由表 / socket 缓存共用的键: 群聊 ``chat:<chat_id>``, 私聊裸 ``open_id``。

    ``chat:`` 前缀隔离两个命名空间, 免得 chat_id 与 open_id 相撞。群聊整群共用
    一个键 (同群不同发言者须命中同一条缓存, 否则每人各打一次 Gateway)。
    """
    if is_group_chat(chat_id, chat_type):
        return f"chat:{chat_id}"
    return open_id
