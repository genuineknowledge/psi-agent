"""Mentor ledger — pure data: the fixed column definition and its request builders.

**This module must stay dependency-free of ``_feishu_impl``.** It exists to break a
real import cycle: ``_feishu_impl`` needs the ledger schema (and the list-tables
request) at module scope, while ``_feishu/mentor_ledger.py`` needs ``_feishu_impl``
as ``_core`` for the shared client/token layer. With both names living in
``mentor_ledger``, importing that module *first* — which the tool registry does
whenever glob order gets there before ``_feishu_impl`` — failed with
``cannot import name '_LEDGER_SCHEMA_FIELDS' from partially initialized module``,
taking down every tool file that reaches Feishu. Load order used to hide it only
because ``_feishu_impl``'s own ``from _feishu.mentor_ledger import ...`` sits at the
bottom of that file.

Only the two things ``_feishu_impl`` pulls in eagerly live here, and only because
they are inert: a literal list, and request builders that construct a
``BaseRequest`` without touching the client. Anything that needs ``_core`` (i.e.
anything that *performs* a call) belongs in ``mentor_ledger.py``, which may import
this module freely — the dependency runs one way, so no order can reintroduce the
cycle. ``lark_channel`` is safe to import here: it is an installed package, not part
of the tools tree.
"""

from __future__ import annotations

from typing import Any

from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

_LEDGER_NAME_PREFIX = "TODO 台账-"

# Fixed column definition for directly-provisioned ledgers. 负责人/mentor are
# PERSON (11) columns — never text. 层级 and 父项 are both single-select (3)
# whose options company-todo-sync syncs from the current cycle BEFORE writing
# rows: 层级 options are per-item level tags ("大目标1", "小目标1", "todo1",
# numbered independently per level, color by level kind — palette 1/3/5);
# 父项 options are the parent-able subset of those tags (大目标*/小目标*).
# 截止日期 is a plain date (5) — callers write nothing (not a default) when
# the source cell has no deadline.
_LEDGER_SCHEMA_FIELDS: list[dict[str, Any]] = [
    {"field_name": "周期日期", "type": 5},
    {"field_name": "负责人", "type": 11},
    {"field_name": "mentor", "type": 11},
    {"field_name": "层级", "type": 3, "property": {"options": []}},
    {"field_name": "父项", "type": 3, "property": {"options": []}},
    {"field_name": "标题", "type": 1},
    {"field_name": "截止日期", "type": 5},
    {
        "field_name": "状态",
        "type": 3,
        "property": {
            "options": [
                {"name": "待开始", "color": 0},
                {"name": "进行中", "color": 1},
                {"name": "已交付", "color": 2},
                {"name": "已闭环", "color": 3},
                {"name": "未闭环逾期", "color": 4},
                {"name": "请假顺延", "color": 5},
            ]
        },
    },
    {
        "field_name": "闭环五要素",
        "type": 4,
        "property": {
            "options": [
                {"name": "有验收人"},
                {"name": "截止到期或提前"},
                {"name": "已勾选提交成果"},
                {"name": "mentor已评分"},
                {"name": "评价已回写wiki"},
            ]
        },
    },
    {"field_name": "mentor打分", "type": 2},
    {"field_name": "mentor评语", "type": 1},
    {"field_name": "外部成果", "type": 1},
    {"field_name": "友商对比", "type": 1},
    {"field_name": "任务GUID", "type": 1},
]


def _ledger_base_name(mentor_name: str) -> str:
    return f"{_LEDGER_NAME_PREFIX}{mentor_name.strip()}"


def _build_list_tables_request(app_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables"
    req.paths["app_token"] = app_token
    req.add_query("page_size", "20")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req
