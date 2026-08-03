"""Tests for 通讯录管理: 手机/邮箱查人 / 部门树 / 部门详情 / 用户写 / 部门写 / 用户组.

These wrap one or two endpoints each, so what needs covering is not "does it reach
Feishu" but the specific places where the *obvious* request or the *obvious* default is
the wrong one:

- ``batch_get_id`` is a POST with the lookup keys in the body, and Feishu's own
  ``include_resigned`` default silently drops departed employees — making "已离职" look
  identical to "查无此人";
- a department tree has to surface 43010 rather than quietly returning a tree that is
  missing a branch, which is what happens if child-fetch errors are swallowed;
- PATCH means "unset fields keep their value", so an empty string must never reach the
  body — sending one *clears* the field;
- resigning a user and deleting a department/user group are irreversible, so they must
  not be reachable by accident;
- user-group membership takes one member per call, so a partial failure has to be
  reported per person instead of as one opaque error.

Assertions land on the outgoing ``BaseRequest`` (method / uri / paths / queries / body),
never on intent — a tool that builds the wrong request while reporting success is
exactly the failure these guard against.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Any

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_feishu_impl")


def _qdict(req: Any) -> dict[str, str]:
    """SDK stores queries as list[tuple[str, str]] with str-coerced values."""
    return dict(req.queries)


def _qlist(req: Any, key: str) -> list[str]:
    """All values for a repeated query key (``user_ids`` is passed once per id)."""
    return [v for k, v in req.queries if k == key]


class _Captured:
    """Replace ``_invoke``; record the request and return a canned success payload."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.request: Any = None
        self.kwargs: dict[str, Any] = {}
        self._data = data or {}

    async def __call__(self, request: Any, **kwargs: Any) -> dict[str, Any]:
        self.request = request() if callable(request) else request
        self.kwargs = kwargs
        return {"ok": True, "code": 0, "msg": "", "data": self._data}


class _Sequenced:
    """Answer successive ``_invoke`` calls from a queue, recording every request.

    The tree walk and the member loops deliberately make more than one call, so
    asserting on them needs the *order* of requests rather than just the last one.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.requests: list[Any] = []

    async def __call__(self, request: Any, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(request() if callable(request) else request)
        if not self.responses:
            raise AssertionError(f"unexpected extra _invoke call #{len(self.requests)}")
        return {"ok": True, "code": 0, "msg": "", "data": {}, **self.responses.pop(0)}


class _Failing:
    """Replace ``_invoke`` with a fixed Feishu error, to check hint translation."""

    def __init__(self, code: int, msg: str = "boom") -> None:
        self._code = code
        self._msg = msg
        self.calls = 0

    async def __call__(self, request: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return {
            "ok": False,
            "code": self._code,
            "msg": self._msg,
            "data": {},
            "message": f"Feishu API error {self._code}: {self._msg}",
        }


# ── 按手机号/邮箱查人 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_contact_find_posts_lookup_keys_in_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is a POST with emails/mobiles in the BODY — writing it as GET query params
    (the intuitive shape for a lookup) returns nothing useful."""
    cap = _Captured({"user_list": []})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.find_users_by_contact_impl(mobiles="13011111111", emails="a@b.com")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri.endswith("/contact/v3/users/batch_get_id")
    assert req.body["mobiles"] == ["13011111111"]
    assert req.body["emails"] == ["a@b.com"]
    assert _qdict(req).get("user_id_type") == "open_id"


@pytest.mark.asyncio
async def test_contact_find_includes_resigned_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feishu defaults include_resigned to false, which drops departed employees
    silently — so "已离职" and "查无此人" become indistinguishable. Default to True."""
    cap = _Captured({"user_list": []})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.find_users_by_contact_impl(mobiles="13011111111")
    assert cap.request.body["include_resigned"] is True


@pytest.mark.asyncio
async def test_contact_find_can_exclude_resigned(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured({"user_list": []})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.find_users_by_contact_impl(mobiles="13011111111", include_resigned=False)
    assert cap.request.body["include_resigned"] is False


@pytest.mark.asyncio
async def test_contact_find_omits_empty_lookup_arrays(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the array actually being searched is sent; an empty ``emails: []`` would
    make the response's echoed-key logic ambiguous."""
    cap = _Captured({"user_list": []})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.find_users_by_contact_impl(mobiles="13011111111")
    assert "emails" not in cap.request.body
    assert "mobiles" in cap.request.body


@pytest.mark.asyncio
async def test_contact_find_reports_not_found_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feishu returns an entry with no user_id for a miss; that must surface as
    not_found rather than as a user with an empty id."""

    async def fake(request: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "code": 0,
            "msg": "",
            "data": {
                "user_list": [
                    {"user_id": "ou_1", "mobile": "13011111111"},
                    {"mobile": "13099999999"},  # 命中不了的那个: 没有 user_id
                ]
            },
        }

    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.find_users_by_contact_impl(mobiles="13011111111,13099999999")
    assert result["count"] == 1
    assert result["users"][0]["user_id"] == "ou_1"
    assert result["users"][0]["matched_by"] == "mobile"
    assert result["not_found"] == ["13099999999"]
    assert "企业邮箱" in result["not_found_note"]


@pytest.mark.asyncio
async def test_contact_find_surfaces_resigned_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(request: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "code": 0,
            "msg": "",
            "data": {
                "user_list": [
                    {"user_id": "ou_1", "email": "gone@b.com", "status": {"is_resigned": True, "is_activated": False}}
                ]
            },
        }

    monkeypatch.setattr(_impl, "_invoke", fake)
    result = await _impl.find_users_by_contact_impl(emails="gone@b.com")
    assert result["users"][0]["is_resigned"] is True
    assert result["users"][0]["matched_by"] == "email"


@pytest.mark.asyncio
async def test_contact_find_enriches_with_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """batch_get_id returns ids but no names, so a second batch call fills them in —
    otherwise the caller holds an ou_ id and still doesn't know who it is."""

    async def fake_invoke(request: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "code": 0, "msg": "", "data": {"user_list": [{"user_id": "ou_1", "mobile": "130"}]}}

    async def fake_batch(user_ids: str, user_id_type: str = "open_id", **kwargs: Any) -> dict[str, Any]:
        assert user_ids == "ou_1"
        return {"ok": True, "users": [{"open_id": "ou_1", "name": "张三", "job_title": "工程师"}]}

    monkeypatch.setattr(_impl, "_invoke", fake_invoke)
    monkeypatch.setattr(_impl, "get_users_batch_impl", fake_batch)
    result = await _impl.find_users_by_contact_impl(mobiles="130")
    assert result["users"][0]["name"] == "张三"
    assert result["users"][0]["job_title"] == "工程师"


@pytest.mark.asyncio
async def test_contact_find_survives_name_lookup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """The id->name enrichment is a convenience; losing it must not fail the lookup,
    whose actual job (contact -> id) already succeeded."""

    async def fake_invoke(request: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "code": 0, "msg": "", "data": {"user_list": [{"user_id": "ou_1", "mobile": "130"}]}}

    async def failing_batch(user_ids: str, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "message": "no scope"}

    monkeypatch.setattr(_impl, "_invoke", fake_invoke)
    monkeypatch.setattr(_impl, "get_users_batch_impl", failing_batch)
    result = await _impl.find_users_by_contact_impl(mobiles="130")
    assert result["ok"] is True
    assert result["users"][0]["user_id"] == "ou_1"
    assert "name" not in result["users"][0]


@pytest.mark.asyncio
async def test_contact_find_requires_a_lookup_key() -> None:
    result = await _impl.find_users_by_contact_impl()
    assert result["ok"] is False
    assert "mobiles" in result["message"]


@pytest.mark.asyncio
async def test_contact_find_rejects_over_50() -> None:
    result = await _impl.find_users_by_contact_impl(mobiles=",".join(f"1301111{i:04d}" for i in range(51)))
    assert result["ok"] is False
    assert "50" in result["message"]


@pytest.mark.asyncio
async def test_contact_find_dedupes_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured({"user_list": []})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.find_users_by_contact_impl(mobiles="130, 130 ,131")
    assert cap.request.body["mobiles"] == ["130", "131"]


@pytest.mark.asyncio
async def test_contact_find_translates_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """40004 is about the app's 通讯录权限范围, which no code change can fix — the hint
    has to say where to go, not just repeat "no permission"."""
    monkeypatch.setattr(_impl, "_invoke", _Failing(40004))
    result = await _impl.find_users_by_contact_impl(mobiles="130")
    assert result["ok"] is False
    assert "通讯录权限范围" in result["hint"]


# ── 部门树 ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_department_tree_builds_children_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([{"data": {"items": [], "has_more": False}}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.department_tree_impl("0", max_depth=1)
    req = seq.requests[0]
    assert req.http_method.name == "GET"
    assert req.uri.endswith("/contact/v3/departments/:department_id/children")
    assert req.paths["department_id"] == "0"
    assert _qdict(req).get("page_size") == "50"
    assert result["count"] == 0
    # 空结果最常见的真因是范围没设成全部成员, 且飞书不报错 —— 必须点出来。
    assert "全部成员" in result["note"]


@pytest.mark.asyncio
async def test_department_tree_nests_children(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced(
        [
            {"data": {"items": [{"open_department_id": "od-a", "name": "研发"}], "has_more": False}},
            {"data": {"items": [{"open_department_id": "od-b", "name": "平台组"}], "has_more": False}},
            {"data": {"items": [], "has_more": False}},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.department_tree_impl("0", max_depth=2)
    assert result["count"] == 2
    top = result["departments"][0]
    assert top["name"] == "研发"
    assert top["children"][0]["name"] == "平台组"


@pytest.mark.asyncio
async def test_department_tree_respects_max_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    """At the depth cap it stops walking but says so — a silently shallow tree reads as
    a complete one."""
    seq = _Sequenced([{"data": {"items": [{"open_department_id": "od-a", "name": "研发"}], "has_more": False}}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.department_tree_impl("0", max_depth=1)
    assert len(seq.requests) == 1  # 没有去查 od-a 的子部门
    assert result["truncated"] is True
    assert "max_depth" in result["truncated_note"]


@pytest.mark.asyncio
async def test_department_tree_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced(
        [
            {"data": {"items": [{"open_department_id": "od-a", "name": "A"}], "has_more": True, "page_token": "pt2"}},
            {"data": {"items": [{"open_department_id": "od-b", "name": "B"}], "has_more": False}},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.department_tree_impl("0", max_depth=1)
    assert _qdict(seq.requests[1]).get("page_token") == "pt2"
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_department_tree_surfaces_43010(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole reason this doesn't reuse _child_department_ids: that helper swallows
    errors, which would turn "部门过大" into a tree quietly missing a branch."""
    monkeypatch.setattr(_impl, "_invoke", _Failing(43010))
    result = await _impl.department_tree_impl("0")
    assert result["ok"] is False
    assert "递归" in result["hint"]


@pytest.mark.asyncio
async def test_department_tree_rejects_bad_depth() -> None:
    for depth in (0, 11):
        result = await _impl.department_tree_impl("0", max_depth=depth)
        assert result["ok"] is False
        assert "max_depth" in result["message"]


@pytest.mark.asyncio
async def test_department_tree_splits_leaders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feishu returns 主/副负责人 mixed in one ``leaders`` array keyed by leaderType;
    callers asking "who runs this" want the primary, not a typed tuple to decode."""
    seq = _Sequenced(
        [
            {
                "data": {
                    "items": [
                        {
                            "open_department_id": "od-a",
                            "name": "研发",
                            "leaders": [
                                {"leaderType": 1, "leaderID": "ou_boss"},
                                {"leaderType": 2, "leaderID": "ou_deputy"},
                            ],
                        }
                    ],
                    "has_more": False,
                }
            }
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.department_tree_impl("0", max_depth=1)
    dept = result["departments"][0]
    assert dept["primary_leader_ids"] == ["ou_boss"]
    assert dept["deputy_leader_ids"] == ["ou_deputy"]


@pytest.mark.asyncio
async def test_department_tree_can_drop_member_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced(
        [{"data": {"items": [{"open_department_id": "od-a", "name": "A", "member_count": 9}], "has_more": False}}]
    )
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.department_tree_impl("0", max_depth=1, include_member_count=False)
    assert "member_count" not in result["departments"][0]


# ── 部门详情 ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_department_get_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured({"department": {"open_department_id": "od-a", "name": "研发", "member_count": 12}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.department_get_impl("od-a", include_children=False, include_path=False)
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri.endswith("/contact/v3/departments/:department_id")
    assert req.paths["department_id"] == "od-a"
    assert result["department"]["name"] == "研发"
    assert result["department"]["member_count"] == 12


@pytest.mark.asyncio
async def test_department_get_rejects_root() -> None:
    """Feishu answers 40002 for the root; saying so up front beats an opaque error."""
    result = await _impl.department_get_impl("0")
    assert result["ok"] is False
    assert "feishu_department_tree" in result["message"]


@pytest.mark.asyncio
async def test_department_get_requires_id() -> None:
    result = await _impl.department_get_impl("   ")
    assert result["ok"] is False
    assert "department_id" in result["message"]


@pytest.mark.asyncio
async def test_department_get_builds_path_root_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """departments/parent returns child->parent order; a path reads root->child."""
    seq = _Sequenced(
        [
            {"data": {"department": {"open_department_id": "od-c", "name": "平台组"}}},
            {
                "data": {
                    "items": [
                        {"open_department_id": "od-b", "name": "研发中心"},
                        {"open_department_id": "od-a", "name": "公司"},
                    ],
                    "has_more": False,
                }
            },
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.department_get_impl("od-c", include_children=False)
    assert seq.requests[1].uri.endswith("/contact/v3/departments/parent")
    assert _qdict(seq.requests[1]).get("department_id") == "od-c"
    assert [a["name"] for a in result["ancestors"]] == ["公司", "研发中心"]
    assert result["path_text"] == "公司/研发中心/平台组"


@pytest.mark.asyncio
async def test_department_get_keeps_detail_when_path_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Losing the ancestor chain must not discard the detail already fetched."""
    calls = {"n": 0}

    async def flaky(request: Any, **kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": True, "code": 0, "msg": "", "data": {"department": {"name": "研发"}}}
        return {"ok": False, "code": 40004, "msg": "no", "data": {}, "message": "Feishu API error 40004: no"}

    monkeypatch.setattr(_impl, "_invoke", flaky)
    result = await _impl.department_get_impl("od-a", include_children=False)
    assert result["ok"] is True
    assert result["department"]["name"] == "研发"
    assert "path_error" in result


@pytest.mark.asyncio
async def test_department_get_includes_children(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced(
        [
            {"data": {"department": {"open_department_id": "od-a", "name": "研发"}}},
            {"data": {"items": [{"open_department_id": "od-b", "name": "平台组"}], "has_more": False}},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.department_get_impl("od-a", include_path=False)
    assert result["children_count"] == 1
    assert result["children"][0]["name"] == "平台组"


# ── 用户写操作 ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_create_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured({"user": {"open_id": "ou_new", "name": "张三"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.user_create_impl("张三", "13011111111", "od-a,od-b", 1, email="z@b.com")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri.endswith("/contact/v3/users")
    assert req.body["name"] == "张三"
    assert req.body["mobile"] == "13011111111"
    assert req.body["department_ids"] == ["od-a", "od-b"]
    assert req.body["employee_type"] == 1
    assert req.body["email"] == "z@b.com"
    assert result["created"] is True
    assert result["user"]["open_id"] == "ou_new"


@pytest.mark.asyncio
async def test_user_create_uses_tenant_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contact writes are tenant-token-only, so they must not go down the prefer="user"
    path — that asks "who should own this", a question a directory entry cannot answer."""
    cap = _Captured({"user": {}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.user_create_impl("张三", "130", "od-a")
    assert cap.kwargs.get("prefer") == "tenant"


@pytest.mark.asyncio
async def test_user_create_validates_required_fields() -> None:
    assert (await _impl.user_create_impl("", "130", "od-a"))["ok"] is False
    assert (await _impl.user_create_impl("张三", "", "od-a"))["ok"] is False
    missing_dept = await _impl.user_create_impl("张三", "130", "  ")
    assert missing_dept["ok"] is False
    assert "department_ids" in missing_dept["message"]


@pytest.mark.asyncio
async def test_user_create_rejects_bad_employee_type() -> None:
    result = await _impl.user_create_impl("张三", "130", "od-a", employee_type=99)
    assert result["ok"] is False
    assert "1=正式" in result["message"]


@pytest.mark.asyncio
async def test_user_create_rejects_too_many_departments() -> None:
    result = await _impl.user_create_impl("张三", "130", ",".join(f"od-{i}" for i in range(51)))
    assert result["ok"] is False
    assert "50" in result["message"]


@pytest.mark.asyncio
async def test_user_create_translates_duplicate_mobile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _Failing(41001))
    result = await _impl.user_create_impl("张三", "130", "od-a")
    assert result["ok"] is False
    assert "手机号已被" in result["hint"]


@pytest.mark.asyncio
async def test_user_update_is_patch_and_omits_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """PATCH means "unset fields keep their value" — an empty string must never reach
    the body, because Feishu would take it as "clear this field"."""
    cap = _Captured({"user": {"open_id": "ou_1"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.user_update_impl("ou_1", job_title="架构师")
    req = cap.request
    assert req.http_method.name == "PATCH"
    assert req.paths["user_id"] == "ou_1"
    assert req.body == {"job_title": "架构师"}
    assert "name" not in req.body
    assert "mobile" not in req.body
    assert result["updated_fields"] == ["job_title"]


@pytest.mark.asyncio
async def test_user_update_requires_a_field() -> None:
    result = await _impl.user_update_impl("ou_1")
    assert result["ok"] is False
    assert "整体替换" in result["message"]


@pytest.mark.asyncio
async def test_user_update_requires_user_id() -> None:
    result = await _impl.user_update_impl("", name="张三")
    assert result["ok"] is False
    assert "user_id" in result["message"]


@pytest.mark.asyncio
async def test_user_resign_requires_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offboarding is irreversible, so a vague instruction must not reach Feishu."""
    cap = _Captured()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.user_resign_impl("ou_1")
    assert result["ok"] is False
    assert result["need_confirmation"] is True
    assert "离职用户" in result["message"]
    assert cap.request is None  # 关键: 什么都没发出去


@pytest.mark.asyncio
async def test_user_resign_warns_about_deleted_calendars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a manager, Feishu *deletes* calendars and surveys rather than keeping
    them — the confirm message has to say so, since it is not what "转交" implies."""
    monkeypatch.setattr(_impl, "_invoke", _Captured())
    result = await _impl.user_resign_impl("ou_1")
    assert "日历和问卷会被直接删除" in result["message"]


@pytest.mark.asyncio
async def test_user_resign_builds_delete_with_acceptors(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.user_resign_impl(
        "ou_1",
        confirm="离职用户",
        docs_acceptor_user_id="ou_boss",
        calendar_acceptor_user_id="ou_boss",
    )
    req = cap.request
    assert req.http_method.name == "DELETE"
    assert req.uri.endswith("/contact/v3/users/:user_id")
    assert req.paths["user_id"] == "ou_1"
    assert req.body["docs_acceptor_user_id"] == "ou_boss"
    assert req.body["calendar_acceptor_user_id"] == "ou_boss"
    assert result["resigned"] is True


@pytest.mark.asyncio
async def test_user_resign_email_acceptor_requires_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _Captured())
    result = await _impl.user_resign_impl("ou_1", confirm="离职用户", email_processing_type="1")
    assert result["ok"] is False
    assert "email_acceptor_user_id" in result["message"]


@pytest.mark.asyncio
async def test_user_resign_email_acceptor_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured()
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.user_resign_impl(
        "ou_1", confirm="离职用户", email_processing_type="1", email_acceptor_user_id="ou_boss"
    )
    assert cap.request.body["email_acceptor"] == {"processing_type": "1", "acceptor_user_id": "ou_boss"}


@pytest.mark.asyncio
async def test_user_resign_rejects_bad_email_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _Captured())
    result = await _impl.user_resign_impl("ou_1", confirm="离职用户", email_processing_type="9")
    assert result["ok"] is False
    assert "'1'" in result["message"]


@pytest.mark.asyncio
async def test_user_resign_translates_admin_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _Failing(44037))
    result = await _impl.user_resign_impl("ou_1", confirm="离职用户")
    assert result["ok"] is False
    assert "管理员" in result["hint"]


# ── 部门写操作 ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_department_create_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured({"department": {"open_department_id": "od-new", "name": "平台组"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.department_create_impl("平台组", "od-a", leader_user_id="ou_boss")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri.endswith("/contact/v3/departments")
    assert req.body["name"] == "平台组"
    assert req.body["parent_department_id"] == "od-a"
    assert req.body["leader_user_id"] == "ou_boss"
    assert result["created"] is True


@pytest.mark.asyncio
async def test_department_create_rejects_slash_in_name() -> None:
    result = await _impl.department_create_impl("研发/平台", "od-a")
    assert result["ok"] is False
    assert "43029" in result["message"]


@pytest.mark.asyncio
async def test_department_create_requires_parent() -> None:
    result = await _impl.department_create_impl("平台组", "")
    assert result["ok"] is False
    assert "parent_department_id" in result["message"]


@pytest.mark.asyncio
async def test_department_create_rejects_reserved_custom_id() -> None:
    """Feishu reserves the od- prefix for its own ids, plus "0"/"1"."""
    for bad in ("od-mine", "0", "1"):
        result = await _impl.department_create_impl("平台组", "od-a", custom_department_id=bad)
        assert result["ok"] is False
        assert "od-" in result["message"]


@pytest.mark.asyncio
async def test_department_update_moves_department(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured({"department": {"open_department_id": "od-b"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.department_update_impl("od-b", parent_department_id="0")
    req = cap.request
    assert req.http_method.name == "PATCH"
    assert req.paths["department_id"] == "od-b"
    assert req.body == {"parent_department_id": "0"}
    assert result["updated_fields"] == ["parent_department_id"]


@pytest.mark.asyncio
async def test_department_update_rejects_root_and_empty() -> None:
    assert (await _impl.department_update_impl("0", name="x"))["ok"] is False
    empty = await _impl.department_update_impl("od-a")
    assert empty["ok"] is False
    assert "至少给一个" in empty["message"]


@pytest.mark.asyncio
async def test_department_delete_requires_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.department_delete_impl("od-a")
    assert result["ok"] is False
    assert result["need_confirmation"] is True
    assert "43011" in result["message"]  # 必须先清空这件事要提前说
    assert cap.request is None


@pytest.mark.asyncio
async def test_department_delete_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.department_delete_impl("od-a", confirm="删除部门")
    req = cap.request
    assert req.http_method.name == "DELETE"
    assert req.paths["department_id"] == "od-a"
    assert result["deleted"] is True


@pytest.mark.asyncio
async def test_department_delete_rejects_root() -> None:
    result = await _impl.department_delete_impl("0", confirm="删除部门")
    assert result["ok"] is False
    assert "40002" in result["message"]


@pytest.mark.asyncio
async def test_department_delete_translates_not_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _Failing(43012))
    result = await _impl.department_delete_impl("od-a", confirm="删除部门")
    assert result["ok"] is False
    assert "最深一层" in result["hint"]


# ── 用户组 ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_group_create_uses_singular_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The path is /contact/v3/group — singular, with no /groups variant. Guessing the
    plural (as every other Feishu resource uses) 404s."""
    cap = _Captured({"group_id": "g_new"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.user_group_manage_impl("create", name="IT 外包组", description="外包人员")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri.endswith("/contact/v3/group")
    assert req.body["name"] == "IT 外包组"
    assert req.body["description"] == "外包人员"
    assert req.body["type"] == 1
    assert result["group_id"] == "g_new"
    assert "动态用户组" in result["note"]


@pytest.mark.asyncio
async def test_user_group_create_requires_name() -> None:
    result = await _impl.user_group_manage_impl("create")
    assert result["ok"] is False
    assert "name" in result["message"]


@pytest.mark.asyncio
async def test_user_group_create_translates_scope_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    """42010 only happens on create, and only because the app's contact scope isn't
    全部成员 — a console setting, so the hint must point there."""
    monkeypatch.setattr(_impl, "_invoke", _Failing(42010))
    result = await _impl.user_group_manage_impl("create", name="x")
    assert result["ok"] is False
    assert "全部成员" in result["hint"]


@pytest.mark.asyncio
async def test_user_group_list_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured(
        {
            "grouplist": [{"id": "g1", "name": "组一", "member_user_count": 3, "type": 1}],
            "has_more": False,
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.user_group_manage_impl("list")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri.endswith("/contact/v3/group/simplelist")
    assert _qdict(req).get("type") == "1"
    assert result["groups"][0]["group_id"] == "g1"
    assert result["groups"][0]["type_text"] == "普通用户组"


@pytest.mark.asyncio
async def test_user_group_list_rejects_bad_page_size() -> None:
    result = await _impl.user_group_manage_impl("list", page_size=500)
    assert result["ok"] is False
    assert "page_size" in result["message"]


@pytest.mark.asyncio
async def test_user_group_get_backfills_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The detail endpoint omits the id, so echo back the one the caller passed rather
    than returning a group whose group_id is empty."""
    cap = _Captured({"group": {"name": "组一", "member_user_count": 3, "type": 1}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.user_group_manage_impl("get", group_id="g1")
    assert cap.request.paths["group_id"] == "g1"
    assert result["group"]["group_id"] == "g1"
    assert result["group"]["name"] == "组一"


@pytest.mark.asyncio
async def test_user_group_actions_need_group_id() -> None:
    for act in ("get", "update", "delete"):
        result = await _impl.user_group_manage_impl(act)
        assert result["ok"] is False
        assert "group_id" in result["message"]


@pytest.mark.asyncio
async def test_user_group_update_is_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.user_group_manage_impl("update", group_id="g1", name="新名字")
    assert cap.request.http_method.name == "PATCH"
    assert cap.request.body == {"name": "新名字"}
    assert result["updated_fields"] == ["name"]


@pytest.mark.asyncio
async def test_user_group_update_requires_a_field() -> None:
    result = await _impl.user_group_manage_impl("update", group_id="g1")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_user_group_delete_requires_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting a group silently strips it from every document ACL and approval flow
    that referenced it, so it must not be reachable by accident."""
    cap = _Captured()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.user_group_manage_impl("delete", group_id="g1")
    assert result["ok"] is False
    assert result["need_confirmation"] is True
    assert cap.request is None


@pytest.mark.asyncio
async def test_user_group_delete_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.user_group_manage_impl("delete", group_id="g1", confirm="删除用户组")
    assert cap.request.http_method.name == "DELETE"
    assert cap.request.paths["group_id"] == "g1"
    assert result["deleted"] is True


@pytest.mark.asyncio
async def test_user_group_rejects_unknown_action() -> None:
    result = await _impl.user_group_manage_impl("destroy")
    assert result["ok"] is False
    assert "action" in result["message"]


# ── 用户组成员 ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_group_members_list_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured({"memberlist": [{"member_id": "ou_1", "member_type": "user"}], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.user_group_members_impl("g1")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri.endswith("/contact/v3/group/:group_id/member/simplelist")
    assert req.paths["group_id"] == "g1"
    assert _qdict(req).get("member_type") == "user"
    assert result["count"] == 1
    # 一次只回一类成员, 不说清就会被当成「这个组没有部门成员」
    assert "一类成员" in result["note"]


@pytest.mark.asyncio
async def test_group_members_add_loops_one_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feishu's member endpoint takes exactly one member, so adding three people is
    three calls — and each needs its own outcome."""
    seq = _Sequenced([{}, {}, {}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.user_group_members_impl("g1", action="add", user_ids="ou_1,ou_2,ou_3")
    assert len(seq.requests) == 3
    first = seq.requests[0]
    assert first.http_method.name == "POST"
    assert first.uri.endswith("/contact/v3/group/:group_id/member/add")
    assert first.body == {"member_type": "user", "member_id_type": "open_id", "member_id": "ou_1"}
    assert result["ok"] is True
    assert result["succeeded_count"] == 3


@pytest.mark.asyncio
async def test_group_members_remove_uses_remove_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([{}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    await _impl.user_group_members_impl("g1", action="remove", user_ids="ou_1")
    assert seq.requests[0].uri.endswith("/contact/v3/group/:group_id/member/remove")


@pytest.mark.asyncio
async def test_group_members_reports_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """One unreachable id must not obscure the other two that worked."""
    calls = {"n": 0}

    async def flaky(request: Any, **kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 2:
            return {"ok": False, "code": 42006, "msg": "resigned", "data": {}, "message": "err"}
        return {"ok": True, "code": 0, "msg": "", "data": {}}

    monkeypatch.setattr(_impl, "_invoke", flaky)
    result = await _impl.user_group_members_impl("g1", action="add", user_ids="ou_1,ou_2,ou_3")
    assert result["ok"] is False
    assert result["partial"] is True
    assert result["succeeded"] == ["ou_1", "ou_3"]
    assert result["failed"][0]["member_id"] == "ou_2"
    assert "已离职" in result["failed"][0]["message"]


@pytest.mark.asyncio
async def test_group_members_add_rejects_department_type() -> None:
    result = await _impl.user_group_members_impl("g1", action="add", user_ids="od-a", member_type="department")
    assert result["ok"] is False
    assert "只支持" in result["message"]


@pytest.mark.asyncio
async def test_group_members_add_requires_ids() -> None:
    result = await _impl.user_group_members_impl("g1", action="add")
    assert result["ok"] is False
    assert "user_ids" in result["message"]


@pytest.mark.asyncio
async def test_group_members_requires_group_id() -> None:
    result = await _impl.user_group_members_impl("")
    assert result["ok"] is False
    assert "group_id" in result["message"]


@pytest.mark.asyncio
async def test_group_members_rejects_unknown_action() -> None:
    result = await _impl.user_group_members_impl("g1", action="purge")
    assert result["ok"] is False
    assert "action" in result["message"]


@pytest.mark.asyncio
async def test_group_members_list_can_ask_for_departments(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Captured({"memberlist": [], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.user_group_members_impl("g1", member_type="department", member_id_type="department_id")
    q = _qdict(cap.request)
    assert q.get("member_type") == "department"
    assert q.get("member_id_type") == "department_id"


# ── 工具层 ─────────────────────────────────────────────────────────────────────


def test_contact_tools_are_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_contact")
    for name in (
        "feishu_contact_find",
        "feishu_department_tree",
        "feishu_department_get",
        "feishu_user_manage",
        "feishu_department_manage",
        "feishu_user_group",
        "feishu_user_group_members",
    ):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), name


@pytest.mark.asyncio
async def test_user_manage_dispatches_by_action(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_contact")
    seen: list[str] = []

    async def fake_create(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append("create")
        return {"ok": True}

    async def fake_update(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append("update")
        return {"ok": True}

    async def fake_resign(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append("resign")
        return {"ok": True}

    monkeypatch.setattr(_impl, "user_create_impl", fake_create)
    monkeypatch.setattr(_impl, "user_update_impl", fake_update)
    monkeypatch.setattr(_impl, "user_resign_impl", fake_resign)
    await mod.feishu_user_manage("create", name="张三", mobile="130", department_ids="od-a")
    await mod.feishu_user_manage("update", user_id="ou_1", name="李四")
    await mod.feishu_user_manage("resign", user_id="ou_1", confirm="离职用户")
    assert seen == ["create", "update", "resign"]


@pytest.mark.asyncio
async def test_user_manage_rejects_unknown_action() -> None:
    mod = importlib.import_module("feishu_contact")
    out = await mod.feishu_user_manage("fire", user_id="ou_1")
    assert '"ok": false' in out.lower()
    assert "create" in out


@pytest.mark.asyncio
async def test_department_manage_dispatches_by_action(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_contact")
    seen: list[str] = []

    for impl_name, tag in (
        ("department_create_impl", "create"),
        ("department_update_impl", "update"),
        ("department_delete_impl", "delete"),
    ):

        async def fake(*args: Any, _tag: str = tag, **kwargs: Any) -> dict[str, Any]:
            seen.append(_tag)
            return {"ok": True}

        monkeypatch.setattr(_impl, impl_name, fake)

    await mod.feishu_department_manage("create", name="平台组", parent_department_id="od-a")
    await mod.feishu_department_manage("update", department_id="od-b", name="新名")
    await mod.feishu_department_manage("delete", department_id="od-b", confirm="删除部门")
    assert seen == ["create", "update", "delete"]


@pytest.mark.asyncio
async def test_department_manage_rejects_unknown_action() -> None:
    mod = importlib.import_module("feishu_contact")
    out = await mod.feishu_department_manage("merge", department_id="od-a")
    assert '"ok": false' in out.lower()


@pytest.mark.asyncio
async def test_tools_return_json_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every tool must hand back a JSON string, not a dict."""
    mod = importlib.import_module("feishu_contact")

    async def fake_find(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "users": [{"name": "张三"}]}

    monkeypatch.setattr(_impl, "find_users_by_contact_impl", fake_find)
    out = await mod.feishu_contact_find(mobiles="130")
    assert isinstance(out, str)
    assert "张三" in out  # ensure_ascii=False
    assert "\\u" not in out
