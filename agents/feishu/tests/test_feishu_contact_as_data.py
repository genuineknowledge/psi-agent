"""Parity: the ``feishu-contact`` skill reaches Feishu the same way the tools did.

The contact domain is the pilot for moving endpoint knowledge out of Python and into
a Markdown table. Migrating it is only defensible if the wire traffic is unchanged —
so each test here builds a request through the generic ``feishu_api`` path driven by
``skills/feishu-contact/SKILL.md``, builds the same call through the hand-written
``_build_*`` helper the dedicated tool uses, and compares what would actually be sent.

Comparing the outgoing ``BaseRequest`` rather than a parsed response is deliberate:
the ``_build_*`` helpers *are* the endpoint knowledge being replaced, so they are the
only honest reference for what "unchanged" means.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_spec: Any = importlib.import_module("_feishu_spec")
_api: Any = importlib.import_module("_feishu_api_impl")
_impl: Any = importlib.import_module("_feishu_impl")

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def _shape(req: BaseRequest) -> dict[str, Any]:
    """The part of a request that determines what Feishu receives."""
    return {
        "method": req.http_method,
        "uri": req.uri,
        "paths": dict(req.paths or {}),
        "queries": sorted((k, str(v)) for k, v in (req.queries or [])),
        "body": req.body or None,
        "tokens": set(req.token_types or set()),
    }


class _CapturedInvoke:
    """Stands in for ``_invoke`` and keeps the request instead of sending it."""

    def __init__(self, pages: list[dict[str, Any]] | None = None) -> None:
        self.requests: list[BaseRequest] = []
        self.kwargs: list[dict[str, Any]] = []
        self._pages = pages or [{"ok": True, "data": {}}]

    async def __call__(self, request: BaseRequest, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(request)
        self.kwargs.append(kwargs)
        return self._pages[min(len(self.requests) - 1, len(self._pages) - 1)]

    @property
    def request(self) -> BaseRequest:
        assert len(self.requests) == 1, f"expected 1 request, got {len(self.requests)}"
        return self.requests[0]


@pytest.fixture(autouse=True)
def _real_skills(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Drive the generic path from the shipped skill files, not a synthetic fixture."""
    _spec.reset_cache()
    monkeypatch.setattr(_api, "_skills_dir", lambda: str(SKILLS_DIR))
    yield
    _spec.reset_cache()


def _generic(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> _CapturedInvoke:
    cap = _CapturedInvoke(pages)
    monkeypatch.setattr(_impl, "_invoke", cap)
    anyio.run(lambda: _api.call_api_impl(**kwargs))
    return cap


def _confirm_codes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture the private messages the confirmation gate sends to the user."""
    sent: list[str] = []

    async def _send(receive_id: str, text: str, receive_id_type: str, on_behalf_of: str = "") -> dict[str, Any]:
        sent.append(text)
        return {"ok": True, "message_id": "om_1"}

    monkeypatch.setattr(_impl, "send_message_impl", _send)
    return sent


def _code_from(message: str) -> str:
    match = re.search(r"确认码: (\d{6})", message)
    assert match, f"no 6-digit code in the message: {message!r}"
    return match.group(1)


# ----------------------------------------------------------------- read endpoints


def test_find_by_department_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _generic(
        monkeypatch,
        pages=[{"ok": True, "data": {"items": [], "has_more": False}}],
        method="GET",
        uri="/open-apis/contact/v3/users/find_by_department",
        query_json=json.dumps(
            {
                "department_id": "od-x",
                "department_id_type": "open_department_id",
                "user_id_type": "open_id",
            }
        ),
    )
    reference = _impl._build_find_by_department_request("od-x", "open_department_id", "open_id", 50, "")
    assert _shape(cap.request) == _shape(reference)


def test_dept_children_matches_dedicated_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _generic(
        monkeypatch,
        pages=[{"ok": True, "data": {"items": [], "has_more": False}}],
        method="GET",
        uri="/open-apis/contact/v3/departments/:department_id/children",
        paths_json=json.dumps({"department_id": "0"}),
        query_json=json.dumps({"department_id_type": "open_department_id"}),
    )
    reference = _impl._build_dept_children_request("0", "open_department_id", 50, "")
    assert _shape(cap.request) == _shape(reference)


def test_page_size_default_comes_from_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dedicated tool hard-coded 50; the table now carries that number."""
    cap = _generic(
        monkeypatch,
        pages=[{"ok": True, "data": {"items": [], "has_more": False}}],
        method="GET",
        uri="/open-apis/contact/v3/users/find_by_department",
        query_json=json.dumps({"department_id": "od-x"}),
    )
    assert ("page_size", "50") in [(k, str(v)) for k, v in cap.request.queries]


def test_search_user_gets_a_user_token_without_being_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/search/v1/user`` rejects a tenant token. The old tool knew; the table now does.

    The table expresses that as the *strategy* (``token: user`` → ``prefer=user`` → the UAT
    send path), not by narrowing the declared candidates: a request narrowed to USER cannot
    be sent as tenant at all, and ``_invoke_write`` legitimately does that when no user is
    logged in. For a genuinely user-only endpoint the tenant attempt gets Feishu's own
    permission error, which names the missing scope.
    """
    cap = _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/search/v1/user",
        query_json=json.dumps({"query": "罗霖"}),
    )
    assert cap.kwargs[0]["prefer"] == "user"
    assert cap.request.token_types == {AccessTokenType.TENANT, AccessTokenType.USER}


def test_batch_get_id_body_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _generic(
        monkeypatch,
        method="POST",
        uri="/open-apis/contact/v3/users/batch_get_id",
        query_json=json.dumps({"user_id_type": "open_id"}),
        body_json=json.dumps({"mobiles": ["13800000000"]}),
    )
    assert cap.request.http_method == HttpMethod.POST
    assert cap.request.body == {"mobiles": ["13800000000"]}


def test_user_get_path_placeholder_is_substituted(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/contact/v3/users/:user_id",
        paths_json=json.dumps({"user_id": "ou_abc"}),
    )
    assert cap.request.uri == "/open-apis/contact/v3/users/:user_id"
    assert cap.request.paths == {"user_id": "ou_abc"}


# ------------------------------------------------------- constraints the tools held


def test_batch_over_fifty_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feishu caps ``users/batch`` at 50 ids. Exceeding it used to be a decoded 400."""
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    res = anyio.run(
        lambda: _api.call_api_impl(
            method="GET",
            uri="/open-apis/contact/v3/users/batch",
            query_json=json.dumps({"user_ids": [f"ou_{i}" for i in range(51)]}),
        )
    )
    assert res["ok"] is False
    assert res["code"] == "spec_violation"
    assert cap.requests == []


def test_batch_at_the_limit_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _generic(
        monkeypatch,
        method="GET",
        uri="/open-apis/contact/v3/users/batch",
        query_json=json.dumps({"user_ids": [f"ou_{i}" for i in range(50)]}),
    )
    assert len(cap.request.queries) >= 50


def test_find_by_department_requires_a_department(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omitting ``department_id`` returns an unhelpful 400 from Feishu; catch it here."""
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    res = anyio.run(lambda: _api.call_api_impl(method="GET", uri="/open-apis/contact/v3/users/find_by_department"))
    assert res["ok"] is False
    assert "department_id" in " ".join(res["violations"])
    assert cap.requests == []


def test_irreversible_delete_surfaces_its_warning() -> None:
    """The table must carry the confirm-first warning where the model will read it."""
    rule = _spec.rules_for(SKILLS_DIR, "DELETE", "/open-apis/contact/v3/users/ou_abc")
    assert rule is not None
    assert rule.pitfalls


# --------------------------------------------------- gates the deleted tools held
#
# The five dispatcher tools that this skill replaces are gone, so every safety
# property they enforced has to be reachable from the table instead. These are the
# ones whose loss would not have shown up as a failure — only as a destructive call
# that used to be stopped and now isn't.


@pytest.mark.parametrize(
    ("uri", "token"),
    [
        ("/open-apis/contact/v3/users/ou_abc", "离职用户"),
        ("/open-apis/contact/v3/departments/od-x", "删除部门"),
        ("/open-apis/contact/v3/group/g1", "删除用户组"),
    ],
)
def test_irreversible_delete_is_gated(monkeypatch: pytest.MonkeyPatch, uri: str, token: str) -> None:
    """Feishu accepts these on the first try and there is no undo, so the gate is the
    only thing that forces a real human decision.

    The code goes to the user out of band and is deliberately absent from the result:
    a token quoted back to the caller would be one the caller can quote onward.
    """
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    sent = _confirm_codes(monkeypatch)
    res = anyio.run(lambda: _api.call_api_impl(method="DELETE", uri=uri, user_key="ou_boss"))
    assert res["ok"] is False
    assert res["need_confirmation"] is True
    assert cap.requests == [], "an unconfirmed irreversible call must send nothing"
    assert len(sent) == 1
    assert _code_from(sent[0]) not in json.dumps(res, ensure_ascii=False)


def test_confirmed_delete_goes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _confirm_codes(monkeypatch)
    args: dict[str, Any] = {
        "method": "DELETE",
        "uri": "/open-apis/contact/v3/departments/:department_id",
        "paths_json": json.dumps({"department_id": "od-x"}),
        "user_key": "ou_boss",
    }
    with monkeypatch.context() as patch:
        patch.setattr(_impl, "_invoke", _CapturedInvoke())
        anyio.run(lambda: _api.call_api_impl(**args))
    cap = _generic(monkeypatch, confirm=_code_from(sent[0]), **args)
    assert cap.request.http_method == HttpMethod.DELETE
    assert cap.request.paths == {"department_id": "od-x"}


def test_wrong_confirm_token_still_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    _confirm_codes(monkeypatch)
    res = anyio.run(
        lambda: _api.call_api_impl(
            method="DELETE",
            uri="/open-apis/contact/v3/departments/:department_id",
            paths_json=json.dumps({"department_id": "od-x"}),
            confirm="yes",
            user_key="ou_boss",
        )
    )
    assert res["ok"] is False
    assert cap.requests == []


def test_the_documented_phrase_is_not_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """``confirm: 删除部门`` is written in the skill file the model reads."""
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    _confirm_codes(monkeypatch)
    res = anyio.run(
        lambda: _api.call_api_impl(
            method="DELETE",
            uri="/open-apis/contact/v3/departments/:department_id",
            paths_json=json.dumps({"department_id": "od-x"}),
            confirm="删除部门",
            user_key="ou_boss",
        )
    )
    assert res["ok"] is False
    assert res["need_confirmation"] is True
    assert cap.requests == []


def test_reversible_writes_are_not_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the unrecoverable calls pay the extra round trip."""
    cap = _generic(
        monkeypatch,
        method="PATCH",
        uri="/open-apis/contact/v3/users/:user_id",
        paths_json=json.dumps({"user_id": "ou_abc"}),
        body_json=json.dumps({"name": "李四"}),
    )
    assert len(cap.requests) == 1


@pytest.mark.parametrize(
    ("uri", "body", "expect_ok"),
    [
        # A department name with a slash returns 43029 — a positive pattern can't say this.
        ("/open-apis/contact/v3/departments", {"name": "研发/中心", "parent_department_id": "0"}, False),
        ("/open-apis/contact/v3/departments", {"name": "研发中心", "parent_department_id": "0"}, True),
        # Feishu reserves the "od-" prefix and the ids "0" and "1".
        (
            "/open-apis/contact/v3/departments",
            {"name": "x", "parent_department_id": "0", "custom_department_id": "od-mine"},
            False,
        ),
        (
            "/open-apis/contact/v3/departments",
            {"name": "x", "parent_department_id": "0", "custom_department_id": "rd-1"},
            True,
        ),
        ("/open-apis/contact/v3/departments", {"name": "x"}, False),
    ],
)
def test_department_create_constraints(
    monkeypatch: pytest.MonkeyPatch, uri: str, body: dict[str, Any], expect_ok: bool
) -> None:
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    res = anyio.run(lambda: _api.call_api_impl(method="POST", uri=uri, body_json=json.dumps(body)))
    assert res.get("ok") is expect_ok, res.get("violations")
    assert bool(cap.requests) is expect_ok


@pytest.mark.parametrize(
    ("body", "expect_ok"),
    [
        ({"name": "张三", "mobile": "+8613800000000", "department_ids": ["od-x"]}, True),
        ({"name": "张三", "department_ids": ["od-x"]}, False),
        ({"name": "张三", "mobile": "1", "department_ids": [f"od-{i}" for i in range(51)]}, False),
        ({"name": "张三", "mobile": "1", "department_ids": ["od-x"], "employee_type": 9}, False),
        ({"name": "张三", "mobile": "1", "department_ids": ["od-x"], "employee_type": 2}, True),
    ],
)
def test_user_create_constraints(monkeypatch: pytest.MonkeyPatch, body: dict[str, Any], expect_ok: bool) -> None:
    """mobile is required and tenant-unique; 50 departments max; employee_type 1-5."""
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    res = anyio.run(
        lambda: _api.call_api_impl(method="POST", uri="/open-apis/contact/v3/users", body_json=json.dumps(body))
    )
    assert res.get("ok") is expect_ok, res.get("violations")


def test_group_member_add_rejects_department_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feishu documents department subjects but does not accept them here."""
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    res = anyio.run(
        lambda: _api.call_api_impl(
            method="POST",
            uri="/open-apis/contact/v3/group/:group_id/member/add",
            paths_json=json.dumps({"group_id": "g1"}),
            body_json=json.dumps({"member_type": "department", "member_id": "od-x"}),
        )
    )
    assert res["ok"] is False
    assert cap.requests == []


def test_group_list_pages_on_its_own_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """``group/simplelist`` returns ``grouplist``, one of the four non-``items`` keys."""
    cap = _CapturedInvoke([{"ok": True, "data": {"grouplist": [{"id": "g1"}], "has_more": False}}])
    monkeypatch.setattr(_impl, "_invoke", cap)
    res = anyio.run(lambda: _api.call_api_impl(method="GET", uri="/open-apis/contact/v3/group/simplelist"))
    assert res["grouplist"] == [{"id": "g1"}]


# ------------------------------------------------------------------ paging parity


def test_department_roster_pages_are_concatenated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dedicated tool looped on ``page_token``; the table now declares that."""
    cap = _generic(
        monkeypatch,
        pages=[
            {"ok": True, "data": {"items": [{"open_id": "a"}], "has_more": True, "page_token": "t2"}},
            {"ok": True, "data": {"items": [{"open_id": "b"}], "has_more": False}},
        ],
        method="GET",
        uri="/open-apis/contact/v3/users/find_by_department",
        query_json=json.dumps({"department_id": "od-x"}),
    )
    assert len(cap.requests) == 2
    first, second = cap.requests
    assert ("page_token", "t2") in [(k, str(v)) for k, v in second.queries]
    assert "page_token" not in [k for k, _ in first.queries]
    assert second.uri == first.uri


def test_role_members_use_their_own_items_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """``functional_roles/:role_id/members`` returns ``members``, not ``items``."""
    cap = _CapturedInvoke([{"ok": True, "data": {"members": [{"user_id": "u1"}], "has_more": False}}])
    monkeypatch.setattr(_impl, "_invoke", cap)
    res = anyio.run(
        lambda: _api.call_api_impl(
            method="GET",
            uri="/open-apis/contact/v3/functional_roles/:role_id/members",
            paths_json=json.dumps({"role_id": "r1"}),
        )
    )
    assert res["ok"] is True
    assert res["members"] == [{"user_id": "u1"}]


def test_write_endpoints_are_not_paged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A POST that happens to echo ``has_more`` must not be retried as a page."""
    cap = _generic(
        monkeypatch,
        pages=[{"ok": True, "data": {"items": [], "has_more": True, "page_token": "t2"}}],
        method="POST",
        uri="/open-apis/contact/v3/users",
        body_json=json.dumps({"name": "张三", "mobile": "+8613800000000", "department_ids": ["od-x"]}),
    )
    assert len(cap.requests) == 1
