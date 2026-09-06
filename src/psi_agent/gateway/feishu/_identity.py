"""会话归属判定 —— 「这个 session 是不是这个飞书用户的」。

单独一个文件而非塞进 ``_routes.py``: 这是本包里唯一的**安全**判定, 判错的后果是
陌生人互相看见对话内容。纯函数 + 零 I/O, 于是能被单测密集覆盖(见
``tests/psi_agent/gateway/test_feishu_identity.py``)。

两类 session 各有判据:

* **机器人派生的私聊** ``feishu-<open_id>`` —— id 本身就是身份, 直接与
  ``FeishuManager.session_id_for(open_id)`` 比对。
* **网页新建的会话** —— id 是随机 uuid, 认不出主人; 靠 **workspace 等于该 open_id 的
  workspace** 认。这是「同一个人的多个会话共享一个 workspace」设计的直接回报。
* **固定会议 Session** ``meeting-session`` —— 会议纪要是组织共享资料, 所有已登录飞书用户
  都可读取它的历史; 匿名请求仍拒绝。其他调度 Session 继续隐藏。

群聊第一版不显示(见 PR #755 讨论), 故群会话恒不拥有。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from psi_agent.gateway.feishu._feishu_manager import (
    FEISHU_SESSION_PREFIX,
    FeishuManager,
    _same_workspace,
)

GROUP_SESSION_PREFIX = f"{FEISHU_SESSION_PREFIX}chat-"
SCHEDULER_SESSION_PREFIX = "scheduler-"
PUBLIC_MEETING_SESSION_ID = "meeting-session"


class SessionLike(Protocol):
    """会话对象最小接口 —— 判定只需 id 与 workspace。"""

    id: str
    workspace: str


def is_group_session(session_id: str) -> bool:
    """判定 session_id 是否为群聊。

    群聊 session_id 以 ``feishu-chat-`` 开头; 私聊为 ``feishu-<open_id>`` 其中 ``-`` 被转义成
    ``_``, 所以私聊恒以 ``feishu-chat_`` 开头(注: 下划线)。
    """
    return session_id.startswith(GROUP_SESSION_PREFIX)


def _same_path(a: str, b: str) -> bool:
    """路径相等性判定, 忽略尾斜杠/大小写(Windows)/相对段。

    实现转发到 ``_feishu_manager._same_workspace`` —— 归属判定 (判错=陌生人互看对话) 与
    adopt 时的 workspace 错位告警问的是同一个问题「这两个字符串是不是同一个目录」, 各留一份
    实现迟早在某一支上分歧。本名字保留: 既有调用点与用例都按它写。
    """
    return _same_workspace(a, b)


def owns_session(open_id: str, session_id: str, workspace: str, fm: FeishuManager) -> bool:
    """*open_id* 是否有权看 *session_id*。

    空 *open_id* (未登录) 恒为假 —— 否则空身份会变成万能钥匙。
    """
    if not open_id or not session_id:
        return False
    if session_id == PUBLIC_MEETING_SESSION_ID:
        return True
    # Scheduler sessions are implementation details.  The shared meeting
    # session above is the only scheduler intentionally exposed to Feishu.
    if session_id.startswith(SCHEDULER_SESSION_PREFIX):
        return False
    if is_group_session(session_id):
        return False
    if session_id == fm.session_id_for(open_id):
        return True
    # 网页新建的 uuid session: 落在本人 workspace 下即为本人所有。
    return _same_path(workspace, fm.workspace_for(open_id))


def visible_sessions[S: SessionLike](open_id: str, sessions: Sequence[S], fm: FeishuManager) -> list[S]:
    """从全量 session 里筛出 *open_id* 可见的那些, 保持入参顺序。

    泛型而非固定 ``SessionLike``: 这是个筛子, 元素原样出去。写死协议类型会把调用方的
    ``SessionInfo`` 擦成协议, 下游要 ``SessionInfo`` 的地方就得靠抑制注释放行。
    """
    return [s for s in sessions if owns_session(open_id, s.id, s.workspace or "", fm)]
