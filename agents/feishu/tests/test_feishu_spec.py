"""Tests for endpoint-knowledge-as-data: the rules blocks in ``feishu-*`` skills.

Moving an endpoint out of Python and into a Markdown table only works if the table
*executes*. Feishu's worst failures are silent — a bare ``!A1`` range writes nothing
and still returns ``code: 0``, a mismatched Bitable column is dropped without error —
so a constraint that the model merely reads is indistinguishable from no constraint
at all when the model fills a wrong value.

What these tests hold down, therefore:

* a violating call is refused **before** the request is built (no request object is
  ever handed to ``_invoke``);
* the refusal names the fix, so the next attempt can succeed;
* declared defaults and token strategies reach the outgoing request;
* declarative paging follows ``page_token`` and concatenates, matching what the
  hand-written loops in ``_feishu_impl`` do;
* parsing survives a malformed block in one skill without taking down the rest.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from pathlib import Path
from typing import Any

import anyio
import pytest
from lark_channel.core.enum import AccessTokenType

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_spec: Any = importlib.import_module("_feishu_spec")
_api: Any = importlib.import_module("_feishu_api_impl")
_impl: Any = importlib.import_module("_feishu_impl")

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


class _Recorder:
    """Records every request handed to ``_invoke`` so a refusal is provably silent."""

    def __init__(self, pages: list[dict[str, Any]] | None = None) -> None:
        self.requests: list[Any] = []
        self.kwargs: list[dict[str, Any]] = []
        self._pages = pages or [{"ok": True, "data": {}}]

    async def __call__(self, request: Any, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(request() if callable(request) else request)
        self.kwargs.append(kwargs)
        idx = min(len(self.requests) - 1, len(self._pages) - 1)
        return self._pages[idx]

    @property
    def request(self) -> Any:
        assert len(self.requests) == 1, f"expected 1 request, got {len(self.requests)}"
        return self.requests[0]


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    _spec.reset_cache()
    yield
    _spec.reset_cache()


def _call(
    monkeypatch: pytest.MonkeyPatch,
    recorder: _Recorder,
    skills: Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    monkeypatch.setattr(_impl, "_invoke", recorder)
    if skills is not None:
        monkeypatch.setattr(_api, "_skills_dir", lambda: str(skills))
    return anyio.run(lambda: _api.call_api_impl(**kwargs))


# --------------------------------------------------------------------------- parsing


def test_parses_rules_block_from_markdown() -> None:
    rules = _spec.parse_rules(
        textwrap.dedent(
            """
            | 要什么 | uri |
            |---|---|
            | 查人 | `GET /open-apis/x/users` |

            ```rules
            - endpoint: GET /open-apis/x/users
              token: user
              required: [name]
              fields:
                page_size: {max: 50}
            ```
            """
        ),
        source="demo",
    )
    assert len(rules) == 1
    assert rules[0].method == "GET"
    assert rules[0].uri == "/open-apis/x/users"
    assert rules[0].token == "user"
    assert rules[0].required == ["name"]


def test_malformed_block_is_skipped_not_fatal() -> None:
    """One skill's YAML typo must not disable validation for every other endpoint."""
    text = textwrap.dedent(
        """
        ```rules
        - endpoint: GET /open-apis/good
        ```

        ```rules
        this: [is not: valid yaml
        ```
        """
    )
    rules = _spec.parse_rules(text)
    assert [r.uri for r in rules] == ["/open-apis/good"]


def test_rule_without_endpoint_is_ignored() -> None:
    assert _spec.parse_rules("```rules\n- token: user\n```") == []


def test_more_specific_uri_wins() -> None:
    text = textwrap.dedent(
        """
        ```rules
        - endpoint: GET /open-apis/sheets/v2
          token: tenant
        - endpoint: GET /open-apis/sheets/v2/spreadsheets/:t/values
          token: user
        ```
        """
    )
    rules = sorted(_spec.parse_rules(text), key=lambda r: -r.specificity)
    match = next(r for r in rules if r.matches("GET", "/open-apis/sheets/v2/spreadsheets/abc/values/Sheet1!A1"))
    assert match.token == "user", "longest matching prefix should win"


def test_method_is_part_of_the_match() -> None:
    rule = _spec.parse_rules("```rules\n- endpoint: POST /open-apis/x\n```")[0]
    assert rule.matches("POST", "/open-apis/x")
    assert not rule.matches("GET", "/open-apis/x")


# ------------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    ("spec_yaml", "value", "expect_ok"),
    [
        ("{max_items: 50}", list(range(50)), True),
        ("{max_items: 50}", list(range(51)), False),
        ("{max: 50}", 50, True),
        ("{max: 50}", 51, False),
        ("{min: 1}", 0, False),
        ("{choices: [1, 2]}", 1, True),
        ("{choices: [1, 2]}", 3, False),
        ("{pattern: '!'}", "Sheet1!A1", True),
        ("{pattern: '!'}", "A1", False),
    ],
)
def test_field_constraints(spec_yaml: str, value: Any, expect_ok: bool) -> None:
    rule = _spec.parse_rules(f"```rules\n- endpoint: GET /open-apis/x\n  fields:\n    f: {spec_yaml}\n```")[0]
    problems = _spec.validate(rule, {"f": value}, {}, {})
    assert (problems == []) is expect_ok, problems


def test_missing_required_field_is_reported() -> None:
    rule = _spec.parse_rules("```rules\n- endpoint: GET /open-apis/x\n  required: [a, b]\n```")[0]
    problems = _spec.validate(rule, {"a": 1}, {}, {})
    assert len(problems) == 1
    assert "b" in problems[0]


def test_all_violations_reported_together() -> None:
    """Two wrong fields should cost one round trip, not two."""
    rule = _spec.parse_rules(
        "```rules\n- endpoint: GET /open-apis/x\n  required: [a]\n  fields:\n    n: {max: 5}\n```"
    )[0]
    problems = _spec.validate(rule, {"n": 9}, {}, {})
    assert len(problems) == 2


def test_field_looked_up_across_body_query_and_paths() -> None:
    """A rule names a field once; which bucket carries it is the endpoint's business."""
    rule = _spec.parse_rules("```rules\n- endpoint: GET /open-apis/x\n  required: [pid]\n```")[0]
    assert _spec.validate(rule, {}, {}, {"pid": "x"}) == []
    assert _spec.validate(rule, {}, {"pid": "x"}, {}) == []


def test_a_pinned_field_in_the_wrong_bucket_is_reported() -> None:
    """``in: query`` used to be checked but never enforced.

    The lookup was scoped to the declared bucket, found nothing, and fell through as
    if the field were absent — so a field passed in the body rode out in the body and
    Feishu answered 99992402. The rule already knows where it belongs.
    """
    rule = _spec.parse_rules(
        "```rules\n- endpoint: POST /open-apis/x\n  fields:\n    t: {in: query, choices: [a, b]}\n```"
    )[0]
    assert _spec.validate(rule, {}, {"t": "a"}, {}) == []
    problems = _spec.validate(rule, {"t": "a"}, {}, {})
    assert len(problems) == 1
    assert "body" in problems[0] and "query" in problems[0]


def test_a_pinned_field_simply_absent_is_not_called_misplaced() -> None:
    """Not passing an optional field at all is fine — it is not in the wrong place."""
    rule = _spec.parse_rules("```rules\n- endpoint: POST /open-apis/x\n  fields:\n    t: {in: query}\n```")[0]
    assert _spec.validate(rule, {}, {}, {}) == []


def test_a_required_pinned_field_says_where_it_belongs() -> None:
    """Being told it is missing sends the caller hunting for the wrong mistake."""
    rule = _spec.parse_rules(
        "```rules\n- endpoint: POST /open-apis/x\n  required: [t]\n  fields:\n    t: {in: query}\n```"
    )[0]
    problems = _spec.validate(rule, {"t": "a"}, {}, {})
    assert len(problems) == 1
    assert "query" in problems[0]
    assert "缺少" not in problems[0], "it is not missing, it is unreachable where it was put"


def test_requires_pairs_fields() -> None:
    rule = _spec.parse_rules("```rules\n- endpoint: GET /open-apis/x\n  fields:\n    a: {requires: b}\n```")[0]
    assert _spec.validate(rule, {"a": 1}, {}, {}) != []
    assert _spec.validate(rule, {"a": 1, "b": 2}, {}, {}) == []


def test_no_rule_means_no_constraint() -> None:
    assert _spec.validate(None, {}, {}, {}) == []


# ------------------------------------------------------------------- hard refusal


def test_violation_refused_before_any_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The whole point: a bad call must not reach Feishu at all.

    A warning on a successful-looking result is useless here — Feishu answers a
    silently-wrong write with ``code: 0``, so the only place to stop is upstream.
    """
    skill = tmp_path / "feishu-demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n- endpoint: GET /open-apis/demo/list\n  fields:\n    page_size: {max: 50}\n```",
        encoding="utf-8",
    )
    rec = _Recorder()
    res = _call(
        monkeypatch, rec, skills=tmp_path, method="GET", uri="/open-apis/demo/list", query_json='{"page_size": 500}'
    )
    assert res["ok"] is False
    assert res["code"] == "spec_violation"
    assert rec.requests == [], "a refused call must send nothing"
    assert "50" in " ".join(res["violations"])


def test_refusal_carries_the_fix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skill = tmp_path / "feishu-demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n- endpoint: POST /open-apis/demo/write\n  fields:\n"
        "    range: {pattern: '!', on_fail: \"range 要写成 'Sheet1!A1', 裸 A1 会静默丢数据\"}\n```",
        encoding="utf-8",
    )
    rec = _Recorder()
    res = _call(
        monkeypatch, rec, skills=tmp_path, method="POST", uri="/open-apis/demo/write", body_json='{"range": "A1"}'
    )
    assert res["ok"] is False
    assert "Sheet1!A1" in " ".join(res["violations"])
    assert "SKILL.md" in res["note"], "must say where to change the rule if Feishu relaxed it"
    assert rec.requests == []


def test_prefer_tool_hard_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skill = tmp_path / "feishu-demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n- endpoint: POST /open-apis/demo/upload\n  prefer_tool: feishu_drive_upload\n"
        "  hard: true\n  why: body 必须是真文件句柄\n```",
        encoding="utf-8",
    )
    rec = _Recorder()
    res = _call(monkeypatch, rec, skills=tmp_path, method="POST", uri="/open-apis/demo/upload")
    assert res["ok"] is False
    assert res["tool"] == "feishu_drive_upload"
    assert rec.requests == []


def test_soft_prefer_tool_does_not_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without ``hard: true`` the call proceeds — the endpoint list is not exhaustive,
    and a blanket refusal would strand legitimate calls."""
    skill = tmp_path / "feishu-demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n- endpoint: GET /open-apis/demo/soft\n  prefer_tool: feishu_something\n```",
        encoding="utf-8",
    )
    rec = _Recorder()
    res = _call(monkeypatch, rec, skills=tmp_path, method="GET", uri="/open-apis/demo/soft")
    assert res["ok"] is True
    assert len(rec.requests) == 1


# --------------------------------------------------------------- defaults & token


def test_declared_default_reaches_the_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skill = tmp_path / "feishu-demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n- endpoint: GET /open-apis/demo/user\n  fields:\n    user_id_type: {default: open_id}\n```",
        encoding="utf-8",
    )
    rec = _Recorder()
    _call(monkeypatch, rec, skills=tmp_path, method="GET", uri="/open-apis/demo/user")
    assert rec.request.queries == [("user_id_type", "open_id")]


def test_explicit_value_beats_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skill = tmp_path / "feishu-demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n- endpoint: GET /open-apis/demo/user\n  fields:\n    user_id_type: {default: open_id}\n```",
        encoding="utf-8",
    )
    rec = _Recorder()
    _call(
        monkeypatch,
        rec,
        skills=tmp_path,
        method="GET",
        uri="/open-apis/demo/user",
        query_json='{"user_id_type":"user_id"}',
    )
    assert rec.request.queries == [("user_id_type", "user_id")]


def test_rule_token_user_reaches_invoke_as_strategy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``token: user`` picks the *send path*, not the declared candidates.

    ``_invoke`` reads ``prefer`` to decide whose token to attach; the request keeps both
    candidates so the tenant path stays sendable (``_invoke_write`` deliberately sends as
    tenant when nobody is logged in, or when the caller answered ``identity=bot``).
    Narrowing to USER here would make those two sends raise inside the SDK before any
    network call — see ``lark_channel/core/token/auth.py``.
    """
    skill = tmp_path / "feishu-demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n- endpoint: GET /open-apis/demo/search\n  token: user\n```", encoding="utf-8"
    )
    rec = _Recorder()
    _call(monkeypatch, rec, skills=tmp_path, method="GET", uri="/open-apis/demo/search")
    assert rec.kwargs[0]["prefer"] == "user"
    assert rec.request.token_types == {AccessTokenType.TENANT, AccessTokenType.USER}


def test_caller_prefer_user_is_not_overridden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skill = tmp_path / "feishu-demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n- endpoint: GET /open-apis/demo/x\n  token: tenant\n```", encoding="utf-8"
    )
    rec = _Recorder()
    _call(monkeypatch, rec, skills=tmp_path, method="GET", uri="/open-apis/demo/x", prefer="user")
    assert rec.kwargs[0]["prefer"] == "user", "an explicit caller prefer beats the table"


# ------------------------------------------------------------------------ paging


def test_paging_follows_token_and_concatenates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Matches what the hand-written loops in ``_feishu_impl`` do, from a table row."""
    skill = tmp_path / "feishu-demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n- endpoint: GET /open-apis/demo/members\n  paginate: {page_size: 100}\n```",
        encoding="utf-8",
    )
    rec = _Recorder(
        [
            {"ok": True, "data": {"items": [1, 2], "has_more": True, "page_token": "p2"}},
            {"ok": True, "data": {"items": [3], "has_more": False}},
        ]
    )
    res = _call(monkeypatch, rec, skills=tmp_path, method="GET", uri="/open-apis/demo/members")
    assert res["ok"] is True
    assert res["items"] == [1, 2, 3]
    assert res["count"] == 3
    assert res["pages"] == 2
    assert len(rec.requests) == 2
    assert ("page_size", "100") in rec.requests[0].queries
    assert ("page_token", "p2") in rec.requests[1].queries
    assert ("page_token", "p2") not in rec.requests[0].queries


def test_paging_custom_items_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Four Feishu endpoints don't use ``items`` — the key is declarable."""
    skill = tmp_path / "feishu-demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n- endpoint: GET /open-apis/demo/tasks\n  paginate: {items: tasks}\n```",
        encoding="utf-8",
    )
    rec = _Recorder([{"ok": True, "data": {"tasks": ["t1"], "has_more": False}}])
    res = _call(monkeypatch, rec, skills=tmp_path, method="GET", uri="/open-apis/demo/tasks")
    assert res["tasks"] == ["t1"]


def test_paging_returns_partial_on_mid_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Three pages of members plus 'page 4 failed' beats discarding everything."""
    skill = tmp_path / "feishu-demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n- endpoint: GET /open-apis/demo/members\n  paginate: true\n```", encoding="utf-8"
    )
    rec = _Recorder(
        [
            {"ok": True, "data": {"items": [1], "has_more": True, "page_token": "p2"}},
            {"ok": False, "code": 99991400, "message": "rate limited"},
        ]
    )
    res = _call(monkeypatch, rec, skills=tmp_path, method="GET", uri="/open-apis/demo/members")
    assert res["ok"] is False
    assert res["partial"] is True
    assert res["items"] == [1]


def test_paging_stops_at_max_pages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A wrong ``items`` key on an echoing endpoint must fail loudly, not spin."""
    skill = tmp_path / "feishu-demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n- endpoint: GET /open-apis/demo/loop\n  paginate: {max_pages: 3}\n```",
        encoding="utf-8",
    )
    rec = _Recorder([{"ok": True, "data": {"items": [1], "has_more": True, "page_token": "same"}}])
    res = _call(monkeypatch, rec, skills=tmp_path, method="GET", uri="/open-apis/demo/loop")
    assert res["truncated"] is True
    assert len(rec.requests) == 3


def test_no_paginate_sends_exactly_one_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skill = tmp_path / "feishu-demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text("```rules\n- endpoint: GET /open-apis/demo/one\n```", encoding="utf-8")
    rec = _Recorder([{"ok": True, "data": {"items": [1], "has_more": True, "page_token": "p2"}}])
    _call(monkeypatch, rec, skills=tmp_path, method="GET", uri="/open-apis/demo/one")
    assert len(rec.requests) == 1, "paging must be opt-in per endpoint"


# ------------------------------------------- a rule reached by prefix cannot refuse
#
# Feishu hangs unrelated operations under a collection URI, so the parent's payload
# constraints must not reach the child. Left inheriting, ``POST /im/v1/chats``
# (requires ``name``) refuses ``POST /im/v1/chats/:chat_id/managers/add_managers``,
# which takes no ``name`` — the endpoint becomes unreachable through either route.


_NESTED_SKILL = """
```rules
- endpoint: POST /open-apis/demo/chats
  token: tenant
  required: [name]
  confirm: MAKE_CHAT
  fields:
    page_size: {max: 50, default: 20}
```
"""


def _nested(tmp_path: Path) -> Path:
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(_NESTED_SKILL, encoding="utf-8")
    return tmp_path


def test_prefix_matched_rule_drops_required(tmp_path: Path) -> None:
    rule = _spec.rules_for(_nested(tmp_path), "POST", "/open-apis/demo/chats/ocX/managers")
    assert rule is not None, "the subtree rule should still be found"
    assert rule.required == [], "a child endpoint must not inherit the parent's required fields"
    assert _spec.validate(rule, {}, {}, {}) == []


def test_prefix_matched_rule_drops_confirm(tmp_path: Path) -> None:
    rule = _spec.rules_for(_nested(tmp_path), "POST", "/open-apis/demo/chats/ocX/managers")
    assert rule is not None
    assert not rule.confirm, "a confirm token guards one irreversible call, not a whole subtree"


def test_prefix_matched_rule_drops_hard_prefer_tool(tmp_path: Path) -> None:
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n- endpoint: POST /open-apis/demo/messages\n  prefer_tool: demo_send\n  hard: true\n```\n",
        encoding="utf-8",
    )
    rule = _spec.rules_for(tmp_path, "POST", "/open-apis/demo/messages/omX/urgent_app")
    assert rule is not None
    assert not rule.prefer_hard, "refusing a child names a tool that cannot do the child's job"
    assert not rule.prefer_tool


def test_prefix_matched_rule_injects_no_defaults(tmp_path: Path) -> None:
    rule = _spec.rules_for(_nested(tmp_path), "POST", "/open-apis/demo/chats/ocX/managers")
    assert _spec.defaults_for(rule)["query"] == {}, "a default would add a field the child never declared"


def test_prefix_matched_rule_keeps_token_and_field_checks(tmp_path: Path) -> None:
    """Advice still travels: the subtree's token strategy and value checks are useful."""
    rule = _spec.rules_for(_nested(tmp_path), "POST", "/open-apis/demo/chats/ocX/managers")
    assert rule is not None
    assert rule.token == "tenant"
    assert _spec.validate(rule, {}, {"page_size": 500}, {}) != [], "value checks only fire on fields actually sent"


def test_exact_match_still_enforces_everything(tmp_path: Path) -> None:
    """The downgrade must not leak upward and weaken the endpoint that owns the rule."""
    rule = _spec.rules_for(_nested(tmp_path), "POST", "/open-apis/demo/chats")
    assert rule is not None
    assert rule.required == ["name"]
    assert rule.confirm == "MAKE_CHAT"
    assert _spec.defaults_for(rule)["query"] == {"page_size": 20}


def test_nested_endpoint_with_its_own_rule_is_enforced(tmp_path: Path) -> None:
    """The escape hatch: an endpoint that needs enforcement declares its own row."""
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "```rules\n"
        "- endpoint: POST /open-apis/demo/chats\n  required: [name]\n"
        "- endpoint: POST /open-apis/demo/chats/:chat_id/members\n  required: [id_list]\n"
        "```\n",
        encoding="utf-8",
    )
    rule = _spec.rules_for(tmp_path, "POST", "/open-apis/demo/chats/ocX/members")
    assert rule is not None
    assert rule.required == ["id_list"], "the specific rule wins and keeps its teeth"
    assert _spec.validate(rule, {}, {}, {}) == ["缺少必填字段 id_list"]


@pytest.mark.parametrize(
    ("method", "uri", "what"),
    [
        ("POST", "/open-apis/im/v1/messages/om_x/urgent_app", "加急(应用内)"),
        ("POST", "/open-apis/im/v1/messages/om_x/urgent_sms", "加急(短信)"),
        ("POST", "/open-apis/im/v1/messages/om_x/push_follow_up", "follow-up"),
        ("GET", "/open-apis/im/v1/messages/om_x", "读单条消息"),
        ("POST", "/open-apis/im/v1/chats/oc_x/managers/add_managers", "加群管理员"),
        ("DELETE", "/open-apis/im/v1/chats/oc_x/managers/delete_managers", "删群管理员"),
        # Nested under the hard `POST .../tables`. This was `.../tables/:table_id/views`
        # until the views endpoints got rules of their own — a tabled endpoint's `required`
        # is enforcement someone chose, so asserting it away would invert this test. Field
        # groups is a real Feishu endpoint still absent from every table.
        ("POST", "/open-apis/bitable/v1/apps/app_x/tables/tbl_x/field_groups", "建字段分组"),
        ("POST", "/open-apis/contact/v3/users/ou_x/resurrect", "恢复离职用户"),
    ],
)
def test_shipped_skills_strand_no_real_endpoint(method: str, uri: str, what: str) -> None:
    """Real Feishu endpoints we did not table must stay callable through the generic tool."""
    rule = _spec.rules_for(SKILLS_DIR, method, uri)
    if rule is None:
        return
    assert not rule.prefer_hard, f"{what} 被 {rule.endpoint} 拦下, 指向的工具做不了这件事"
    assert not rule.confirm, f"{what} 继承了 {rule.endpoint} 的 confirm, 那个令牌是给别的调用的"
    assert _spec.validate(rule, {}, {}, {}) == [], f"{what} 继承了 {rule.endpoint} 的必填字段"


# ------------------------------------------------- the real contact skill on disk


def test_shipped_contact_skill_parses() -> None:
    rules = _spec.load_rules(SKILLS_DIR)
    contact = [r for r in rules if r.source == "feishu-contact"]
    assert contact, "feishu-contact/SKILL.md should carry rules"
    assert all(r.uri.startswith("/open-apis/") for r in contact)


def test_shipped_contact_rules_are_ordered_most_specific_first() -> None:
    rules = _spec.load_rules(SKILLS_DIR)
    assert [r.specificity for r in rules] == sorted((r.specificity for r in rules), reverse=True)


@pytest.mark.parametrize(
    ("method", "uri", "query", "expect_ok"),
    [
        ("GET", "/open-apis/contact/v3/users/find_by_department", {"department_id": "d1"}, True),
        ("GET", "/open-apis/contact/v3/users/find_by_department", {}, False),
        ("GET", "/open-apis/contact/v3/users/find_by_department", {"department_id": "d1", "page_size": 500}, False),
        ("GET", "/open-apis/trust_party/v1/collaboration_tenants", {"page_size": 100}, True),
        ("GET", "/open-apis/trust_party/v1/collaboration_tenants", {"page_size": 101}, False),
    ],
)
def test_shipped_contact_rules_enforce(method: str, uri: str, query: dict[str, Any], expect_ok: bool) -> None:
    rule = _spec.rules_for(SKILLS_DIR, method, uri)
    assert rule is not None, f"no rule matched {method} {uri}"
    problems = _spec.validate(rule, {}, query, {})
    assert (problems == []) is expect_ok, problems


def test_search_user_endpoint_declares_user_token() -> None:
    """The gotcha this encodes: /search/v1/user rejects a tenant token outright."""
    rule = _spec.rules_for(SKILLS_DIR, "GET", "/open-apis/search/v1/user")
    assert rule is not None
    assert rule.token == "user"


def test_irreversible_writes_carry_pitfalls() -> None:
    """Resign / delete-department / delete-group are unrecoverable; the table must say so."""
    for uri in (
        "/open-apis/contact/v3/users/ou_x",
        "/open-apis/contact/v3/departments/d1",
        "/open-apis/contact/v3/group/g1",
    ):
        rule = _spec.rules_for(SKILLS_DIR, "DELETE", uri)
        assert rule is not None, f"DELETE {uri} should have a rule"
        assert rule.pitfalls, f"DELETE {uri} must document that it is irreversible"
        assert any("确认" in p or "不可逆" in p for p in rule.pitfalls)
