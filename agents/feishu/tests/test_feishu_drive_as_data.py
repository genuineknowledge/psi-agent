"""Parity: the ``feishu-drive`` skill reaches Feishu the same way the tools did.

Sixth domain through the migration, after ``contact``, ``chat``, ``message``, ``bitable``
and ``approval``. Same standard of proof: build the request through the generic
``feishu_api`` path driven by ``skills/feishu-drive/SKILL.md``, and compare it against
what the deleted tool sent.

Drive is where the split runs along a different seam than in any earlier domain: not
"forwarder vs orchestrator" but **read vs write**. The three reads are plain GETs (plus
one DELETE whose only argument is a type), and they are tabled. The two comment *writes*
are not, and the reason is the body shape: posting a comment is
``reply_list.replies[].content.elements[]`` — three levels deep, with the text buried in
an array element. ``_present`` looks a field up by top-level key and cannot index into an
array, so tabling those would leave the comment text itself with no check at all. Feishu
accepts a malformed ``elements`` and answers ``code: 0``, so the comment lands empty and
the call reports success. That is the silent-failure class this migration refuses to
table, so ``feishu_drive_add_comment`` / ``_reply_comment`` stay tools and this file pins
the ``hard: true`` rules that keep the generic path from reaching around them.

The other two kept tools are the domain's two ends of the same impossibility. Download
produces **a file on disk**, not a JSON response — ``download_file_impl`` never calls
``_invoke`` at all; it goes straight to ``client.arequest``, pulls bytes out of the raw
response and writes them. Upload needs a real file handle **in the body**, which a JSON
string cannot express. Both directions are unreachable for a generic caller, so both are
hard refusals, and this file proves the generic path refuses rather than builds.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from lark_channel.core.enum import HttpMethod
from lark_channel.core.model import BaseRequest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_spec: Any = importlib.import_module("_feishu_spec")
_api: Any = importlib.import_module("_feishu_api_impl")
_impl: Any = importlib.import_module("_feishu_impl")

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
DRIVE_SKILL = SKILLS_DIR / "feishu-drive" / "SKILL.md"

#: Every drive endpoint declares both TENANT and USER as candidates; ``prefer`` picks the
#: send path. See ``test_token_candidates_are_declared_for_every_endpoint``.
WAS_TOKENS = {"TENANT", "USER"}

# The wire contract of the 3 tools this skill replaced, captured mechanically by running
# each builder at migration time and frozen here. The builders are gone — keeping them
# alive only to be a test reference would be keeping dead production code, which is what
# this migration exists to remove. These literals are what Feishu received before.
WAS: dict[str, dict[str, Any]] = {
    "list_comments": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/drive/v1/files/:file_token/comments",
        "paths": {"file_token": "doccnFT1"},
        "queries": [("file_type", "docx"), ("is_whole", "true"), ("page_size", "50")],
        "body": None,
    },
    "list_comments_paged": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/drive/v1/files/:file_token/comments",
        "paths": {"file_token": "doccnFT1"},
        "queries": [("file_type", "docx"), ("is_whole", "true"), ("page_size", "20"), ("page_token", "tok1")],
        "body": None,
    },
    "list_replies": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies",
        "paths": {"file_token": "doccnFT1", "comment_id": "CMT1"},
        "queries": [("file_type", "docx"), ("page_size", "50")],
        "body": None,
    },
    "list_replies_paged": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies",
        "paths": {"file_token": "doccnFT1", "comment_id": "CMT1"},
        "queries": [("file_type", "docx"), ("page_size", "20"), ("page_token", "tok1")],
        "body": None,
    },
    "delete_file": {
        "method": HttpMethod.DELETE,
        "uri": "/open-apis/drive/v1/files/:file_token",
        "paths": {"file_token": "doccnFT1"},
        "queries": [("type", "docx")],
        "body": None,
    },
    "delete_folder": {
        "method": HttpMethod.DELETE,
        "uri": "/open-apis/drive/v1/files/:file_token",
        "paths": {"file_token": "fldrABC"},
        "queries": [("type", "folder")],
        "body": None,
    },
}

# The file-management endpoints added for the 云文档与云盘 gap list. These never had a
# tool, so there is no deleted builder to compare against — the frozen shape here is the
# request the *official documentation* describes, field for field, which is what the
# generic path has to produce for the call to work at all.
WAS_NEW: dict[str, dict[str, Any]] = {
    "batch_meta": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/drive/v1/metas/batch_query",
        "paths": {},
        "queries": [],
        "body": {"request_docs": [{"doc_token": "doccnFT1", "doc_type": "docx"}], "with_url": True},
    },
    "list_folder": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/drive/v1/files",
        "paths": {},
        "queries": [("folder_token", "fldrABC"), ("order_by", "EditedTime"), ("page_size", "100")],
        "body": None,
    },
    "create_folder": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/drive/v1/files/create_folder",
        "paths": {},
        "queries": [],
        "body": {"name": "季度报告", "folder_token": "fldrABC"},
    },
    "copy_file": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/drive/v1/files/:file_token/copy",
        "paths": {"file_token": "doccnFT1"},
        "queries": [],
        "body": {"name": "副本", "type": "docx", "folder_token": "fldrABC"},
    },
    "move_file": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/drive/v1/files/:file_token/move",
        "paths": {"file_token": "doccnFT1"},
        "queries": [],
        "body": {"type": "docx", "folder_token": "fldrABC"},
    },
    "task_check": {
        "method": HttpMethod.GET,
        "uri": "/open-apis/drive/v1/files/task_check",
        "paths": {},
        "queries": [("task_id", "7360595374803812356")],
        "body": None,
    },
    "create_shortcut": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/drive/v1/files/create_shortcut",
        "paths": {},
        "queries": [],
        "body": {"parent_token": "fldrABC", "refer_entity": {"refer_token": "doccnFT1", "refer_type": "docx"}},
    },
    "wiki_rename": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/wiki/v2/spaces/:space_id/nodes/:node_token/update_title",
        "paths": {"space_id": "SPC1", "node_token": "wikcnND1"},
        "queries": [],
        "body": {"title": "新标题"},
    },
}
WAS.update(WAS_NEW)

#: The four endpoints that keep a dedicated tool, and the shape the *tool* sent. Kept here
#: so the refusal tests can prove the generic path never builds these requests at all.
#: Note how much structure the two comment writes carry — that is the argument for keeping
#: them, written out.
KEPT: dict[str, dict[str, Any]] = {
    "add_comment": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/drive/v1/files/:file_token/comments",
        "paths": {"file_token": "doccnFT1"},
        "queries": [("file_type", "docx")],
        "body": {
            "reply_list": {
                "replies": [{"content": {"elements": [{"type": "text_run", "text_run": {"text": "hello"}}]}}]
            }
        },
    },
    "reply_plain": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies",
        "paths": {"file_token": "doccnFT1", "comment_id": "CMT1"},
        "queries": [("file_type", "docx")],
        "body": {"content": {"elements": [{"type": "text_run", "text_run": {"text": "ok"}}]}},
    },
    "reply_at": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies",
        "paths": {"file_token": "doccnFT1", "comment_id": "CMT1"},
        "queries": [("file_type", "docx")],
        # The person node goes FIRST; the text run follows. Order is what makes the
        # @-mention render, and getting it wrong still returns code 0.
        "body": {
            "content": {
                "elements": [
                    {"type": "person", "person": {"user_id": "ou_a"}},
                    {"type": "text_run", "text_run": {"text": "ok"}},
                ]
            }
        },
    },
    "upload": {
        "method": HttpMethod.POST,
        "uri": "/open-apis/drive/v1/medias/upload_all",
        "paths": {},
        "queries": [],
        # ``file`` holds a ``_NamedBytes`` — an ``io.IOBase`` subclass, because the SDK
        # decides "this is multipart" by finding one in the body. This is the fact a JSON
        # argument cannot carry.
        "body": {"file_name": "v.mp4", "parent_type": "explorer", "parent_node": "fldrABC", "size": "7"},
    },
}

#: How each frozen shape is asked for through the generic tool. Keys match ``WAS``.
CALLS: dict[str, dict[str, Any]] = {
    "list_comments": {
        "method": "GET",
        "uri": "/open-apis/drive/v1/files/:file_token/comments",
        "paths": {"file_token": "doccnFT1"},
        "query": {"file_type": "docx", "is_whole": "true"},
    },
    "list_comments_paged": {
        "method": "GET",
        "uri": "/open-apis/drive/v1/files/:file_token/comments",
        "paths": {"file_token": "doccnFT1"},
        "query": {"file_type": "docx", "is_whole": "true", "page_size": 20, "page_token": "tok1"},
    },
    "list_replies": {
        "method": "GET",
        "uri": "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies",
        "paths": {"file_token": "doccnFT1", "comment_id": "CMT1"},
        "query": {"file_type": "docx"},
    },
    "list_replies_paged": {
        "method": "GET",
        "uri": "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies",
        "paths": {"file_token": "doccnFT1", "comment_id": "CMT1"},
        "query": {"file_type": "docx", "page_size": 20, "page_token": "tok1"},
    },
    "delete_file": {
        "method": "DELETE",
        "uri": "/open-apis/drive/v1/files/:file_token",
        "paths": {"file_token": "doccnFT1"},
        "query": {"type": "docx"},
    },
    "delete_folder": {
        "method": "DELETE",
        "uri": "/open-apis/drive/v1/files/:file_token",
        "paths": {"file_token": "fldrABC"},
        "query": {"type": "folder"},
    },
    "batch_meta": {
        "method": "POST",
        "uri": "/open-apis/drive/v1/metas/batch_query",
        "body": {"request_docs": [{"doc_token": "doccnFT1", "doc_type": "docx"}], "with_url": True},
    },
    # ``page_size`` is not passed: the rule's default supplies it, which is what makes a
    # table row a full replacement for a tool that hardcoded one.
    "list_folder": {
        "method": "GET",
        "uri": "/open-apis/drive/v1/files",
        "query": {"folder_token": "fldrABC", "order_by": "EditedTime"},
    },
    "create_folder": {
        "method": "POST",
        "uri": "/open-apis/drive/v1/files/create_folder",
        "body": {"name": "季度报告", "folder_token": "fldrABC"},
    },
    "copy_file": {
        "method": "POST",
        "uri": "/open-apis/drive/v1/files/:file_token/copy",
        "paths": {"file_token": "doccnFT1"},
        "body": {"name": "副本", "type": "docx", "folder_token": "fldrABC"},
    },
    "move_file": {
        "method": "POST",
        "uri": "/open-apis/drive/v1/files/:file_token/move",
        "paths": {"file_token": "doccnFT1"},
        "body": {"type": "docx", "folder_token": "fldrABC"},
    },
    "task_check": {
        "method": "GET",
        "uri": "/open-apis/drive/v1/files/task_check",
        "query": {"task_id": "7360595374803812356"},
    },
    "create_shortcut": {
        "method": "POST",
        "uri": "/open-apis/drive/v1/files/create_shortcut",
        "body": {"parent_token": "fldrABC", "refer_entity": {"refer_token": "doccnFT1", "refer_type": "docx"}},
    },
    "wiki_rename": {
        "method": "POST",
        "uri": "/open-apis/wiki/v2/spaces/:space_id/nodes/:node_token/update_title",
        "paths": {"space_id": "SPC1", "node_token": "wikcnND1"},
        "body": {"title": "新标题"},
    },
}

#: Endpoints whose rule declares ``paginate``, so one call drains pages.
PAGED = {"list_comments", "list_comments_paged", "list_replies", "list_replies_paged", "list_folder"}


def _sent(req: BaseRequest) -> dict[str, Any]:
    """Normalize a live request so it can be compared with a frozen shape."""
    return {
        "method": req.http_method,
        "uri": req.uri,
        "paths": dict(req.paths or {}),
        "queries": sorted((k, str(v)) for k, v in (req.queries or [])),
        "body": req.body,
    }


def _want(label: str) -> dict[str, Any]:
    """One frozen wire shape, normalized the same way ``_sent`` normalizes a live one."""
    want = dict(WAS[label])
    want["queries"] = sorted(want["queries"])
    return want


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
    pages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> tuple[_CapturedInvoke, dict[str, Any]]:
    cap = _CapturedInvoke(pages)
    monkeypatch.setattr(_impl, "_invoke", cap)
    out: dict[str, Any] = anyio.run(lambda: _api.call_api_impl(**kwargs))
    return cap, out


#: Paged endpoints whose items live under a key other than ``items``. Listing a folder is
#: the domain's one such endpoint — ``paginate: {items: files}`` — and getting this key
#: wrong is not a crash: the loop reads an empty list, decides there is nothing more, and
#: returns zero files for a folder that is full.
PAGE_ITEMS_KEY = {"list_folder": "files"}


def _pages_for(label: str) -> list[dict[str, Any]] | None:
    """A single terminal page, under whichever key this endpoint's rule declares."""
    if label not in PAGED:
        return None
    return [{"ok": True, "data": {PAGE_ITEMS_KEY.get(label, "items"): [], "has_more": False}}]


def _call(monkeypatch: pytest.MonkeyPatch, label: str, **overrides: Any) -> tuple[_CapturedInvoke, dict[str, Any]]:
    """Invoke one ``CALLS`` entry through the generic path."""
    spec = {**CALLS[label], **overrides}
    return _generic(
        monkeypatch,
        pages=_pages_for(label),
        method=spec["method"],
        uri=spec["uri"],
        paths_json=json.dumps(spec.get("paths", {})),
        body_json=json.dumps(spec.get("body", {}), ensure_ascii=False),
        query_json=json.dumps(spec.get("query", {})),
        confirm=spec.get("confirm", ""),
    )


def _rules() -> list[Any]:
    return _spec.parse_rules(DRIVE_SKILL.read_text(encoding="utf-8"))


def _rule(method: str, uri: str) -> Any:
    match = [r for r in _rules() if r.method == method and r.uri == uri]
    assert len(match) == 1, f"expected exactly one {method} {uri} rule, got {len(match)}"
    return match[0]


# ------------------------------------------------------------------ the skill parses


def test_skill_declares_every_migrated_endpoint() -> None:
    """Every tabled endpoint has a rule, and the only extras are the tool sign-posts."""
    got = {(r.method, r.uri) for r in _rules()}
    tabled = {(WAS[k]["method"].name, WAS[k]["uri"]) for k in WAS}
    assert tabled <= got, f"tabled endpoint missing a rule: {tabled - got}"
    assert got - tabled == {
        ("POST", "/open-apis/drive/v1/files/:file_token/comments"),
        ("POST", "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies"),
        ("POST", "/open-apis/drive/v1/medias/upload_all"),
        ("POST", "/open-apis/drive/v1/files/upload_all"),
        ("GET", "/open-apis/drive/v1/medias/:file_token/download"),
        # Downloading a Drive resource file and the three export steps: all four produce
        # bytes rather than JSON, so they exist as rules only to name the tool.
        ("GET", "/open-apis/drive/v1/files/:file_token/download"),
        ("POST", "/open-apis/drive/v1/export_tasks"),
        ("GET", "/open-apis/drive/v1/export_tasks/:ticket"),
        ("GET", "/open-apis/drive/v1/export_tasks/file/:file_token/download"),
    }


def test_every_rule_has_a_documented_row() -> None:
    """A rule the Markdown never mentions is a rule the model will never pick."""
    text = DRIVE_SKILL.read_text(encoding="utf-8")
    table = text.split("```rules")[0]
    for rule in _rules():
        assert rule.uri in table, f"{rule.endpoint} is enforced but undocumented"


# ------------------------------------------------------- wire parity vs the builders


@pytest.mark.parametrize("label", sorted(WAS))
def test_generic_path_matches_the_deleted_builder(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Field for field, the request the skill builds is the request the tool sent."""
    cap, out = _call(monkeypatch, label)
    assert out.get("ok") is not False, out
    assert cap.requests, f"{label}: no request was built"
    assert _sent(cap.requests[0]) == _want(label)


@pytest.mark.parametrize("label", sorted(WAS))
def test_token_candidates_are_declared_for_every_endpoint(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    """Both types are always declared; ``prefer`` selects the send path, not this set.

    The SDK's ``verify()`` picks the token type whose token is actually present on the
    ``RequestOption``, so narrowing to one type makes the request unsendable as the other
    identity. Every drive endpoint here works under either.
    """
    cap, _ = _call(monkeypatch, label)
    got = {str(t).split(".")[-1] for t in (cap.requests[0].token_types or set())}
    assert got == WAS_TOKENS


# ----------------------------------------------------------- the writes stay refused

#: Endpoint → the tool that must be named instead, for every ``hard: true`` rule.
KEPT_TOOLS = [
    ("POST", "/open-apis/drive/v1/files/:file_token/comments", "feishu_drive_add_comment"),
    (
        "POST",
        "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies",
        "feishu_drive_reply_comment",
    ),
    ("POST", "/open-apis/drive/v1/medias/upload_all", "feishu_drive_upload"),
    ("POST", "/open-apis/drive/v1/files/upload_all", "feishu_drive_upload"),
    ("GET", "/open-apis/drive/v1/medias/:file_token/download", "feishu_file_download"),
    ("GET", "/open-apis/drive/v1/files/:file_token/download", "feishu_file_download"),
    ("POST", "/open-apis/drive/v1/export_tasks", "feishu_doc_export"),
    ("GET", "/open-apis/drive/v1/export_tasks/:ticket", "feishu_doc_export"),
    ("GET", "/open-apis/drive/v1/export_tasks/file/:file_token/download", "feishu_doc_export"),
]

#: Which module each hard-refused endpoint's tool actually lives in, so the "still exists"
#: check reads the right file rather than assuming one module per domain.
TOOL_MODULES = {
    "feishu_drive_add_comment": "feishu_drive.py",
    "feishu_drive_reply_comment": "feishu_drive.py",
    "feishu_drive_upload": "feishu_drive.py",
    "feishu_file_download": "feishu_drive.py",
    "feishu_doc_export": "feishu_doc_export.py",
}


@pytest.mark.parametrize(("method", "uri", "tool"), KEPT_TOOLS)
def test_kept_tool_endpoint_refuses_and_names_the_tool(
    monkeypatch: pytest.MonkeyPatch, method: str, uri: str, tool: str
) -> None:
    """A hard rule must refuse *before* building, and say what to use instead.

    ``cap.requests == []`` is the load-bearing assertion: a warning attached to a
    successful-looking result would be indistinguishable from success, which for the
    comment writes means an empty comment posted with ``code: 0``.
    """
    # Every placeholder any of these endpoints declares, so the refusal is reached rather
    # than pre-empted by ``missing_path_params``.
    paths = {"file_token": "doccnFT1", "comment_id": "CMT1", "ticket": "TKT1"}
    cap, out = _generic(
        monkeypatch,
        method=method,
        uri=uri,
        paths_json=json.dumps(paths),
        body_json="{}",
        query_json=json.dumps({"file_type": "docx"}),
    )
    assert out.get("ok") is False, out
    assert out.get("code") == "use_dedicated_tool", out
    assert tool in json.dumps(out, ensure_ascii=False), out
    assert cap.requests == [], "a hard refusal must not reach _invoke"


@pytest.mark.parametrize(("method", "uri", "tool"), KEPT_TOOLS)
def test_kept_tool_rule_says_why(method: str, uri: str, tool: str) -> None:
    """A refusal without a reason reads as arbitrary, and gets worked around."""
    rule = _rule(method, uri)
    assert rule.prefer_tool == tool
    assert rule.prefer_hard is True
    assert rule.why.strip(), f"{method} {uri}: a hard refusal without a reason"


def test_kept_tools_still_exist() -> None:
    """A hard rule pointing at a deleted tool would be a dead end with no way forward."""
    for _, _, tool in KEPT_TOOLS:
        module = TOOLS_DIR / TOOL_MODULES[tool]
        source = module.read_text(encoding="utf-8")
        assert f"async def {tool}(" in source, f"{tool} is named by a hard rule but no longer exists in {module.name}"


def test_uploads_are_refused_by_the_generic_tool_itself() -> None:
    """The two ``upload_all`` endpoints were already blocked before this skill existed.

    ``_UPLOAD_ENDPOINTS`` in the generic tool is the older, broader guard: it covers the
    ``im`` uploads too. The skill's rules restate it for drive so the reason travels with
    the endpoint table, but the block does not depend on the skill being loaded.
    """
    assert "/open-apis/drive/v1/medias/upload_all" in _api._UPLOAD_ENDPOINTS
    assert "/open-apis/drive/v1/files/upload_all" in _api._UPLOAD_ENDPOINTS


# --------------------------------------------------- refusals stay in their own lane


def test_listing_comments_is_not_swallowed_by_the_post_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same URI, different method: ``GET .../comments`` must stay reachable.

    ``POST .../comments`` is hard-refused. A rule that matched on path alone would make
    reading a document's discussion impossible — which is the domain's most common read.
    """
    cap, out = _call(monkeypatch, "list_comments")
    assert out.get("ok") is not False, out
    assert cap.requests, "listing comments was refused by the create rule"
    assert cap.requests[0].http_method == HttpMethod.GET


def test_listing_replies_is_not_swallowed_by_the_comments_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    """The replies GET sits two segments below the hard-refused comments POST.

    ``Rule.matches`` is segment-wise *prefix* matching, so ``POST .../comments`` is a
    prefix of ``.../comments/:comment_id/replies``. Specificity ordering plus the advice
    downgrade is what keeps the parent's authority from leaking down.
    """
    cap, out = _call(monkeypatch, "list_replies")
    assert out.get("ok") is not False, out
    assert cap.requests, "listing replies was refused by a parent rule"
    assert cap.requests[0].uri == WAS["list_replies"]["uri"]


def test_deleting_is_not_swallowed_by_the_upload_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """``DELETE /files/:file_token`` vs the hard-refused ``POST /files/upload_all``.

    Both live under ``/open-apis/drive/v1/files``. ``upload_all`` is a literal segment
    and ``:file_token`` is a placeholder, so a literal-first ordering bug would send a
    delete into the upload refusal.
    """
    cap, out = _call(monkeypatch, "delete_file")
    assert out.get("ok") is not False, out
    assert cap.requests, "deleting was refused by the upload rule"
    assert cap.requests[0].http_method == HttpMethod.DELETE


# ------------------------------------------------------------------------- validation


def test_listing_comments_without_file_type_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``file_type`` is required: Feishu answers "not found" rather than guessing."""
    cap, out = _call(monkeypatch, "list_comments", query={"is_whole": "true"})
    assert out.get("ok") is False, out
    assert out.get("code") == "spec_violation", out
    assert "file_type" in json.dumps(out, ensure_ascii=False)
    assert cap.requests == [], "validation must run before the request is built"


def test_deleting_without_a_type_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old impl made ``file_type`` a checked argument; the rule keeps it required."""
    cap, out = _call(monkeypatch, "delete_file", query={})
    assert out.get("ok") is False, out
    assert out.get("code") == "spec_violation", out
    assert cap.requests == []


def test_deleting_with_an_unknown_type_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_DELETABLE_FILE_TYPES`` was a Python set; it is now the rule's ``choices``."""
    cap, out = _call(monkeypatch, "delete_file", query={"type": "docx_image"})
    assert out.get("ok") is False, out
    assert out.get("code") == "spec_violation", out
    assert cap.requests == []


def test_every_deletable_type_the_impl_allowed_is_still_allowed() -> None:
    """The choices list must be the old set exactly — narrower silently breaks callers."""
    rule = _rule("DELETE", "/open-apis/drive/v1/files/:file_token")
    assert set(rule.fields["type"]["choices"]) == {
        "file",
        "docx",
        "doc",
        "sheet",
        "bitable",
        "mindnote",
        "slides",
        "folder",
        "shortcut",
    }


# ----------------------------------------------------------------------------- paging


def test_comment_paging_drains_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """``paginate`` is a generic capability; the deleted tool returned one page."""
    pages = [
        {"ok": True, "data": {"items": [{"comment_id": "c1"}], "has_more": True, "page_token": "p2"}},
        {"ok": True, "data": {"items": [{"comment_id": "c2"}], "has_more": False}},
    ]
    cap, out = _generic(
        monkeypatch,
        pages=pages,
        method="GET",
        uri="/open-apis/drive/v1/files/:file_token/comments",
        paths_json=json.dumps({"file_token": "doccnFT1"}),
        query_json=json.dumps({"file_type": "docx", "is_whole": "true"}),
    )
    assert out.get("ok") is not False, out
    assert len(cap.requests) == 2, "the second page was not fetched"
    assert out["count"] == 2
    assert [c["comment_id"] for c in out["items"]] == ["c1", "c2"]


def test_paging_carries_the_token_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page token that is not sent means page 2 is a re-read of page 1, forever."""
    pages = [
        {"ok": True, "data": {"items": [], "has_more": True, "page_token": "p2"}},
        {"ok": True, "data": {"items": [], "has_more": False}},
    ]
    cap, _ = _generic(
        monkeypatch,
        pages=pages,
        method="GET",
        uri="/open-apis/drive/v1/files/:file_token/comments",
        paths_json=json.dumps({"file_token": "doccnFT1"}),
        query_json=json.dumps({"file_type": "docx"}),
    )
    second = dict(cap.requests[1].queries or [])
    assert second.get("page_token") == "p2"


def test_page_size_over_the_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refused, not silently clamped — the same decision the other domains made."""
    cap, out = _call(monkeypatch, "list_comments", query={"file_type": "docx", "page_size": 500})
    assert out.get("ok") is False, out
    assert out.get("code") == "spec_violation", out
    assert cap.requests == []


# --------------------------------------------------------------- knowledge that moved


#: Facts that lived in Python (or in a docstring) and now live only in the skill. If a
#: rewrite drops one, the model loses it silently — nothing else in the repo asserts them.
PITFALL_FACTS = [
    "is_whole",
    "is_solved",
    "reply_id",
    "task_id",
    "obj_token",
    "user_key",
]


@pytest.mark.parametrize("fact", PITFALL_FACTS)
def test_pitfall_text_survives(fact: str) -> None:
    """Each fact must appear in the executable rules block, not only in the prose."""
    text = DRIVE_SKILL.read_text(encoding="utf-8")
    rules_block = text.split("```rules")[1]
    assert fact in rules_block, f"{fact} is no longer carried by any rule"


def test_folder_deletion_being_async_stays_documented() -> None:
    """Deleting a folder returns a ``task_id`` and is not done when the call returns.

    The old impl surfaced ``task_id`` from the response. The generic path returns the
    whole response, so the field still arrives — but *what it means* was only in Python.
    """
    text = DRIVE_SKILL.read_text(encoding="utf-8")
    assert "task_id" in text
    assert "异步" in text


def test_the_read_write_split_is_explained() -> None:
    """Why half this domain is tabled and half is not — the model has to know the seam."""
    text = DRIVE_SKILL.read_text(encoding="utf-8")
    assert "elements" in text
    assert "静默" in text


def test_download_and_upload_reasons_stay_documented() -> None:
    """Both impossibilities, and the ~12h approval link that motivates one of them."""
    text = DRIVE_SKILL.read_text(encoding="utf-8")
    assert "12 小时" in text
    assert "20MB" in text
    assert "文件句柄" in text
