"""未迁移组合的**可操作报错**:把"连不上演示库"翻译成"这个参数组合没迁"。

## 为什么需要这一层

``_formal.dispatch`` 对**未迁移的工具 / scope / 参数组合**返回 ``None``,调用方于是落到
演示实现 —— 演示实现去连演示 MySQL(``weekly_mock``)。演示环境里这没问题;但国数生产
环境**没有那台库**,于是 agent 一用默认参数就拿到::

    {"ok": false, "error": {"code": "store_unreachable",
     "message": "cannot reach mysql://weekly_ro@127.0.0.1:3306/weekly_mock: Connection refused"}}

这条错误的两个害处:

1. 它**不是**"数据库故障",却长得像 —— 主 Agent 会去查一个根本不存在的故障;
2. 错误信息指向一个与国数无关的 MySQL,主 Agent 无从判断该换参数。

所以正式源模式下遇到"演示库不可用",一律改报 ``not_migrated`` 并指路。
**演示模式(未开正式源)保持原样**:那时连不上演示库就是真的连不上,报错必须照实说。

## 归属

这一层刻意放在独立模块:``server.py`` 一 import 就拉起 ``mcp`` 包,而单测要能
**不连库、不拉 mcp** 地把这条判定钉住。
"""

from __future__ import annotations

import _formal

MIGRATION_HINT = "请改用已迁移的 scope / 参数(见 CHATBI_o2oa_接入说明.md 的调用建议)"

CODE = "not_migrated"
"""正式源模式下"该参数组合尚未迁移"的错误码(取代 store_unreachable)。"""

STORE_CODE = "store_unreachable"
"""数据源不可达的原始错误码。

演示源(MySQL)与正式源(PG)连不上都用它 —— 两边是同一个语义。演示模式下照原样返回;
正式模式下,演示路径那一路由 ``server._guard`` 翻译成 ``CODE``(那确实是"没迁"),
正式源自己那一路由 ``formal_source_unreachable_code()`` 翻译(那是真故障,不能混)。
"""

FORMAL_SOURCE_CODE = "formal_source_unreachable"
"""**正式源自己**连不上时的错误码。

与 ``not_migrated`` 必须分开,两者要调用方做的事完全不同:

* ``not_migrated`` —— "这组参数没迁,演示库也不可用" ⇒ 换参数 / 换 scope(**口径问题**);
* ``formal_source_unreachable`` —— "国数 o2oa 连不上" ⇒ 查网络、查库、查只读账号
  (**基础设施问题**)。

混成一个码的代价是真实的:把基础设施故障说成"参数没迁",调用方会一直换参数试。
"""


def should_translate() -> bool:
    """是否把"演示库不可用"翻译成 ``not_migrated``(仅正式源模式)。"""
    return _formal.enabled()


def migrated(tool: str) -> bool:
    """该工具是否已有正式源接线。

    真则错误信息说"**这组参数**未迁移"(工具本身在),假则说"**工具**未迁移" ——
    两者给主 Agent 的下一步动作不同(换参数 vs 换工具),不能合并成一句话。
    """
    return tool in _formal._HANDLERS


def not_migrated_message(tool: str, cause: str) -> str:
    head = f"{tool} 的这组参数未迁移到正式源" if migrated(tool) else f"{tool} 未迁移到正式源"
    return f"{head},演示库不可用({cause});{MIGRATION_HINT}"


def translate_formal_error(tool: str, payload: dict) -> dict:
    """把**正式源自己**返回的错误信封也过一遍这层翻译。

    为什么要在这里做:``_formal.envelope`` 现在会把驱动异常包成
    ``{"ok": false, "error": {"code": "store_unreachable", ...}}``(容器实跑抓到的:
    原先异常一路冒到 MCP 层,工具返回的是一句 ``Error executing tool ...`` 文本,
    连 ``error.code`` 都读不到)。那个信封走的是 ``dispatch`` 的返回值,不经过
    ``server._guard`` 的 ``except`` 分支 —— 兜底翻译必须同时覆盖**两条路**,
    否则"正式源连不上"在演示侧被翻译、在正式侧又漏出去,又回到两种读法。

    只认 ``store_unreachable`` 一个码:那是"数据源不可达"的统一语义,
    其余错误(``table_not_granted`` / ``invalid_argument`` / ``task_not_formal`` …)
    都是**已经可操作**的契约错误,不该被改写。
    """
    error = payload.get("error") or {}
    if error.get("code") != STORE_CODE:
        return payload
    return {
        "ok": False,
        "error": {
            "code": FORMAL_SOURCE_CODE,
            "message": (
                f"正式源(国数 o2oa / PostgreSQL)当前不可达:{error.get('message')};"
                "这不是参数问题,请先确认到 o2oa 的网络与只读账号是否正常"
            ),
        },
    }
