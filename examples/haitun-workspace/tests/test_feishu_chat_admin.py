"""Tests for running a Feishu group: 群公告 / 群设置 / 禁言 / 解散 / 转让群主 / 群菜单 /
群标签页 / 会话列表 / 消息搜索.

These tools mostly wrap one endpoint each, so what needs covering is not "does it call
Feishu" but the handful of places where the *obvious* request is the wrong one:

- an announcement is a docx document, addressed by ``chat_id``-as-root-block_id, and
  every write is optimistically locked on a ``revision_id`` that moves after each call;
- 禁言 lives on a different endpoint from every other group setting;
- ``add_member_permission`` and ``share_card_permission`` must agree or Feishu refuses;
- dismissing a group is irreversible, so it must not be reachable by accident;
- message search is user-token-only and returns bare ids that have to be hydrated.

Assertions land on the outgoing ``BaseRequest`` (method / uri / paths / queries / body),
never on intent — a tool that builds the wrong request while reporting success is
exactly the failure these guard against.
"""

from __future__ import annotations

import importlib
import inspect
import json
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


class _Sequenced:
    """Answer successive ``_invoke`` calls from a queue, recording every request.

    Several of these operations deliberately make more than one call (read the revision,
    delete, re-read, append), so asserting on them needs the *order* of requests rather
    than just the last one.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.requests: list[Any] = []
        self.kwargs: list[dict[str, Any]] = []

    async def __call__(self, request: Any, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(request() if callable(request) else request)
        self.kwargs.append(kwargs)
        if not self.responses:
            raise AssertionError(f"unexpected extra _invoke call #{len(self.requests)}")
        return {"ok": True, "code": 0, "msg": "", "data": {}, **self.responses.pop(0)}

    @property
    def request(self) -> Any:
        assert len(self.requests) == 1, f"expected exactly one call, got {len(self.requests)}"
        return self.requests[0]


def _failing(code: int, msg: str = "denied") -> Any:
    async def _fail(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": False, "code": code, "msg": msg, "message": f"Feishu API error {code}"}

    return _fail


async def _async(value: dict[str, Any]) -> dict[str, Any]:
    return value


# ── 群公告 (announcement) ────────────────────────────────────────────────────────


def _meta(revision: int = 7) -> dict[str, Any]:
    return {
        "data": {
            "revision_id": revision,
            "announcement_type": "docx",
            "owner_id": "ou_owner",
            "modifier_id": "ou_mod",
            "update_time_v2": "1700000000",
        }
    }


def _blocks(*texts: str, chat_id: str = "oc_x") -> dict[str, Any]:
    """A blocks page shaped like Feishu's: the root block first, then the body."""
    items: list[dict[str, Any]] = [{"block_id": chat_id, "block_type": 1, "parent_id": ""}]
    for position, text in enumerate(texts):
        items.append(
            {
                "block_id": f"blk_{position}",
                "block_type": 2,
                "parent_id": chat_id,
                "text": {"elements": [{"text_run": {"content": text}}]},
            }
        )
    return {"data": {"items": items, "has_more": False}}


@pytest.mark.asyncio
async def test_read_announcement_uses_docx_endpoints_and_strips_root(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_meta(9), _blocks("值班表", "周一 张三")])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.read_chat_announcement_impl("oc_x")

    meta_req, blocks_req = seq.requests
    # The announcement is a *document*: docx endpoints, not im/v1.
    assert meta_req.uri == "/open-apis/docx/v1/chats/:chat_id/announcement"
    assert blocks_req.uri == "/open-apis/docx/v1/chats/:chat_id/announcement/blocks"
    assert blocks_req.paths["chat_id"] == "oc_x"
    assert result["revision_id"] == 9
    assert result["announcement_type"] == "docx"
    # The root block (whose id IS the chat_id) is scaffolding, not content.
    assert result["block_count"] == 2
    assert [b["block_id"] for b in result["blocks"]] == ["blk_0", "blk_1"]
    assert result["text"] == "值班表\n周一 张三"
    assert result["empty"] is False


@pytest.mark.asyncio
async def test_read_announcement_empty_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _Sequenced([_meta(), _blocks()]))
    result = await _impl.read_chat_announcement_impl("oc_x")
    # A group that never had a notice still has an announcement document.
    assert result["ok"] is True
    assert result["empty"] is True
    assert result["text"] == ""
    assert result["block_count"] == 0


@pytest.mark.asyncio
async def test_read_announcement_translates_legacy_and_doc_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _failing(232097))
    assert "旧版" in (await _impl.read_chat_announcement_impl("oc_x"))["hint"]

    monkeypatch.setattr(_impl, "_invoke", _failing(232066))
    assert "阅读权限" in (await _impl.read_chat_announcement_impl("oc_x"))["hint"]


@pytest.mark.asyncio
async def test_set_announcement_replaces_then_appends_with_fresh_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced(
        [
            _meta(4),  # clear: read revision
            _blocks("旧公告", "旧的第二行"),  # clear: read blocks
            {"data": {"revision_id": 5}},  # clear: delete
            _meta(5),  # append: re-read revision
            {"data": {"revision_id": 6}},  # append
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.set_chat_announcement_impl("oc_x", "# 新公告\n第一条")

    delete_req, append_req = seq.requests[2], seq.requests[4]
    assert delete_req.http_method.name == "DELETE"
    assert delete_req.body == {"start_index": 0, "end_index": 2}
    # The delete bumps the revision, so the append must not reuse the pre-delete one —
    # Feishu rejects a stale optimistic lock.
    assert _qdict(delete_req).get("revision_id") == "4"
    assert _qdict(append_req).get("revision_id") == "5"
    # The announcement root's block_id is the chat_id itself.
    assert append_req.paths["block_id"] == "oc_x"
    assert [b["block_type"] for b in append_req.body["children"]] == [3, 2]  # heading, paragraph
    assert (result["deleted"], result["added"], result["replaced"]) == (2, 2, True)


@pytest.mark.asyncio
async def test_set_announcement_append_mode_keeps_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_meta(3), {"data": {"revision_id": 4}}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.set_chat_announcement_impl("oc_x", "补一条", replace=False)
    # No delete at all: read the revision, then append.
    assert [r.http_method.name for r in seq.requests] == ["GET", "POST"]
    assert result["deleted"] == 0
    assert result["replaced"] is False


@pytest.mark.asyncio
async def test_set_announcement_refuses_blank_and_points_at_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([])
    monkeypatch.setattr(_impl, "_invoke", seq)
    blank = await _impl.set_chat_announcement_impl("oc_x", "   ")
    missing = await _impl.set_chat_announcement_impl("", "x")
    # Emptying a group's notice must be asked for by name, never inferred from "".
    assert blank["ok"] is False
    assert "feishu_chat_announcement_clear" in blank["message"]
    assert missing["ok"] is False
    assert seq.requests == []  # both refused before spending a request


@pytest.mark.asyncio
async def test_clear_announcement_deletes_the_body_range(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_meta(11), _blocks("a", "b", "c"), {"data": {"revision_id": 12}}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.clear_chat_announcement_impl("oc_x")
    assert seq.requests[2].body == {"start_index": 0, "end_index": 3}
    assert result["deleted"] == 3
    assert result["revision_id"] == 12


@pytest.mark.asyncio
async def test_clear_announcement_on_empty_notice_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_meta(2), _blocks()])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.clear_chat_announcement_impl("oc_x")
    # Nothing to delete is success, not an error — and spends no DELETE.
    assert result["ok"] is True
    assert result["deleted"] == 0
    assert all(r.http_method.name == "GET" for r in seq.requests)


# ── 群设置变更 (update) ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_chat_sends_only_named_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([{"data": {}}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.update_chat_impl("oc_x", name="新群名")
    req = seq.request
    assert req.http_method.name == "PUT"
    assert req.uri == "/open-apis/im/v1/chats/:chat_id"
    assert req.paths["chat_id"] == "oc_x"
    # Renaming must not carry along permission fields Feishu would then apply.
    assert req.body == {"name": "新群名"}
    assert result["updated"] == {"name": "新群名"}


@pytest.mark.asyncio
async def test_update_chat_couples_share_card_to_add_member(monkeypatch: pytest.MonkeyPatch) -> None:
    # Feishu refuses only_owner + allowed, and one half alone leaves the pair
    # contradictory — so the partner field is derived rather than left to the caller.
    seq = _Sequenced([{"data": {}}, {"data": {}}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    await _impl.update_chat_impl("oc_x", add_member_permission="only_owner")
    assert seq.requests[0].body == {
        "add_member_permission": "only_owner",
        "share_card_permission": "not_allowed",
    }
    await _impl.update_chat_impl("oc_x", add_member_permission="all_members")
    assert seq.requests[1].body == {
        "add_member_permission": "all_members",
        "share_card_permission": "allowed",
    }


@pytest.mark.asyncio
async def test_update_chat_accepts_chinese_enum_words(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([{"data": {}}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    await _impl.update_chat_impl(
        "oc_x",
        at_all_permission="仅群主和管理员",
        edit_permission="所有群成员",
        membership_approval="需审批",
        chat_type="公开",
    )
    assert seq.request.body == {
        "at_all_permission": "only_owner",
        "edit_permission": "all_members",
        "membership_approval": "approval_required",
        "chat_type": "public",
    }


@pytest.mark.asyncio
async def test_update_chat_refuses_unknown_values_and_empty_change(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([])
    monkeypatch.setattr(_impl, "_invoke", seq)
    bad_who = await _impl.update_chat_impl("oc_x", add_member_permission="everyone")
    assert bad_who["ok"] is False
    assert "all_members" in bad_who["message"]

    bad_approval = await _impl.update_chat_impl("oc_x", membership_approval="maybe")
    assert bad_approval["ok"] is False
    assert "approval_required" in bad_approval["message"]

    nothing = await _impl.update_chat_impl("oc_x")
    assert nothing["ok"] is False
    # Point at the two things that deliberately are NOT on this endpoint.
    assert "feishu_chat_mute" in nothing["message"]
    assert "feishu_chat_transfer_owner" in nothing["message"]
    assert seq.requests == []  # nothing was sent


@pytest.mark.asyncio
async def test_update_chat_hints_edit_restriction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _failing(232002))
    assert "仅群主和管理员可编辑群信息" in (await _impl.update_chat_impl("oc_x", name="x"))["hint"]

    monkeypatch.setattr(_impl, "_invoke", _failing(232021))
    assert "image_type='avatar'" in (await _impl.update_chat_impl("oc_x", avatar="img_x"))["hint"]


# ── 转让群主 (transfer owner) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_owner_sets_owner_id_on_the_update_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([{"data": {}}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.transfer_chat_owner_impl("oc_x", "ou_new", user_key="ou_owner")
    req = seq.request
    assert req.http_method.name == "PUT"
    assert req.body == {"owner_id": "ou_new"}
    assert _qdict(req).get("user_id_type") == "open_id"
    assert seq.kwargs[0]["user_key"] == "ou_owner"
    assert result["new_owner_id"] == "ou_new"


@pytest.mark.asyncio
async def test_transfer_owner_requires_target_and_hints_non_member(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([])
    monkeypatch.setattr(_impl, "_invoke", seq)
    assert (await _impl.transfer_chat_owner_impl("oc_x", "  "))["ok"] is False
    assert (await _impl.transfer_chat_owner_impl("", "ou_new"))["ok"] is False
    assert (await _impl.transfer_chat_owner_impl("oc_x", "ou_new", "email"))["ok"] is False
    assert seq.requests == []

    # 232012 is the one an agent will actually hit: the new owner isn't in the group yet.
    monkeypatch.setattr(_impl, "_invoke", _failing(232012))
    assert "先用 feishu_chat_add_members" in (await _impl.transfer_chat_owner_impl("oc_x", "ou_new"))["hint"]


# ── 解散群 (dismiss) ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dismiss_requires_explicit_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([])
    monkeypatch.setattr(_impl, "_invoke", seq)
    for confirm in ("", "yes", "确认", "解散", "解散群聊"):
        blocked = await _impl.dismiss_chat_impl("oc_x", confirm)
        assert blocked["ok"] is False, confirm
        assert blocked["need_confirmation"] is True
    # A loosely-worded instruction must not be able to dissolve a group.
    assert seq.requests == []


@pytest.mark.asyncio
async def test_dismiss_with_confirmation_sends_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([{"data": {}}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.dismiss_chat_impl("oc_x", "解散群", user_key="ou_owner")
    req = seq.request
    assert req.http_method.name == "DELETE"
    assert req.uri == "/open-apis/im/v1/chats/:chat_id"
    assert req.paths["chat_id"] == "oc_x"
    assert result["dismissed"] is True


@pytest.mark.asyncio
async def test_dismiss_hints_owner_only_and_already_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _failing(232017))
    assert "群主" in (await _impl.dismiss_chat_impl("oc_x", "解散群"))["hint"]

    monkeypatch.setattr(_impl, "_invoke", _failing(232009))
    assert "已解散" in (await _impl.dismiss_chat_impl("oc_x", "解散群"))["hint"]


# ── 全员禁言 (moderation) ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mute_uses_the_moderation_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    # The trap this guards: the 谁可以发言 value chat_get *reads* cannot be written
    # through the chat-update body — it needs this separate endpoint.
    seq = _Sequenced([{"data": {}}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.update_chat_moderation_impl("oc_x", "全员禁言", user_key="ou_owner")
    req = seq.request
    assert req.http_method.name == "PUT"
    assert req.uri == "/open-apis/im/v1/chats/:chat_id/moderation"
    assert req.body == {"moderation_setting": "only_owner"}
    assert result["moderation_setting"] == "only_owner"


@pytest.mark.asyncio
async def test_mute_maps_release_and_moderator_list(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([{"data": {}}, {"data": {}}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    await _impl.update_chat_moderation_impl("oc_x", "解除禁言")
    assert seq.requests[0].body == {"moderation_setting": "all_members"}

    await _impl.update_chat_moderation_impl(
        "oc_x", "moderator_list", speaker_ids=["ou_a", " ou_b "], revoke_ids=["ou_c"]
    )
    assert seq.requests[1].body == {
        "moderation_setting": "moderator_list",
        "moderator_added_list": ["ou_a", "ou_b"],
        "moderator_removed_list": ["ou_c"],
    }


@pytest.mark.asyncio
async def test_mute_rejects_bad_setting_overlap_and_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([])
    monkeypatch.setattr(_impl, "_invoke", seq)
    bad = await _impl.update_chat_moderation_impl("oc_x", "禁言所有人")
    assert bad["ok"] is False
    assert "moderator_list" in bad["message"]

    # Feishu rejects an id in both lists; catching it here names the id.
    overlap = await _impl.update_chat_moderation_impl(
        "oc_x", "moderator_list", speaker_ids=["ou_a"], revoke_ids=["ou_a"]
    )
    assert overlap["ok"] is False
    assert "ou_a" in overlap["message"]

    # moderator_list with nobody named would mute everyone — not what was asked.
    empty = await _impl.update_chat_moderation_impl("oc_x", "moderator_list")
    assert empty["ok"] is False
    assert seq.requests == []


@pytest.mark.asyncio
async def test_mute_hints_meeting_in_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _failing(232092))
    hint = (await _impl.update_chat_moderation_impl("oc_x", "全员禁言"))["hint"]
    assert "开会" in hint


# ── 群菜单 (menu) ───────────────────────────────────────────────────────────────


def _menu_reply(*names: str) -> dict[str, Any]:
    return {
        "data": {
            "menu_tree": {
                "chat_menu_top_levels": [
                    {
                        "chat_menu_top_level_id": f"top_{position}",
                        "chat_menu_item": {"name": name, "redirect_link": {"common_url": f"https://x/{position}"}},
                        "children": [
                            {
                                "chat_menu_second_level_id": f"sub_{position}",
                                "chat_menu_item": {"name": f"{name}-子", "redirect_link": {"common_url": "https://y"}},
                            }
                        ],
                    }
                    for position, name in enumerate(names)
                ]
            }
        }
    }


@pytest.mark.asyncio
async def test_get_menu_flattens_ids_and_children(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_menu_reply("帮助")])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.get_chat_menu_impl("oc_x")
    assert seq.request.uri == "/open-apis/im/v1/chats/:chat_id/menu_tree"
    assert seq.request.http_method.name == "GET"
    # The id is the only way to delete a menu, so it has to survive the flattening.
    assert result["menus"][0]["id"] == "top_0"
    assert result["menus"][0]["url"] == "https://x/0"
    assert result["menus"][0]["children"][0]["id"] == "sub_0"
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_add_menu_builds_feishu_nested_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_menu_reply("值班表")])
    monkeypatch.setattr(_impl, "_invoke", seq)
    await _impl.add_chat_menu_impl(
        "oc_x",
        [
            {"name": "值班表", "url": "https://example.com/duty", "image_key": "img_1"},
            {"name": "常用", "children": [{"name": "报销", "url": "https://example.com/fee"}]},
        ],
    )
    body = seq.request.body
    top = body["menu_tree"]["chat_menu_top_levels"]
    # A menu with a URL redirects; the icon rides along.
    assert top[0]["chat_menu_item"]["action_type"] == "REDIRECT_LINK"
    assert top[0]["chat_menu_item"]["redirect_link"] == {"common_url": "https://example.com/duty"}
    assert top[0]["chat_menu_item"]["image_key"] == "img_1"
    assert "children" not in top[0]
    # A menu with children is a container: action_type NONE and no link of its own.
    assert top[1]["chat_menu_item"]["action_type"] == "NONE"
    assert "redirect_link" not in top[1]["chat_menu_item"]
    assert top[1]["children"][0]["chat_menu_item"]["name"] == "报销"


@pytest.mark.asyncio
async def test_add_menu_enforces_feishu_shape_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([])
    monkeypatch.setattr(_impl, "_invoke", seq)
    assert (await _impl.add_chat_menu_impl("oc_x", []))["ok"] is False
    # 3 top-level max, 5 children max.
    assert (await _impl.add_chat_menu_impl("oc_x", [{"name": f"m{i}"} for i in range(4)]))["ok"] is False
    deep = await _impl.add_chat_menu_impl(
        "oc_x", [{"name": "多", "children": [{"name": f"c{i}", "url": "https://y"} for i in range(6)]}]
    )
    assert deep["ok"] is False

    # A parent with children may not itself redirect or carry an icon.
    both = await _impl.add_chat_menu_impl(
        "oc_x", [{"name": "混", "url": "https://y", "children": [{"name": "c", "url": "https://y"}]}]
    )
    assert both["ok"] is False
    assert "只能是分组" in both["message"]

    # A relative link would be accepted by Feishu and then do nothing useful.
    scheme = await _impl.add_chat_menu_impl("oc_x", [{"name": "x", "url": "example.com"}])
    assert scheme["ok"] is False
    assert "http://" in scheme["message"]
    assert (await _impl.add_chat_menu_impl("oc_x", [{"name": "  "}]))["ok"] is False
    assert seq.requests == []


@pytest.mark.asyncio
async def test_delete_menu_takes_ids_not_names(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_menu_reply()])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.delete_chat_menu_impl("oc_x", ["top_0", " top_1 "])
    assert seq.request.http_method.name == "DELETE"
    assert seq.request.body == {"chat_menu_top_level_ids": ["top_0", "top_1"]}
    assert result["deleted"] == ["top_0", "top_1"]

    empty = await _impl.delete_chat_menu_impl("oc_x", [])
    assert empty["ok"] is False
    assert "不是菜单名" in empty["message"]


# ── 群标签页 (tabs) ─────────────────────────────────────────────────────────────


def _tabs_reply(*specs: tuple[str, str, str]) -> dict[str, Any]:
    return {
        "data": {
            "chat_tabs": [
                {"tab_id": tab_id, "tab_name": name, "tab_type": kind, "tab_content": {kind: "https://z"}}
                for tab_id, name, kind in specs
            ]
        }
    }


@pytest.mark.asyncio
async def test_list_tabs_uses_list_tabs_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_tabs_reply(("tab_1", "周报", "doc"), ("tab_2", "Pin", "pin"))])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.list_chat_tabs_impl("oc_x")
    assert seq.request.uri == "/open-apis/im/v1/chats/:chat_id/chat_tabs/list_tabs"
    assert seq.request.http_method.name == "GET"
    # Built-in tabs are listed too — a tab_id from here is not necessarily deletable.
    assert [t["type"] for t in result["tabs"]] == ["doc", "pin"]
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_add_tab_builds_content_keyed_by_type(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_tabs_reply(("tab_1", "周报", "doc"))])
    monkeypatch.setattr(_impl, "_invoke", seq)
    await _impl.add_chat_tab_impl("oc_x", "周报", "doc", "https://feishu.cn/docx/abc")
    assert seq.request.uri == "/open-apis/im/v1/chats/:chat_id/chat_tabs"
    assert seq.request.body == {
        "chat_tabs": [{"tab_name": "周报", "tab_type": "doc", "tab_content": {"doc": "https://feishu.cn/docx/abc"}}]
    }


@pytest.mark.asyncio
async def test_add_tab_refuses_read_only_types_and_bad_links(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([])
    monkeypatch.setattr(_impl, "_invoke", seq)
    # Nine of Feishu's eleven tab types are built-in; asking for one must not become an
    # opaque parameter error.
    for kind in ("pin", "meeting_minute", "task", "images_videos"):
        refused = await _impl.add_chat_tab_impl("oc_x", "x", kind, "https://y")
        assert refused["ok"] is False, kind
        assert "只能读不能建" in refused["message"]

    assert (await _impl.add_chat_tab_impl("oc_x", "  ", "url", "https://y"))["ok"] is False
    assert (await _impl.add_chat_tab_impl("oc_x", "x" * 61, "url", "https://y"))["ok"] is False
    assert (await _impl.add_chat_tab_impl("oc_x", "x", "url", ""))["ok"] is False
    assert (await _impl.add_chat_tab_impl("oc_x", "x", "url", "feishu.cn/x"))["ok"] is False
    assert seq.requests == []


@pytest.mark.asyncio
async def test_delete_tabs_and_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_tabs_reply()])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.delete_chat_tabs_impl("oc_x", ["tab_1"])
    assert seq.request.uri == "/open-apis/im/v1/chats/:chat_id/chat_tabs/delete_tabs"
    assert seq.request.body == {"tab_ids": ["tab_1"]}
    assert result["deleted"] == ["tab_1"]

    assert (await _impl.delete_chat_tabs_impl("oc_x", []))["ok"] is False

    monkeypatch.setattr(_impl, "_invoke", _failing(232046))
    assert "20 个" in (await _impl.add_chat_tab_impl("oc_x", "x", "url", "https://y"))["hint"]


@pytest.mark.asyncio
async def test_upload_avatar_uses_avatar_image_type(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The whole reason this is separate from upload_image_impl: a message-type key
    # uploads fine and is then rejected by the chat update with 232021.
    picture = tmp_path / "logo.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    seq = _Sequenced([{"data": {"image_key": "img_avatar"}}])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.upload_chat_avatar_impl(str(picture))
    assert seq.request.uri == "/open-apis/im/v1/images"
    assert seq.request.body["image_type"] == "avatar"
    assert result["image_key"] == "img_avatar"


@pytest.mark.asyncio
async def test_upload_avatar_rejects_non_images(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doc = tmp_path / "notes.txt"
    doc.write_text("hi", encoding="utf-8")
    seq = _Sequenced([])
    monkeypatch.setattr(_impl, "_invoke", seq)
    assert (await _impl.upload_chat_avatar_impl(str(doc)))["ok"] is False
    assert (await _impl.upload_chat_avatar_impl(str(tmp_path / "missing.png")))["ok"] is False
    assert seq.requests == []


# ── 会话列表 (chat list) ────────────────────────────────────────────────────────


def _chat_page(*names: str, has_more: bool = False, token: str = "") -> dict[str, Any]:
    return {
        "data": {
            "items": [
                {
                    "chat_id": f"oc_{position}",
                    "name": name,
                    "owner_id": "ou_owner" if position else "",
                    "chat_status": "normal",
                    "external": False,
                }
                for position, name in enumerate(names)
            ],
            "has_more": has_more,
            "page_token": token,
        }
    }


@pytest.mark.asyncio
async def test_list_chats_bot_uses_tenant_and_creation_order(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_chat_page("研发群", "产品群")])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.list_chats_impl()
    req = seq.request
    assert req.uri == "/open-apis/im/v1/chats"
    # Creation order on purpose: Feishu warns that paging an activity-ordered list can
    # skip groups as the order shifts underfoot.
    assert _qdict(req).get("sort_type") == "ByCreateTimeAsc"
    assert seq.kwargs[0]["prefer"] == "tenant"
    assert result["count"] == 2
    # A missing owner_id means a bot owns it, not that the group has no owner.
    assert result["chats"][0]["owner_is_bot"] is True
    assert result["chats"][1]["owner_is_bot"] is False
    assert result["chats"][0]["status_label"] == "正常"


@pytest.mark.asyncio
async def test_list_chats_me_requires_user_key_and_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_chat_page("我的群")])
    monkeypatch.setattr(_impl, "_invoke", seq)
    # "我在哪些群" answered with the bot's groups is a wrong answer that looks right —
    # so whose="me" must switch the token, and needs to know who is asking.
    missing = await _impl.list_chats_impl("me")
    assert missing["ok"] is False
    assert "sender_open_id" in missing["message"]
    assert seq.requests == []

    result = await _impl.list_chats_impl("me", user_key="ou_me")
    assert seq.kwargs[0]["prefer"] == "user"
    assert seq.kwargs[0]["user_key"] == "ou_me"
    assert result["whose"] == "me"

    assert (await _impl.list_chats_impl("everyone"))["ok"] is False


@pytest.mark.asyncio
async def test_list_chats_pages_and_reports_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_chat_page("a", "b", has_more=True, token="pt1"), _chat_page("c")])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.list_chats_impl(limit=100)
    assert _qdict(seq.requests[1]).get("page_token") == "pt1"
    assert result["count"] == 3
    assert result["truncated"] is False

    # A limit smaller than what exists must say so rather than look complete.
    seq = _Sequenced([_chat_page("a", "b", has_more=True, token="pt1")])
    monkeypatch.setattr(_impl, "_invoke", seq)
    capped = await _impl.list_chats_impl(limit=2)
    assert capped["count"] == 2
    assert capped["truncated"] is True


# ── 消息搜索 (message search) ────────────────────────────────────────────────────


def _hit_page(*ids: str, has_more: bool = False, token: str = "") -> dict[str, Any]:
    """Feishu answers with message ids only — no text, no sender, no chat."""
    return {"data": {"items": list(ids), "has_more": has_more, "page_token": token}}


def _message_reply(text: str, chat_id: str = "oc_1") -> dict[str, Any]:
    return {
        "data": {
            "items": [
                {
                    "message_id": "om_1",
                    "chat_id": chat_id,
                    "sender": {"id": "ou_a", "sender_type": "user"},
                    "create_time": "1700000000",
                    "body": {"content": json.dumps({"text": text}, ensure_ascii=False), "message_type": "text"},
                }
            ]
        }
    }


@pytest.mark.asyncio
async def test_search_messages_hydrates_ids_into_readable_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_hit_page("om_1"), _message_reply("发布时间定在周五")])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.search_messages_impl("发布时间", user_key="ou_me")

    search_req, hydrate_req = seq.requests
    assert search_req.http_method.name == "POST"
    assert search_req.uri == "/open-apis/search/v2/message"
    assert search_req.body == {"query": "发布时间"}
    # User-token only: Feishu accepts no tenant token here at all.
    assert seq.kwargs[0]["prefer"] == "user"
    assert hydrate_req.uri == "/open-apis/im/v1/messages/:message_id"
    # A search result the agent can't read is useless, so the text comes back.
    assert result["messages"][0]["text"] == "发布时间定在周五"
    assert result["messages"][0]["chat_id"] == "oc_1"
    assert result["messages"][0]["readable"] is True
    assert result["unreadable"] == 0


@pytest.mark.asyncio
async def test_search_messages_keeps_unreadable_hits_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Mixed:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, request: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 1:
                return {"ok": True, "data": {"items": ["om_1", "om_2"], "has_more": False}}
            if self.calls == 2:
                return {"ok": True, **_message_reply("能读到")}
            return {"ok": False, "code": 230002, "message": "bot not in chat"}

    monkeypatch.setattr(_impl, "_invoke", _Mixed())
    result = await _impl.search_messages_impl("x", user_key="ou_me")
    # A hit in a chat the bot isn't in is normal; dropping it would hide the gap.
    assert result["count"] == 2
    assert result["unreadable"] == 1
    assert result["messages"][1]["readable"] is False
    assert result["messages"][1]["message_id"] == "om_2"


@pytest.mark.asyncio
async def test_search_messages_requires_user_key_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([])
    monkeypatch.setattr(_impl, "_invoke", seq)
    no_key = await _impl.search_messages_impl("x")
    assert no_key["ok"] is False
    assert "sender_open_id" in no_key["message"]
    assert (await _impl.search_messages_impl("  ", user_key="ou_me"))["ok"] is False
    assert seq.requests == []


@pytest.mark.asyncio
async def test_search_messages_validates_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([])
    monkeypatch.setattr(_impl, "_invoke", seq)
    # message_type filters by *attachment* kind; "text" would silently match nothing.
    bad_type = await _impl.search_messages_impl("x", message_type="text", user_key="ou_me")
    assert bad_type["ok"] is False
    assert "file" in bad_type["message"]

    assert (await _impl.search_messages_impl("x", from_type="robot", user_key="ou_me"))["ok"] is False
    assert (await _impl.search_messages_impl("x", chat_type="group", user_key="ou_me"))["ok"] is False

    # Milliseconds are the mistake that costs an hour: accepted by Feishu, matches nothing.
    ms = await _impl.search_messages_impl("x", start_time="1700000000000", user_key="ou_me")
    assert ms["ok"] is False
    assert "秒级" in ms["message"]
    assert (await _impl.search_messages_impl("x", end_time="last week", user_key="ou_me"))["ok"] is False
    assert seq.requests == []


@pytest.mark.asyncio
async def test_search_messages_passes_named_filters_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _Sequenced([_hit_page(), _message_reply("x")])
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.search_messages_impl(
        "周报",
        chat_ids=["oc_1", " "],
        from_ids=["ou_a"],
        message_type="image",
        from_type="user",
        chat_type="group_chat",
        start_time="1609296809",
        end_time="1609396809",
        user_key="ou_me",
    )
    assert seq.requests[0].body == {
        "query": "周报",
        "chat_ids": ["oc_1"],
        "from_ids": ["ou_a"],
        "message_type": "image",
        "from_type": "user",
        "chat_type": "group_chat",
        "start_time": "1609296809",
        "end_time": "1609396809",
    }
    # No hits: nothing to hydrate, so the second queued response goes unused.
    assert result["count"] == 0
    assert len(seq.requests) == 1


@pytest.mark.asyncio
async def test_search_messages_hints_missing_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_invoke", _failing(99991663))
    hint = (await _impl.search_messages_impl("x", user_key="ou_me"))["hint"]
    assert "本人身份" in hint


# ── Tool layer: every new tool is async, documented, and returns JSON ────────────


@pytest.mark.asyncio
async def test_chat_tools_return_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_chat")
    stubs = {
        "read_chat_announcement_impl": {"ok": True, "text": "值班表"},
        "set_chat_announcement_impl": {"ok": True, "added": 2},
        "clear_chat_announcement_impl": {"ok": True, "deleted": 3},
        "update_chat_impl": {"ok": True, "updated": {"name": "新群名"}},
        "transfer_chat_owner_impl": {"ok": True, "new_owner_id": "ou_new"},
        "dismiss_chat_impl": {"ok": True, "dismissed": True},
        "update_chat_moderation_impl": {"ok": True, "moderation_setting": "only_owner"},
        "get_chat_menu_impl": {"ok": True, "count": 1},
        "add_chat_menu_impl": {"ok": True, "count": 2},
        "delete_chat_menu_impl": {"ok": True, "deleted": ["top_0"]},
        "list_chat_tabs_impl": {"ok": True, "count": 4},
        "add_chat_tab_impl": {"ok": True, "count": 5},
        "delete_chat_tabs_impl": {"ok": True, "deleted": ["tab_1"]},
        "list_chats_impl": {"ok": True, "count": 7},
        "upload_chat_avatar_impl": {"ok": True, "image_key": "img_a"},
    }
    for name, payload in stubs.items():
        monkeypatch.setattr(_impl, name, lambda *a, _p=payload, **k: _async(_p))

    assert json.loads(await mod.feishu_chat_announcement("oc_x"))["text"] == "值班表"
    assert json.loads(await mod.feishu_chat_announcement_set("oc_x", "x"))["added"] == 2
    assert json.loads(await mod.feishu_chat_announcement_clear("oc_x"))["deleted"] == 3
    assert json.loads(await mod.feishu_chat_update("oc_x", name="新群名"))["updated"]["name"] == "新群名"
    assert json.loads(await mod.feishu_chat_transfer_owner("oc_x", "ou_new"))["new_owner_id"] == "ou_new"
    assert json.loads(await mod.feishu_chat_dismiss("oc_x", "解散群"))["dismissed"] is True
    assert json.loads(await mod.feishu_chat_mute("oc_x", "全员禁言"))["moderation_setting"] == "only_owner"
    assert json.loads(await mod.feishu_chat_menu_get("oc_x"))["count"] == 1
    assert json.loads(await mod.feishu_chat_menu_add("oc_x", [{"name": "x"}]))["count"] == 2
    assert json.loads(await mod.feishu_chat_menu_delete("oc_x", ["top_0"]))["deleted"] == ["top_0"]
    assert json.loads(await mod.feishu_chat_tabs("oc_x"))["count"] == 4
    assert json.loads(await mod.feishu_chat_tab_add("oc_x", "周报", "doc", "https://y"))["count"] == 5
    assert json.loads(await mod.feishu_chat_tab_delete("oc_x", ["tab_1"]))["deleted"] == ["tab_1"]
    assert json.loads(await mod.feishu_chat_list())["count"] == 7
    assert json.loads(await mod.feishu_chat_upload_avatar("a.png"))["image_key"] == "img_a"


@pytest.mark.asyncio
async def test_message_search_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_message")
    monkeypatch.setattr(_impl, "search_messages_impl", lambda *a, **k: _async({"ok": True, "count": 3}))
    assert json.loads(await mod.feishu_message_search("x", user_key="ou_me"))["count"] == 3


def test_new_tools_are_async_and_documented() -> None:
    """Every tool the registry exposes must be async with a docstring, or it isn't usable."""
    chat = importlib.import_module("feishu_chat")
    message = importlib.import_module("feishu_message")
    expected = [
        (chat, name)
        for name in (
            "feishu_chat_list",
            "feishu_chat_announcement",
            "feishu_chat_announcement_set",
            "feishu_chat_announcement_clear",
            "feishu_chat_update",
            "feishu_chat_upload_avatar",
            "feishu_chat_mute",
            "feishu_chat_transfer_owner",
            "feishu_chat_dismiss",
            "feishu_chat_menu_get",
            "feishu_chat_menu_add",
            "feishu_chat_menu_delete",
            "feishu_chat_tabs",
            "feishu_chat_tab_add",
            "feishu_chat_tab_delete",
        )
    ]
    expected.append((message, "feishu_message_search"))
    for mod, name in expected:
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (fn.__doc__ or "").strip(), name
