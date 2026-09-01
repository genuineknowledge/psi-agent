"""会话归属判定 —— 「这个 session 是不是这个飞书用户的」。

单独一个文件而非塞进 ``_routes.py``: 这是本包里唯一的**安全**判定, 判错的后果是
陌生人互相看见对话内容。纯函数 + 零 I/O, 于是能被单测密集覆盖(见
``tests/psi_agent/gateway/test_feishu_identity.py``)。

两类 session 各有判据:

* **机器人派生的私聊** ``feishu-<open_id>`` —— id 本身就是身份, 直接与
  ``FeishuManager.session_id_for(open_id)`` 比对。
* **网页新建的会话** —— id 是随机 uuid, 认不出主人; 靠 **workspace 等于该 open_id 的
  workspace** 认。这是「同一个人的多个会话共享一个 workspace」设计的直接回报。

群聊第一版不显示(见 PR #755 讨论), 故群会话恒不拥有。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Protocol

from psi_agent.gateway.feishu._feishu_manager import FeishuManager

GROUP_SESSION_PREFIX = "feishu-chat-"


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

    Path comparison must NOT use ``os.path.samefile`` 因为这些路径可能不存在,
    本判定必须纯且能处理假设路径。用 normcase/normpath/abspath 三层。
    """
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(os.path.abspath(a))) == os.path.normcase(
        os.path.normpath(os.path.abspath(b))
    )


def owns_session(open_id: str, session_id: str, workspace: str, fm: FeishuManager) -> bool:
    """*open_id* 是否有权看 *session_id*。

    空 *open_id* (未登录) 恒为假 —— 否则空身份会变成万能钥匙。
    """
    if not open_id or not session_id:
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
