"""Tests for the Haitun workspace ``github`` toolset PR-review tools.

These never touch the network. They monkeypatch ``_resolve_token_async`` (to
supply a fake token) and ``_gh_request`` (to return canned API payloads or record
the calls made), then assert on request assembly (paths, media types, bodies),
response shaping, pagination, comment routing (inline vs top-level), and the
validation/error branches — all OS- and network-independent.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

tool: Any = importlib.import_module("github")


@pytest.fixture()
def gh(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch token resolution and _gh_request; record calls, script responses.

    Set ``state["responses"]`` to a list consumed FIFO per _gh_request call.
    Read ``state["calls"]`` for the (method, path, kwargs) of each call made.
    """
    state: dict[str, Any] = {"calls": [], "responses": []}

    async def fake_token() -> str | None:
        return state.get("token", "fake-token")

    async def fake_request(method: str, path: str, token: str, **kwargs: Any) -> dict[str, Any]:
        state["calls"].append({"method": method, "path": path, "kwargs": kwargs})
        if state["responses"]:
            return state["responses"].pop(0)
        return {"ok": True, "status": 200, "data": {}, "next_url": None}

    monkeypatch.setattr(tool, "_resolve_token_async", fake_token)
    monkeypatch.setattr(tool, "_gh_request", fake_request)
    return state


def _ok(data: Any, next_url: str | None = None) -> dict[str, Any]:
    return {"ok": True, "status": 200, "data": data, "next_url": next_url}


# ── review_pull_request ──────────────────────────────────────────────────────


async def test_review_pull_request_shapes_overview_and_files(gh: dict[str, Any]) -> None:
    gh["responses"] = [
        _ok(
            {
                "number": 7,
                "title": "Add feature",
                "body": "desc",
                "state": "open",
                "draft": False,
                "user": {"login": "alice"},
                "base": {"ref": "main"},
                "head": {"ref": "feat", "sha": "abc123"},
                "mergeable": True,
                "mergeable_state": "clean",
                "changed_files": 1,
                "additions": 10,
                "deletions": 2,
                "commits": 3,
                "html_url": "https://github.com/o/r/pull/7",
            }
        ),
        _ok(
            [{"filename": "a.py", "status": "modified", "additions": 10, "deletions": 2, "changes": 12, "patch": "@@"}]
        ),
    ]
    out = json.loads(await tool.review_pull_request("o", "r", 7))
    assert out["ok"] is True
    assert out["pull"]["title"] == "Add feature"
    assert out["pull"]["head_sha"] == "abc123"
    assert out["files"][0]["filename"] == "a.py"
    # include_patch defaults to False, so patch is omitted.
    assert "patch" not in out["files"][0]
    assert gh["calls"][0]["path"] == "/repos/o/r/pulls/7"


async def test_review_pull_request_include_patch(gh: dict[str, Any]) -> None:
    gh["responses"] = [
        _ok({"number": 1, "head": {"sha": "s"}}),
        _ok([{"filename": "a.py", "patch": "@@ -1 +1 @@"}]),
    ]
    out = json.loads(await tool.review_pull_request("o", "r", 1, include_patch=True))
    assert out["files"][0]["patch"] == "@@ -1 +1 @@"


async def test_review_pull_request_skip_files(gh: dict[str, Any]) -> None:
    gh["responses"] = [_ok({"number": 1, "head": {"sha": "s"}})]
    out = json.loads(await tool.review_pull_request("o", "r", 1, include_files=False))
    assert "files" not in out
    # Only the PR fetch happened, no /files call.
    assert len(gh["calls"]) == 1


async def test_review_pull_request_api_error(gh: dict[str, Any]) -> None:
    gh["responses"] = [{"ok": False, "status": 404, "message": "GitHub API HTTP 404: Not Found"}]
    out = json.loads(await tool.review_pull_request("o", "r", 999))
    assert out["ok"] is False
    assert out["status"] == 404


# ── get_pull_request_diff ────────────────────────────────────────────────────


async def test_get_diff_requests_diff_media_type(gh: dict[str, Any]) -> None:
    gh["responses"] = [_ok("diff --git a b\n@@")]
    out = json.loads(await tool.get_pull_request_diff("o", "r", 3))
    assert out["ok"] is True
    assert out["diff"].startswith("diff --git")
    assert out["truncated"] is False
    assert gh["calls"][0]["kwargs"]["accept"] == "application/vnd.github.v3.diff"


async def test_get_diff_truncates(gh: dict[str, Any]) -> None:
    gh["responses"] = [_ok("x" * 100)]
    out = json.loads(await tool.get_pull_request_diff("o", "r", 3, max_chars=10))
    assert out["truncated"] is True
    assert out["length"] == 10


# ── list_pull_request_comments ───────────────────────────────────────────────


async def test_list_comments_all_streams(gh: dict[str, Any]) -> None:
    gh["responses"] = [
        _ok([{"id": 1, "user": {"login": "bob"}, "path": "a.py", "line": 5, "diff_hunk": "@@", "body": "nit"}]),
        _ok([{"id": 2, "user": {"login": "amy"}, "body": "LGTM"}]),
    ]
    out = json.loads(await tool.list_pull_request_comments("o", "r", 4))
    assert out["review_comments"][0]["path"] == "a.py"
    assert out["issue_comments"][0]["body"] == "LGTM"
    assert gh["calls"][0]["path"] == "/repos/o/r/pulls/4/comments"
    assert gh["calls"][1]["path"] == "/repos/o/r/issues/4/comments"


async def test_list_comments_review_only(gh: dict[str, Any]) -> None:
    gh["responses"] = [_ok([{"id": 1, "body": "x"}])]
    out = json.loads(await tool.list_pull_request_comments("o", "r", 4, kind="review"))
    assert "review_comments" in out
    assert "issue_comments" not in out
    assert len(gh["calls"]) == 1


async def test_list_comments_bad_kind(gh: dict[str, Any]) -> None:
    out = json.loads(await tool.list_pull_request_comments("o", "r", 4, kind="bogus"))
    assert out["ok"] is False
    assert "kind must be" in out["message"]


async def test_list_comments_paginates(gh: dict[str, Any]) -> None:
    # First page points to a next_url; second page ends pagination.
    gh["responses"] = [
        _ok([{"id": 1, "body": "p1"}], next_url="https://api.github.com/next"),
        _ok([{"id": 2, "body": "p2"}]),
    ]
    out = json.loads(await tool.list_pull_request_comments("o", "r", 4, kind="review"))
    assert [c["id"] for c in out["review_comments"]] == [1, 2]
    assert out["review_comments_truncated"] is False


# ── add_pull_request_comment ─────────────────────────────────────────────────


async def test_add_top_level_comment(gh: dict[str, Any]) -> None:
    gh["responses"] = [_ok({"id": 55, "html_url": "https://github.com/o/r/pull/4#c55"})]
    out = json.loads(await tool.add_pull_request_comment("o", "r", 4, "great work"))
    assert out["ok"] is True
    assert out["kind"] == "issue"
    assert out["comment"]["id"] == 55
    call = gh["calls"][0]
    assert call["method"] == "POST"
    assert call["path"] == "/repos/o/r/issues/4/comments"
    assert call["kwargs"]["json_body"] == {"body": "great work"}


async def test_add_inline_comment_with_commit_id(gh: dict[str, Any]) -> None:
    gh["responses"] = [_ok({"id": 77, "html_url": "u"})]
    out = json.loads(
        await tool.add_pull_request_comment("o", "r", 4, "fix this", path="a.py", line=12, commit_id="deadbeef")
    )
    assert out["kind"] == "inline"
    body = gh["calls"][0]["kwargs"]["json_body"]
    assert body["path"] == "a.py"
    assert body["line"] == 12
    assert body["side"] == "RIGHT"
    assert body["commit_id"] == "deadbeef"
    assert gh["calls"][0]["path"] == "/repos/o/r/pulls/4/comments"


async def test_add_inline_comment_resolves_head_sha(gh: dict[str, Any]) -> None:
    # No commit_id → tool first GETs the PR to resolve head sha, then POSTs.
    gh["responses"] = [
        _ok({"number": 4, "head": {"sha": "resolved-sha"}}),
        _ok({"id": 78, "html_url": "u"}),
    ]
    out = json.loads(await tool.add_pull_request_comment("o", "r", 4, "note", path="a.py", line=3))
    assert out["ok"] is True
    assert gh["calls"][0]["method"] == "GET"
    assert gh["calls"][1]["kwargs"]["json_body"]["commit_id"] == "resolved-sha"


async def test_add_comment_empty_body_rejected(gh: dict[str, Any]) -> None:
    out = json.loads(await tool.add_pull_request_comment("o", "r", 4, "   "))
    assert out["ok"] is False
    assert "body is required" in out["message"]


async def test_add_inline_requires_line(gh: dict[str, Any]) -> None:
    out = json.loads(await tool.add_pull_request_comment("o", "r", 4, "x", path="a.py", line=0))
    assert out["ok"] is False
    assert "line must be > 0" in out["message"]


async def test_add_inline_bad_side(gh: dict[str, Any]) -> None:
    out = json.loads(await tool.add_pull_request_comment("o", "r", 4, "x", path="a.py", line=1, side="MIDDLE"))
    assert out["ok"] is False
    assert 'side must be "RIGHT" or "LEFT"' in out["message"]


# ── auth + schema ────────────────────────────────────────────────────────────


async def test_no_token_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_token() -> str | None:
        return None

    monkeypatch.setattr(tool, "_resolve_token_async", no_token)
    for out in (
        json.loads(await tool.review_pull_request("o", "r", 1)),
        json.loads(await tool.get_pull_request_diff("o", "r", 1)),
        json.loads(await tool.list_pull_request_comments("o", "r", 1)),
        json.loads(await tool.add_pull_request_comment("o", "r", 1, "hi")),
    ):
        assert out["ok"] is False
        assert "No GitHub token found" in out["message"]


def test_resolve_token_prefers_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "gh-tok")
    monkeypatch.setenv("GITHUB_TOKEN", "gitHub-tok")
    assert tool._resolve_token() == "gh-tok"
    monkeypatch.delenv("GH_TOKEN")
    assert tool._resolve_token() == "gitHub-tok"


@pytest.mark.parametrize(
    ("name", "required"),
    [
        ("review_pull_request", ["owner", "repo", "number"]),
        ("get_pull_request_diff", ["owner", "repo", "number"]),
        ("list_pull_request_comments", ["owner", "repo", "number"]),
        ("add_pull_request_comment", ["owner", "repo", "number", "body"]),
    ],
)
def test_tools_register_as_valid_toolfunctions(name: str, required: list[str]) -> None:
    tf = ToolFunction.from_callable(getattr(tool, name))
    assert tf.name == name
    assert tf.description
    assert tf.parameters["required"] == required
