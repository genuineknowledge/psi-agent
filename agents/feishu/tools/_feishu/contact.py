"""Feishu Contact (通讯录) — departments, users by phone/email, user groups.

Split out of ``_feishu_impl.py`` by domain. The shared client/token layer stays
there: this module reaches it through ``_core`` so that everything patched on
``_feishu_impl`` (``_invoke``, ``_get_client``, ``_get_valid_uat``, ...) keeps
taking effect here. ``_feishu_impl`` re-exports every public name below, so tool
entrypoints keep importing it and nothing else has to change.
"""

from __future__ import annotations

import re
from typing import Any

import _feishu_impl as _core
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

# ── Contact (通讯录) — list department members ────────────────────────────────
#
# Get the roster for a department (or the whole org from root id "0"), so the
# agent has the user_id list needed to batch-query attendance/payroll. Tenant
# token works; the app's 通讯录权限范围 must cover the members you want to see.


def _build_dept_children_request(
    department_id: str, department_id_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/departments/:department_id/children"
    req.paths["department_id"] = department_id
    req.add_query("department_id_type", department_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_find_by_department_request(
    department_id: str, department_id_type: str, user_id_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/users/find_by_department"
    req.add_query("department_id", department_id)
    req.add_query("department_id_type", department_id_type)
    req.add_query("user_id_type", user_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def _members_of_department(
    department_id: str, department_id_type: str, user_id_type: str
) -> tuple[list[dict[str, str]], dict[str, Any] | None]:
    """All members directly in one department (paged). Returns (members, error_or_None)."""
    members: list[dict[str, str]] = []
    page_token = ""
    while True:
        res = await _core._invoke(
            _build_find_by_department_request(department_id, department_id_type, user_id_type, 50, page_token)
        )
        if not res["ok"]:
            return members, res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            members.append(
                {
                    "user_id": it.get("user_id", ""),
                    "open_id": it.get("open_id", ""),
                    "name": it.get("name", ""),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return members, None


async def _child_department_ids(department_id: str, department_id_type: str) -> list[str]:
    """Direct child department ids of a department (one level, paged)."""
    ids: list[str] = []
    page_token = ""
    while True:
        res = await _core._invoke(_build_dept_children_request(department_id, department_id_type, 50, page_token))
        if not res["ok"]:
            return ids
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            did = (
                it.get("department_id", "")
                if department_id_type == "department_id"
                else it.get("open_department_id", "")
            )
            if did:
                ids.append(did)
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return ids


async def list_department_members_impl(
    department_id: str = "0",
    department_id_type: str = "open_department_id",
    user_id_type: str = "open_id",
    recursive: bool = False,
) -> dict[str, Any]:
    """List members of a department. recursive=True walks sub-departments too.

    department_id "0" is the org root. Returns de-duplicated [{user_id, open_id, name}].
    """
    seen: set[str] = set()
    all_members: list[dict[str, str]] = []
    to_visit = [department_id]
    visited: set[str] = set()
    while to_visit:
        did = to_visit.pop()
        if did in visited:
            continue
        visited.add(did)
        members, err = await _members_of_department(did, department_id_type, user_id_type)
        if err is not None:
            return err
        for m in members:
            key = m.get("open_id") or m.get("user_id") or m.get("name")
            if key and key not in seen:
                seen.add(key)
                all_members.append(m)
        if recursive:
            child_type = "department_id" if department_id_type == "department_id" else "open_department_id"
            to_visit.extend(await _child_department_ids(did, child_type))
    return {
        "ok": True,
        "department_id": department_id,
        "recursive": recursive,
        "members": all_members,
        "count": len(all_members),
    }


# ── Contact — batch user detail (contact info: mobile / email / job title) ─────
#
# find_by_department only gives name + ids. To hand someone a colleague's contact
# details (so an employee stuck on a blocker can reach the right owner), fetch the
# full user records via the batch endpoint: mobile, email, job title, department.
# Tenant token works; the app's 通讯录权限范围 must cover the users, and reading
# mobile/email needs the corresponding contact scopes (see feishu_contact tool).


def _build_batch_users_request(user_ids: list[str], user_id_type: str, department_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/users/batch"
    for uid in user_ids:
        req.add_query("user_ids", uid)
    req.add_query("user_id_type", user_id_type)
    req.add_query("department_id_type", department_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def get_users_batch_impl(
    user_ids: str,
    user_id_type: str = "open_id",
    department_id_type: str = "open_department_id",
) -> dict[str, Any]:
    """Fetch full user records (contact details) for up to 50 ids in one call.

    Returns [{open_id, user_id, name, mobile, email, enterprise_email, job_title,
    department_ids, leader_user_id}] — the info needed to hand someone a colleague's
    contact details. mobile/email are only populated if the app has the matching
    contact scopes and 通讯录权限范围 covers the user.
    """
    ids = [uid.strip() for uid in user_ids.split(",") if uid.strip()]
    if not ids:
        return _core._error("user_ids is required (comma-separated ids).")
    if len(ids) > 50:
        return _core._error("Feishu allows at most 50 user_ids per batch call.")
    res = await _core._invoke(_build_batch_users_request(ids, user_id_type, department_id_type))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    users: list[dict[str, Any]] = []
    for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
        users.append(
            {
                "open_id": it.get("open_id", ""),
                "user_id": it.get("user_id", ""),
                "name": it.get("name", ""),
                "mobile": it.get("mobile", ""),
                "email": it.get("email", ""),
                "enterprise_email": it.get("enterprise_email", ""),
                "job_title": it.get("job_title", ""),
                "department_ids": it.get("department_ids", []),
                "leader_user_id": it.get("leader_user_id", ""),
            }
        )
    return {"ok": True, "user_id_type": user_id_type, "users": users, "count": len(users)}


# ── 通讯录管理 (contact admin) — 共用错误码 hint ────────────────────────────────
#
# 这一批端点全部只吃 tenant_access_token (scope contact:contact / contact:group /
# contact:functional_role), 所以调用一律 prefer="tenant" —— 传 prefer="user" 会去问
# 「这东西归谁」, 而通讯录条目不存在归属问题, 那个问题问出来就是答不上来的。
#
# 最常见的两类失败根本不是参数写错:
#   40004 / 41050 / 42009 —— 应用的「通讯录权限范围」没覆盖到目标 (后台配的, 改代码没用)
#   42010            —— 有些接口 (建用户组) 硬要求范围 = 全部成员
# 所以 hint 直接说去哪儿改, 而不是复述一遍 "no permission"。
_CONTACT_ADMIN_ERROR_HINTS = {
    40002: "根部门 (department_id='0') 不支持这个操作。",
    40004: "目标部门不在应用的「通讯录权限范围」内; 去开发者后台 > 应用权限 > 通讯录范围里加上该部门。",
    40008: "部门信息为空。",
    40014: "父部门不存在或不在通讯录范围内。",
    40015: "部门不存在; 用 feishu_department_tree 核对 department_id。",
    41001: "手机号已被租户内其他账号占用。",
    41002: "邮箱已被租户内其他账号占用。",
    41003: "该手机号和邮箱分属两个不同账号。",
    41004: "手机号格式非法; 非中国大陆号码要带 + 国家码 (如 +81...)。",
    41005: "邮箱格式非法。",
    41011: "user_id 重复; 换一个或留空让飞书自动分配。",
    41017: "缺 department_ids; 建用户必须指定至少一个部门。",
    41025: "orders 里引用了该用户并不属于的部门。",
    41030: "leader_user_id 不能是这个用户自己。",
    41033: "department_ids 超过 50 个。",
    41050: "没有该用户的操作权限; 该用户所在部门不在应用的通讯录范围内。",
    41052: "资源转交人非法 (已离职/不存在/不在通讯录范围)。",
    41059: "employee_type 非法; 1 正式 2 实习 3 外包 4 劳务 5 顾问 (自定义类型用后台的枚举号)。",
    41060: "employee_type 对应的人员类型已停用。",
    41071: "member_id_type 非法。",
    41072: "member_id 与 member_id_type 不匹配 (例如 id_type 填 open_id 却传了 user_id)。",
    41073: "member_id 非法。",
    41074: "member_type 非法; 用户组成员目前只支持 'user'。",
    41201: "角色名已存在 (租户内必须唯一)。",
    41202: "role_id 不存在; 角色 id 只能从建角色的返回值或管理后台「组织架构 > 角色管理」拿。",
    41208: "角色数量已达租户上限 500。",
    41209: "单个角色成员数已达上限 1000。",
    41410: "主部门的 department_order 必须是最大的那个。",
    42002: "group_id 非法; 用 GET /open-apis/contact/v3/group/simplelist 重新取。",
    42005: "该成员已在这个用户组里 (无需重复添加)。",
    42006: "该用户已离职, 不能加入用户组。",
    42009: "该用户组不在应用的通讯录权限范围内。",
    42010: "这个接口要求应用的通讯录权限范围是「全部成员」, 当前不是; 去开发者后台改范围。",
    42012: "用户组成员数超限 (单组 10 万; 全部普通组之和不得超过租户人数的 10 倍)。",
    42016: "用户组数量已达租户上限 500。",
    42029: "该字段不支持通过 OpenAPI 修改, 只能去管理后台改。",
    43005: "order 重复; 同一父部门下 order 必须唯一。",
    43010: "部门层级过大, 不支持递归查询; 改成逐层查 (recursive=False) 或换更小的子部门。",
    43011: "部门里还有用户, 删不掉; 先把人挪走或设为离职。",
    43012: "部门里还有子部门, 删不掉; 先删子部门 (从最深一层往上删)。",
    43022: "同一父部门下已有同名部门。",
    43023: "没有操作该部门的权限。",
    43024: "并发冲突, 稍后重试。",
    43029: "部门名不能含斜杠 '/'。",
    43030: "并发冲突, 稍后重试。",
    44037: "租户管理员不能被离职; 先去管理后台撤掉他的管理员身份。",
    44042: "该用户正在恢复流程中, 稍后重试。",
    44062: "该租户的账号只能通过「成员生命周期引擎」处理, 不能走这个接口。",
    48001: "搜索参数非法。",
    1970011: "page_size 越界 (关联组织列表要求 1-100)。",
    1970012: "page_token 非法; 用上一页返回的 page_token。",
}


# ── Contact — 按手机号/邮箱定位用户 (users/batch_get_id) ────────────────────────
#
# 补上「只有一串手机号/邮箱, 要拿 open_id」这个缺口: 按名字全局搜 (search/v1/user) 只能按
# *姓名* 搜且只吃 user token, 这个按联系方式精确命中且用 tenant token。
#
# 四个照着文档写也会踩的地方, 所以做成 Python 工具而不是留给 feishu_api:
#  1. 是 POST 不是 GET —— 查询条件在 body 里, 直觉上会写成 query 参数。
#  2. include_resigned 默认 false, 离职的人会**静默查不到** (不报错, 只是少一条),
#     于是「查无此人」和「已离职」看起来一模一样。默认改成 True 并回报 is_resigned,
#     让「查不到」真的只意味着查不到。
#  3. 响应只回显命中的那个键 (查邮箱回 email, 查手机回 mobile), 不回姓名 ——
#     所以这里顺手补一次 get_users_batch_impl 把姓名/部门带上, 否则拿到一串
#     ou_xxx 还得再调一次才知道是谁。
#  4. 不支持企业邮箱 (enterprise_email), 传了就是查不到; 非大陆手机号必须带 +国家码。
_BATCH_GET_ID_MAX = 50


def _build_batch_get_id_request(
    emails: list[str], mobiles: list[str], include_resigned: bool, user_id_type: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/contact/v3/users/batch_get_id"
    req.add_query("user_id_type", user_id_type)
    body: dict[str, Any] = {"include_resigned": include_resigned}
    if emails:
        body["emails"] = emails
    if mobiles:
        body["mobiles"] = mobiles
    req.body = body
    req.token_types = {AccessTokenType.TENANT}
    return req


def _split_contacts(raw: str) -> list[str]:
    """逗号/空格/分号分隔的联系方式列表 -> 去重后的列表 (保持顺序)。"""
    parts = [p.strip() for p in re.split(r"[,;\s]+", raw or "") if p.strip()]
    return list(dict.fromkeys(parts))


async def find_users_by_contact_impl(
    mobiles: str = "",
    emails: str = "",
    include_resigned: bool = True,
    user_id_type: str = "open_id",
) -> dict[str, Any]:
    """按手机号/邮箱精确定位用户, 返回 open_id 及姓名。

    返回 users[]: {open_id/user_id, matched_by, matched_value, name, ...,
    is_resigned, is_activated} 以及 not_found[] —— 哪些号码/邮箱没查到。
    """
    mobile_list = _split_contacts(mobiles)
    email_list = _split_contacts(emails)
    if not mobile_list and not email_list:
        return _core._error("至少要给一个 mobiles 或 emails (逗号分隔)。")
    for label, items in (("mobiles", mobile_list), ("emails", email_list)):
        if len(items) > _BATCH_GET_ID_MAX:
            return _core._error(f"{label} 一次最多 {_BATCH_GET_ID_MAX} 个, 当前 {len(items)} 个; 分批调用。")

    res = await _core._invoke(
        _build_batch_get_id_request(email_list, mobile_list, include_resigned, user_id_type),
        prefer="tenant",
    )
    if not res["ok"]:
        return _core._with_hint(res, _CONTACT_ADMIN_ERROR_HINTS)

    data = res["data"] if isinstance(res["data"], dict) else {}
    raw_list = data.get("user_list", []) if isinstance(data.get("user_list"), list) else []
    found: list[dict[str, Any]] = []
    seen_ids: list[str] = []
    matched_values: set[str] = set()
    for it in raw_list:
        if not isinstance(it, dict):
            continue
        uid = it.get("user_id", "") or ""
        # 飞书对查不到的条目也回一条 (只有 email/mobile 没有 user_id), 据此判定未命中。
        matched_by = "email" if it.get("email") else ("mobile" if it.get("mobile") else "")
        value = it.get("email", "") or it.get("mobile", "") or ""
        if not uid:
            continue
        # 只有真拿到 id 才算命中 —— 回显了号码但没有 user_id 恰恰是「查不到」,
        # 把它记成已命中会让 not_found 永远是空的。
        if value:
            matched_values.add(value)
        status = it.get("status", {}) if isinstance(it.get("status"), dict) else {}
        found.append(
            {
                "user_id": uid,
                "matched_by": matched_by,
                "matched_value": value,
                "is_resigned": bool(status.get("is_resigned")),
                "is_activated": bool(status.get("is_activated")),
                "is_frozen": bool(status.get("is_frozen")),
            }
        )
        seen_ids.append(uid)

    # 补姓名/部门: batch_get_id 只回 id, 不回姓名。拿不到就算了 (通常是缺
    # contact:contact.base:readonly), 联系方式->id 这个主要目的已经达成。
    if seen_ids and user_id_type == "open_id":
        detail = await _core.get_users_batch_impl(",".join(seen_ids[:_BATCH_GET_ID_MAX]), user_id_type="open_id")
        if detail.get("ok"):
            by_id = {u.get("open_id", ""): u for u in detail.get("users", []) if isinstance(u, dict)}
            for entry in found:
                extra = by_id.get(entry["user_id"])
                if extra:
                    entry["name"] = extra.get("name", "")
                    entry["job_title"] = extra.get("job_title", "")
                    entry["department_ids"] = extra.get("department_ids", [])

    not_found = [v for v in (*mobile_list, *email_list) if v not in matched_values]
    result: dict[str, Any] = {
        "ok": True,
        "user_id_type": user_id_type,
        "users": found,
        "count": len(found),
        "not_found": not_found,
        "include_resigned": include_resigned,
    }
    if not_found:
        result["not_found_note"] = (
            "查不到的常见原因: 号码/邮箱本身不存在; 用了**企业邮箱**(该接口只认个人邮箱, "
            "企业邮箱一律查不到); 非中国大陆手机号没带 + 国家码; 或该用户所在部门不在应用的"
            "通讯录权限范围内。"
        )
    return result


# ── Contact — 部门树 / 部门详情 ────────────────────────────────────────────────
#
# 已有的 _child_department_ids 只取 id 且**吞掉错误** (取不到就当没有子部门), 那对
# list_department_members 是对的 —— 少一层子部门只是少几个人。但画组织架构树时同一个
# 吞法会把「43010 部门过大」变成一棵看起来完整、实际缺一大块的树, 所以这里单独走一条
# 会把错误抛出来的遍历。
#
# 另外两点:
#  - 飞书自己的 fetch_child=true 一次就能递归, 但**上限 1000 个部门**且超了报 43010。
#    这里默认逐层查 (fetch_child=false) 并自己按 max_depth 控制, 于是「部门太多」表现为
#    截断 + truncated=true, 而不是整棵树查失败。
#  - member_count 含子部门人数, primary_member_count 只含主部门在此的人 —— 两个都回,
#    因为「这个部门多少人」这个问题两种答案都有人要。
_DEPT_TREE_MAX_DEPTH = 10
_DEPT_PAGE_SIZE = 50


def _department_record(it: dict[str, Any], department_id_type: str) -> dict[str, Any]:
    """把飞书的部门对象收成稳定形状 (含主/副负责人拆分)。"""
    leaders = it.get("leaders", []) if isinstance(it.get("leaders"), list) else []
    primary = [lead.get("leaderID", "") for lead in leaders if isinstance(lead, dict) and lead.get("leaderType") == 1]
    deputy = [lead.get("leaderID", "") for lead in leaders if isinstance(lead, dict) and lead.get("leaderType") == 2]
    status = it.get("status", {}) if isinstance(it.get("status"), dict) else {}
    did = it.get("department_id", "") if department_id_type == "department_id" else it.get("open_department_id", "")
    return {
        "department_id": did,
        "open_department_id": it.get("open_department_id", ""),
        "custom_department_id": it.get("department_id", ""),
        "name": it.get("name", ""),
        "parent_department_id": it.get("parent_department_id", ""),
        "leader_user_id": it.get("leader_user_id", ""),
        "primary_leader_ids": [x for x in primary if x],
        "deputy_leader_ids": [x for x in deputy if x],
        "department_hrbps": it.get("department_hrbps", []) if isinstance(it.get("department_hrbps"), list) else [],
        "chat_id": it.get("chat_id", ""),
        "order": it.get("order", ""),
        "member_count": it.get("member_count", 0),
        "primary_member_count": it.get("primary_member_count", 0),
        "is_deleted": bool(status.get("is_deleted")),
    }


async def _child_departments(
    department_id: str, department_id_type: str, user_id_type: str
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """一个部门的直接子部门 (分页取全)。返回 (子部门列表, 错误 or None)。

    与 ``_child_department_ids`` 的区别: 这里把错误**返回给调用方**而不是当作
    「没有子部门」—— 画树时静默少一层比报错更难发现。
    """
    out: list[dict[str, Any]] = []
    page_token = ""
    while True:
        req = _build_dept_children_request(department_id, department_id_type, _DEPT_PAGE_SIZE, page_token)
        req.add_query("user_id_type", user_id_type)
        res = await _core._invoke(req, prefer="tenant")
        if not res["ok"]:
            return out, _core._with_hint(res, _CONTACT_ADMIN_ERROR_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            if isinstance(it, dict):
                out.append(_department_record(it, department_id_type))
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return out, None


async def department_tree_impl(
    department_id: str = "0",
    department_id_type: str = "open_department_id",
    user_id_type: str = "open_id",
    max_depth: int = 2,
    include_member_count: bool = True,
) -> dict[str, Any]:
    """列出一个部门下的子部门 (可多层), 返回嵌套的组织架构树。

    department_id "0" 是组织根。max_depth=1 只列直接子部门。
    """
    if max_depth < 1 or max_depth > _DEPT_TREE_MAX_DEPTH:
        return _core._error(f"max_depth 必须在 1 到 {_DEPT_TREE_MAX_DEPTH} 之间, 当前 {max_depth}。")

    total = 0
    truncated = False
    visited: set[str] = set()

    async def walk(did: str, depth: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        nonlocal total, truncated
        children, err = await _child_departments(did, department_id_type, user_id_type)
        if err is not None:
            return [], err
        nodes: list[dict[str, Any]] = []
        for child in children:
            cid = child.get("department_id", "")
            # 环形父子关系理论上不该出现, 但真出现就会无限递归。
            if cid and cid in visited:
                continue
            if cid:
                visited.add(cid)
            total += 1
            node = dict(child)
            if not include_member_count:
                node.pop("member_count", None)
                node.pop("primary_member_count", None)
            if depth < max_depth and cid:
                sub, sub_err = await walk(cid, depth + 1)
                if sub_err is not None:
                    return [], sub_err
                if sub:
                    node["children"] = sub
            elif depth >= max_depth and cid:
                # 到深度上限就不再往下走, 但明确标出「这下面可能还有」。
                truncated = True
            nodes.append(node)
        return nodes, None

    tree, error = await walk(department_id, 1)
    if error is not None:
        return error
    result: dict[str, Any] = {
        "ok": True,
        "root_department_id": department_id,
        "department_id_type": department_id_type,
        "max_depth": max_depth,
        "departments": tree,
        "count": total,
    }
    if truncated:
        result["truncated"] = True
        result["truncated_note"] = f"已到 max_depth={max_depth}, 更深的子部门未展开; 需要更深就调大 max_depth。"
    if not tree:
        result["note"] = (
            "没有子部门。若确信应该有, 检查应用的「通讯录权限范围」—— 用 tenant token 查根部门 "
            "('0') 的子部门要求范围设为「全部成员」, 否则会返回空而不是报错。"
        )
    return result


def _build_department_get_request(department_id: str, department_id_type: str, user_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/departments/:department_id"
    req.paths["department_id"] = department_id
    req.add_query("department_id_type", department_id_type)
    req.add_query("user_id_type", user_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_department_parent_request(
    department_id: str, department_id_type: str, user_id_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/departments/parent"
    req.add_query("department_id", department_id)
    req.add_query("department_id_type", department_id_type)
    req.add_query("user_id_type", user_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def department_get_impl(
    department_id: str,
    department_id_type: str = "open_department_id",
    user_id_type: str = "open_id",
    include_children: bool = True,
    include_path: bool = True,
    user_key: str = "",
) -> dict[str, Any]:
    """一个部门的详细信息, 可带直接子部门和从根到它的路径。

    路径 (ancestors) 走 departments/parent, 飞书返回顺序是子->父, 这里翻成根->子
    再拼出 "公司/一级部门/二级部门" 的 path_text, 因为「这个部门在组织架构哪儿」
    是问部门详情时真正想知道的事。
    """
    did = (department_id or "").strip()
    if not did:
        return _core._error("department_id 是必填的 ('0' 表示组织根); 用 feishu_department_tree 找部门 id。")
    if did == "0":
        return _core._error(
            "根部门 ('0') 没有详情可查 (飞书返回 40002)。要看组织架构从根往下列, "
            "用 feishu_department_tree(department_id='0')。"
        )

    res = await _core._invoke(
        _build_department_get_request(did, department_id_type, user_id_type),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _core._with_hint({**res, "department_id": did}, _CONTACT_ADMIN_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    raw = data.get("department") if isinstance(data.get("department"), dict) else data
    department = _department_record(raw if isinstance(raw, dict) else {}, department_id_type)

    result: dict[str, Any] = {"ok": True, "department": department}

    if include_path:
        ancestors: list[dict[str, Any]] = []
        page_token = ""
        while True:
            pres = await _core._invoke(
                _build_department_parent_request(did, department_id_type, user_id_type, 50, page_token),
                user_key=user_key,
                prefer="tenant",
            )
            if not pres["ok"]:
                # 拿不到路径不该让整个详情查询失败 —— 主体信息已经有了。
                result["path_error"] = _core._with_hint(pres, _CONTACT_ADMIN_ERROR_HINTS).get("message", "")
                break
            pdata = pres["data"] if isinstance(pres["data"], dict) else {}
            for it in pdata.get("items", []) if isinstance(pdata.get("items"), list) else []:
                if isinstance(it, dict):
                    ancestors.append(_department_record(it, department_id_type))
            page_token = pdata.get("page_token", "") or ""
            if not pdata.get("has_more") or not page_token:
                break
        if ancestors or "path_error" not in result:
            # 飞书按子->父返回且不含根部门; 翻转成根->子更像「路径」。
            ordered = list(reversed(ancestors))
            result["ancestors"] = ordered
            names = [a.get("name", "") for a in ordered if a.get("name")]
            result["path_text"] = "/".join([*names, department.get("name", "")])

    if include_children:
        children, err = await _child_departments(did, department_id_type, user_id_type)
        if err is not None:
            result["children_error"] = err.get("message", "")
        else:
            result["children"] = children
            result["children_count"] = len(children)

    return result


def _build_group_member_request(group_id: str, action: str, body: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = f"/open-apis/contact/v3/group/:group_id/member/{action}"
    req.paths["group_id"] = group_id
    req.body = body
    req.token_types = {AccessTokenType.TENANT}
    return req


def _build_group_member_list_request(
    group_id: str, member_type: str, member_id_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/group/:group_id/member/simplelist"
    req.paths["group_id"] = group_id
    req.add_query("member_type", member_type)
    req.add_query("member_id_type", member_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT}
    return req


async def user_group_members_impl(
    group_id: str,
    action: str = "list",
    user_ids: str = "",
    member_id_type: str = "open_id",
    member_type: str = "user",
    page_size: int = 50,
    page_token: str = "",
) -> dict[str, Any]:
    """用户组成员: action = list | add | remove。

    add/remove 逐个调用飞书的单成员接口 (它一次只收一个), 并把每个人的结果分开回报,
    这样 10 个人里 1 个失败不会让另外 9 个的结果无从判断。
    """
    gid = (group_id or "").strip()
    if not gid:
        return _core._error("group_id 是必填的; 用 GET /open-apis/contact/v3/group/simplelist 取。")
    act = (action or "").strip().lower()
    if act not in {"list", "add", "remove"}:
        return _core._error(f"action 只能是 list, add, remove (当前 '{action}')。")

    if act == "list":
        if member_type not in {"user", "department"}:
            return _core._error("member_type 只能是 'user' 或 'department'。")
        if page_size < 1 or page_size > 100:
            return _core._error("page_size 必须在 1 到 100 之间。")
        res = await _core._invoke(
            _build_group_member_list_request(gid, member_type, member_id_type, page_size, page_token),
            prefer="tenant",
        )
        if not res["ok"]:
            return _core._with_hint({**res, "group_id": gid}, _CONTACT_ADMIN_ERROR_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        raw_list = data.get("memberlist", []) if isinstance(data.get("memberlist"), list) else []
        members = [
            {"member_id": m.get("member_id", ""), "member_type": m.get("member_type", "")}
            for m in raw_list
            if isinstance(m, dict)
        ]
        return {
            "ok": True,
            "group_id": gid,
            "member_type": member_type,
            "members": members,
            "count": len(members),
            "has_more": bool(data.get("has_more")),
            "page_token": data.get("page_token", ""),
            "note": "一次只返回一类成员; 要看部门成员再用 member_type='department' 调一次。",
        }

    # add / remove —— 飞书只支持 user 类型成员, 且一次一个。
    if member_type != "user":
        return _core._error("增删用户组成员目前只支持 member_type='user' (飞书暂不支持部门类型)。")
    ids = _split_contacts(user_ids)
    if not ids:
        return _core._error("user_ids 是必填的 (逗号分隔)。")

    endpoint = "add" if act == "add" else "remove"
    succeeded: list[str] = []
    failed: list[dict[str, Any]] = []
    for uid in ids:
        body = {"member_type": "user", "member_id_type": member_id_type, "member_id": uid}
        res = await _core._invoke(_build_group_member_request(gid, endpoint, body), prefer="tenant")
        if res["ok"]:
            succeeded.append(uid)
        else:
            hinted = _core._with_hint(res, _CONTACT_ADMIN_ERROR_HINTS)
            # hint 才是「这人为什么加不进去」那句话 (42005 已在组内 / 42006 已离职),
            # 只留 message 会退化成一句看不出所以然的 "Feishu API error 42006"。
            failed.append(
                {
                    "member_id": uid,
                    "code": res.get("code"),
                    "message": hinted.get("hint") or hinted.get("message", ""),
                }
            )

    result: dict[str, Any] = {
        "ok": not failed,
        "group_id": gid,
        "action": act,
        "member_id_type": member_id_type,
        "succeeded": succeeded,
        "succeeded_count": len(succeeded),
        "failed": failed,
    }
    if failed and succeeded:
        result["partial"] = True
    if failed:
        result["message"] = f"{len(succeeded)} 个成功, {len(failed)} 个失败; 看 failed 里每个人的原因。"
    return result
