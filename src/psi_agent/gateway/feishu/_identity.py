"""会话归属判定 —— 「这个 session 是不是这个飞书用户的」。

单独一个文件而非塞进 ``_routes.py``: 这是本包里唯一的**安全**判定, 判错的后果是
陌生人互相看见对话内容。纯函数 + 零 I/O, 于是能被单测密集覆盖(见
``tests/psi_agent/gateway/test_feishu_identity.py``)。

两类 session 各有判据:

* **机器人派生的私聊** ``feishu-<open_id>`` —— id 本身就是身份, 直接与
  ``FeishuManager.session_id_for(open_id)`` 比对。
* **网页新建的会话** —— id 是随机 uuid, 认不出主人; 靠 **workspace 等于该 open_id 的
  workspace** 认。这是「同一个人的多个会话共享一个 workspace」设计的直接回报。
* **调度 Session** ``scheduler-*`` —— 实现细节, 默认对飞书用户隐藏。唯一例外是
  **组织共享会话**: 公司级种子任务 workspace (``PSI_SEED_SCHEDULES_WORKSPACE``,
  会议自动化等公司级任务都落在这里) 派生的调度 Session 对全体已登录用户开放
  **只读历史** (会议纪要为组织共享资料), 匿名请求仍拒绝, 聊天在路由层单独拒绝。

群聊第一版不显示(见 PR #755 讨论), 故群会话恒不拥有。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Protocol

from psi_agent.gateway.feishu._feishu_manager import (
    FEISHU_SESSION_PREFIX,
    FeishuManager,
    _same_workspace,
)

GROUP_SESSION_PREFIX = f"{FEISHU_SESSION_PREFIX}chat-"
SCHEDULER_SESSION_PREFIX = "scheduler-"
SEED_WORKSPACE_ENV = "PSI_SEED_SCHEDULES_WORKSPACE"
"""公司级种子任务落点 (与 ``gateway/__init__.py`` 构造 SchedulerManager 时读的是
同一个变量, 别处改名这里必须同步)。

组织可见性是公司级任务的固有属性, 不另设开关: seed workspace 派生的 ``scheduler-*``
会话对全体已登录飞书用户只读可见; 个人 workspace 的调度会话 (用户自建定时任务) 与
未启用公司级任务时 (env 空) 一律保持隐藏 —— 与既有默认行为一致。
"""


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


def _seed_workspace() -> str:
    """公司级种子任务 workspace (每次调用读 env, 便于测试与热改配置)。"""
    return os.environ.get(SEED_WORKSPACE_ENV, "").strip()


def is_org_session(session_id: str, workspace: str) -> bool:
    """*session_id* 是否属于组织共享调度会话 (对已登录用户只读可见)。

    判定 = 「调度 Session」且「其 workspace == 公司级种子任务 workspace
    (``PSI_SEED_SCHEDULES_WORKSPACE``)」。不依赖任何固定 session id 字符串 ——
    组织任务用哪个 Session 由 seed workspace 派生, 与 id 派生规则解耦。
    """
    if not session_id.startswith(SCHEDULER_SESSION_PREFIX):
        return False
    org_workspace = _seed_workspace()
    return bool(org_workspace) and _same_workspace(workspace, org_workspace)


def owns_session(open_id: str, session_id: str, workspace: str, fm: FeishuManager) -> bool:
    """*open_id* 是否有权看 *session_id*。

    空 *open_id* (未登录) 恒为假 —— 否则空身份会变成万能钥匙。
    """
    if not open_id or not session_id:
        return False
    if session_id.startswith(SCHEDULER_SESSION_PREFIX):
        # Scheduler sessions are implementation details; only the configured
        # org-shared workspace(s) expose their history to Feishu users.
        return is_org_session(session_id, workspace)
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
