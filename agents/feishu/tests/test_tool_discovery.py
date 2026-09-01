"""Tests for the Haitun workspace tool-discovery meta-tools.

Covers ``_tool_index`` (static AST scan) and the ``tool_search`` /
``tool_search_code`` / ``tool_describe`` tools built on top of it.
"""

from __future__ import annotations

import base64
import builtins
import hashlib
import importlib
import inspect
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import httpx
import pytest

from psi_agent.session.tool_registry import ToolFunction, ToolRegistry

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_idx: Any = importlib.import_module("_tool_index")
tool_search: Any = importlib.import_module("tool_search").tool_search
tool_search_code: Any = importlib.import_module("tool_search_code").tool_search_code
tool_describe: Any = importlib.import_module("tool_describe").tool_describe


# ── _tool_index against the real tools/ dir ──────────────────────────────────


async def test_index_finds_known_tools_and_skips_private_files():
    metas = await _idx.index_tools()
    names = {m.name for m in metas}
    # Known public tools are indexed.
    assert "find_files" in names
    assert "fetch" in names
    # The three discovery tools index themselves.
    assert {"tool_search", "tool_search_code", "tool_describe"} <= names
    assert {
        "assignment_upsert",
        "assignment_get",
        "assignment_list",
        "assignment_transition",
        "assignment_feedback",
        "assignment_send_card",
        "assignment_accept",
        "assignment_delivery_refresh",
        "organization_memory_add",
    } <= names
    # Private helper files (``_fetch_impl.py``) never expose a tool.
    assert "fetch_impl" not in names
    assert all(not n.startswith("_") for n in names)


async def test_assignment_read_tools_are_replayable():
    source = await (anyio.Path(str(TOOLS_DIR)) / "_fusion_memory_mcp.py").read_text(encoding="utf-8")
    read_tools = source.split("READ_TOOLS", 1)[1].split("}", 1)[0]
    assert '"assignment_get"' in read_tools
    assert '"assignment_list"' in read_tools
    assert '"assignment_upsert"' not in read_tools


async def test_memory_read_tools_route_one_explicit_visibility(monkeypatch):
    memory = _MemoryStub(
        memory_search=[{"ok": True}, {"ok": True}],
        memory_answer_context=[{"ok": True}],
    )
    search_module = _import_memory_module("memory_search", memory, monkeypatch)
    context_module = _import_memory_module("memory_answer_context", memory, monkeypatch)

    personal = json.loads(await search_module.memory_search("release plan"))
    organization = json.loads(await search_module.memory_search("release plan", visibility="organization"))
    invalid = json.loads(await context_module.memory_answer_context("release plan", visibility="both"))

    assert personal["ok"] is True
    assert organization["ok"] is True
    assert invalid["error"]["code"] == "invalid_argument"
    assert memory.calls == [("memory_search", {"query": "release plan", "limit": 8, "visibility": "personal"}, True)]
    assert memory.organization_read_calls == [
        ("memory_search", {"query": "release plan", "limit": 8, "visibility": "organization"}, True)
    ]
    search_schema = ToolFunction.from_callable(search_module.memory_search).parameters["properties"]
    context_schema = ToolFunction.from_callable(context_module.memory_answer_context).parameters["properties"]
    assert search_schema["visibility"]["enum"] == ["personal", "organization"]
    assert context_schema["visibility"]["enum"] == ["personal", "organization"]


async def test_organization_memory_add_exposes_fact_fields_without_identity(monkeypatch):
    memory = _MemoryStub(organization_memory_add=[{"ok": True, "result": {"deduplicated": False}}])
    module = _import_memory_module("organization_memory_add", memory, monkeypatch)

    signature = inspect.signature(module.organization_memory_add)
    assert "organization_id" not in signature.parameters
    assert "actor_user_id" not in signature.parameters
    assert "feishu_open_id" not in signature.parameters
    schema = ToolFunction.from_callable(module.organization_memory_add).parameters["properties"]
    assert schema["category"]["enum"] == [
        "project_context",
        "decision",
        "status",
        "process",
        "constraint",
        "shared_reference",
    ]
    assert schema["source_type"]["enum"] == [
        "feishu_message",
        "feishu_doc",
        "repository",
        "task",
        "other",
    ]
    assert "standalone" in schema["content"]["description"]
    assert "evidence" in schema["source_ref"]["description"]
    assert "ISO-8601" in schema["observed_at"]["description"]
    assert "replaces" in schema["supersedes_fact_id"]["description"]

    result = json.loads(
        await module.organization_memory_add(
            content="The project release window is Monday.",
            category="decision",
            source_type="feishu_doc",
            source_ref="https://example.invalid/doc",
            project="Project Alpha",
            tags=["release", "decision"],
        )
    )

    assert result["ok"] is True
    assert memory.calls == []
    name, arguments, retryable = memory.organization_write_calls[0]
    assert name == "organization_memory_add"
    assert retryable is False
    assert arguments == {
        "content": "The project release window is Monday.",
        "category": "decision",
        "source_type": "feishu_doc",
        "source_ref": "https://example.invalid/doc",
        "project": "Project Alpha",
        "observed_at": None,
        "supersedes_fact_id": None,
        "tags": ["release", "decision"],
    }


async def test_organization_write_router_refreshes_stale_membership_once(monkeypatch):
    mcp_module = importlib.import_module("_fusion_memory_mcp")
    router = mcp_module.MemoryMcpRouter(SimpleNamespace())
    sync_calls: list[tuple[str, bool]] = []
    tool_calls: list[tuple[str, str]] = []

    async def sync_membership(_config, open_id: str, *, force: bool = False):
        sync_calls.append((open_id, force))
        return {"ok": True}

    async def call_for_session(session_id: str, name: str, arguments: dict[str, Any], *, retryable: bool):
        del arguments, retryable
        tool_calls.append((session_id, name))
        if len(tool_calls) == 1:
            return {"ok": False, "error": {"code": "organization_membership_stale", "retryable": True}}
        return {"ok": True}

    monkeypatch.setattr(mcp_module, "sync_current_membership", sync_membership)
    monkeypatch.setattr(router, "call_tool_for_session", call_for_session)
    monkeypatch.setattr(mcp_module, "get_session_id", lambda: "feishu-ou_member1")

    result = await router.call_organization_write_tool("memory_search", {"query": "project"}, retryable=True)

    assert result == {"ok": True}
    assert sync_calls == [("ou_member1", True), ("ou_member1", True)]
    assert tool_calls == [
        ("feishu-ou_member1", "memory_search"),
        ("feishu-ou_member1", "memory_search"),
    ]


async def test_organization_write_router_rechecks_cached_member_before_memory_access(monkeypatch):
    mcp_module = importlib.import_module("_fusion_memory_mcp")
    router = mcp_module.MemoryMcpRouter(SimpleNamespace())
    tool_calls: list[str] = []

    async def sync_membership(_config, _open_id: str, *, force: bool = False):
        if force:
            return {
                "ok": False,
                "error": {
                    "code": "organization_access_denied",
                    "message": "Organization access is not available",
                    "retryable": False,
                },
            }
        return {"ok": True, "cached": True}

    async def call_for_session(_session_id: str, name: str, _arguments: dict[str, Any], *, retryable: bool):
        del retryable
        tool_calls.append(name)
        return {"ok": True}

    monkeypatch.setattr(mcp_module, "sync_current_membership", sync_membership)
    monkeypatch.setattr(router, "call_tool_for_session", call_for_session)
    monkeypatch.setattr(mcp_module, "get_session_id", lambda: "feishu-ou_former_member")

    result = await router.call_organization_write_tool("memory_search", {"query": "project"}, retryable=True)

    assert result["error"]["code"] == "organization_access_denied"
    assert tool_calls == []


@pytest.mark.parametrize(
    ("auto_register", "replay_result", "expected_replays"),
    [
        (True, {"ok": True, "result": {"status": "ok"}}, 1),
        (True, {"ok": False, "error": {"code": "organization_required", "retryable": False}}, 1),
        (False, {"ok": True}, 0),
    ],
)
async def test_organization_write_router_reregisters_at_most_once(
    monkeypatch,
    auto_register,
    replay_result,
    expected_replays,
):
    mcp_module = importlib.import_module("_fusion_memory_mcp")
    router = mcp_module.MemoryMcpRouter(SimpleNamespace(auto_register_feishu=auto_register))
    required = {"ok": False, "error": {"code": "organization_required", "retryable": False}}
    replay_calls: list[str] = []

    async def sync_membership(_config, _open_id: str, *, force: bool = False):
        assert force is True
        return {"ok": True}

    async def call_for_session(_session_id: str, _name: str, _arguments: dict[str, Any], *, retryable: bool):
        del retryable
        return required

    async def replay(session_id: str, _name: str, _arguments: dict[str, Any], *, retryable: bool):
        del retryable
        replay_calls.append(session_id)
        return replay_result

    monkeypatch.setattr(mcp_module, "sync_current_membership", sync_membership)
    monkeypatch.setattr(router, "call_tool_for_session", call_for_session)
    monkeypatch.setattr(router, "_call_with_refreshed_registration", replay)
    monkeypatch.setattr(mcp_module, "get_session_id", lambda: "feishu-ou_member1")

    result = await router.call_organization_write_tool("memory_search", {"query": "project"}, retryable=True)

    assert result == (replay_result if expected_replays else required)
    assert replay_calls == ["feishu-ou_member1"] * expected_replays


async def test_organization_read_router_skips_membership_sync(monkeypatch):
    mcp_module = importlib.import_module("_fusion_memory_mcp")
    router = mcp_module.MemoryMcpRouter(SimpleNamespace(auto_register_feishu=False))
    tool_calls: list[str] = []

    async def unexpected_sync(*_args, **_kwargs):
        raise AssertionError("organization reads must not require writer-group membership")

    async def call_for_session(_session_id: str, name: str, _arguments: dict[str, Any], *, retryable: bool):
        del retryable
        tool_calls.append(name)
        return {"ok": True}

    monkeypatch.setattr(mcp_module, "sync_current_membership", unexpected_sync)
    monkeypatch.setattr(router, "call_tool_for_session", call_for_session)
    monkeypatch.setattr(mcp_module, "get_session_id", lambda: "feishu-ou_reader1")

    result = await router.call_organization_read_tool("memory_search", {"query": "project"}, retryable=True)

    assert result == {"ok": True}
    assert tool_calls == ["memory_search"]


async def test_registration_refresh_returns_safe_configuration_error(monkeypatch):
    mcp_module = importlib.import_module("_fusion_memory_mcp")
    router = mcp_module.MemoryMcpRouter(SimpleNamespace(auto_register_feishu=True))

    async def reject_refresh(_session_id: str, _config: Any):
        raise mcp_module.MemoryConfigError(
            "registration_failed",
            "Fusion Memory registration failed",
            retryable=True,
        )

    monkeypatch.setattr(mcp_module, "refresh_feishu_registration", reject_refresh)

    result = await router._call_with_refreshed_registration("feishu-ou_member1", "memory_search", {}, retryable=True)

    assert result == {
        "ok": False,
        "error": {
            "code": "registration_failed",
            "message": "Fusion Memory registration failed",
            "retryable": True,
        },
    }


async def test_memory_router_reregisters_once_after_unauthorized(monkeypatch):
    mcp_module = importlib.import_module("_fusion_memory_mcp")
    config = SimpleNamespace(auto_register_feishu=True)
    router = mcp_module.MemoryMcpRouter(config)
    stale = SimpleNamespace(token="stale-token")
    fresh = SimpleNamespace(token="fresh-token")
    calls: list[tuple[str, str]] = []

    class Client:
        def __init__(self, token: str) -> None:
            self.token = token

        async def call_tool(self, name: str, _arguments: dict[str, Any], *, retryable: bool):
            del retryable
            calls.append((self.token, name))
            if self.token == "stale-token":
                return mcp_module._TransportAuthRejectedResult(
                    {"ok": False, "error": {"code": "unauthorized", "retryable": False}}
                )
            return {"ok": True, "result": {"status": "ok"}}

    async def resolve(_session_id: str, _config: Any):
        return stale

    refresh_calls: list[str] = []

    async def refresh(session_id: str, _config: Any):
        refresh_calls.append(session_id)
        return fresh

    def client_for(_session_id: str, resolved: Any):
        return Client(resolved.token), None

    monkeypatch.setattr(mcp_module, "resolve_memory_config", resolve)
    monkeypatch.setattr(mcp_module, "refresh_feishu_registration", refresh)
    monkeypatch.setattr(router, "_client_for", client_for)

    result = await router.call_tool_for_session("feishu-ou_a", "memory_health", {}, retryable=True)

    assert result == {"ok": True, "result": {"status": "ok"}}
    assert refresh_calls == ["feishu-ou_a"]
    assert calls == [("stale-token", "memory_health"), ("fresh-token", "memory_health")]


async def test_memory_client_marks_http_auth_rejection_out_of_band():
    mcp_module = importlib.import_module("_fusion_memory_mcp")
    client = mcp_module.MemoryMcpClient("https://memory.example.invalid/mcp", "stale-token")
    request = mcp_module._Request("memory_add", {"content": "one"}, False)

    class Session:
        async def call_tool(self, _name: str, _arguments: dict[str, Any]):
            http_request = httpx.Request("POST", "https://memory.example.invalid/mcp")
            response = httpx.Response(401, request=http_request)
            raise httpx.HTTPStatusError("unauthorized", request=http_request, response=response)

    async def connect():
        return Session()

    async def disconnect():
        return None

    result = await client._execute(request, connect, disconnect)

    assert isinstance(result, mcp_module._TransportAuthRejectedResult)
    assert result == {
        "ok": False,
        "error": {"code": "unauthorized", "message": "Fusion Memory authentication failed", "retryable": False},
    }


async def test_memory_router_does_not_replay_tool_level_unauthorized(monkeypatch):
    mcp_module = importlib.import_module("_fusion_memory_mcp")
    config = SimpleNamespace(auto_register_feishu=True)
    router = mcp_module.MemoryMcpRouter(config)
    resolved = SimpleNamespace(token="current-token")
    calls: list[str] = []

    class Client:
        async def call_tool(self, name: str, _arguments: dict[str, Any], *, retryable: bool):
            del retryable
            calls.append(name)
            return {
                "ok": False,
                "error": {"code": "unauthorized", "retryable": False},
                "_fusion_memory_transport_auth_rejected": True,
            }

    async def resolve(_session_id: str, _config: Any):
        return resolved

    async def unexpected_refresh(_session_id: str, _config: Any):
        raise AssertionError("tool-level unauthorized must not refresh credentials")

    monkeypatch.setattr(mcp_module, "resolve_memory_config", resolve)
    monkeypatch.setattr(mcp_module, "refresh_feishu_registration", unexpected_refresh)
    monkeypatch.setattr(router, "_client_for", lambda _session_id, _resolved: (Client(), None))

    result = await router.call_tool_for_session("feishu-ou_a", "memory_add", {"content": "one"}, retryable=False)

    assert result == {
        "ok": False,
        "error": {"code": "unauthorized", "retryable": False},
        "_fusion_memory_transport_auth_rejected": True,
    }
    assert calls == ["memory_add"]


async def test_memory_activation_logs_safe_configuration_error(monkeypatch):
    mcp_module = importlib.import_module("_fusion_memory_mcp")
    router = mcp_module.MemoryMcpRouter(SimpleNamespace())
    warnings: list[tuple[object, ...]] = []

    async def reject(_session_id: str, _config: Any):
        raise mcp_module.MemoryConfigError(
            "registration_credentials_incomplete",
            "secret-value must never be logged",
        )

    monkeypatch.setattr(mcp_module, "resolve_memory_config", reject)
    monkeypatch.setattr(mcp_module, "get_session_id", lambda: "feishu-ou_a")
    monkeypatch.setattr(mcp_module.logger, "warning", lambda *args: warnings.append(args))

    result = await router.activate_current_session(WORKSPACE_ROOT)

    assert result["error"]["code"] == "registration_credentials_incomplete"
    assert warnings == [("Fusion Memory activation skipped: {}", "registration_credentials_incomplete")]
    assert "secret-value" not in json.dumps(warnings)


async def test_membership_assertion_reflects_group_roster(monkeypatch):
    impl = types.ModuleType("_feishu_impl")

    async def list_members(_chat_id: str):
        return {"ok": True, "members": [{"id": "ou_member1", "name": "Member One"}]}

    impl.__dict__["list_chat_members_impl"] = list_members
    monkeypatch.setitem(sys.modules, "_feishu_impl", impl)
    sys.modules.pop("_fusion_memory_membership", None)
    membership = importlib.import_module("_fusion_memory_membership")
    config = SimpleNamespace(
        organization_id="org-one",
        organization_chat_id="oc_group",
        feishu_app_id="cli_app",
        feishu_app_secret="secret-value",
    )

    active = await membership.build_current_assertion(config, "ou_member1")
    disabled = await membership.build_current_assertion(config, "ou_outsider")
    active_payload = json.loads(base64.urlsafe_b64decode(active.encode("ascii")))["payload"]
    disabled_payload = json.loads(base64.urlsafe_b64decode(disabled.encode("ascii")))["payload"]

    assert active_payload["membership_status"] == "active"
    assert disabled_payload["membership_status"] == "disabled"
    assert active_payload["source_group_id"] == "oc_group"
    assert active_payload["organization_id"] == "org-one"


async def test_membership_sync_posts_once_until_refresh(monkeypatch):
    impl = types.ModuleType("_feishu_impl")
    roster_calls: list[str] = []

    async def list_members(chat_id: str):
        roster_calls.append(chat_id)
        return {"ok": True, "members": [{"id": "ou_member1"}]}

    impl.__dict__["list_chat_members_impl"] = list_members
    monkeypatch.setitem(sys.modules, "_feishu_impl", impl)
    sys.modules.pop("_fusion_memory_membership", None)
    membership = importlib.import_module("_fusion_memory_membership")
    membership._SYNC_CACHE.clear()
    posts: list[str] = []

    async def post_assertion(_config, assertion: str):
        posts.append(assertion)
        return {"ok": True}

    monkeypatch.setattr(membership, "_post_membership_assertion", post_assertion)
    config = SimpleNamespace(
        url="https://memory.example.invalid/mcp",
        timeout_seconds=3,
        organization_id="org-one",
        organization_chat_id="oc_group",
        feishu_app_id="cli_app",
        feishu_app_secret="secret-value",
    )

    first = await membership.sync_current_membership(config, "ou_member1")
    cached = await membership.sync_current_membership(config, "ou_member1")
    refreshed = await membership.sync_current_membership(config, "ou_member1", force=True)

    assert first == {"ok": True, "cached": False}
    assert cached == {"ok": True, "cached": True}
    assert refreshed == {"ok": True, "cached": False}
    assert roster_calls == ["oc_group", "oc_group"]
    assert len(posts) == 2


async def test_membership_sync_records_revocation_and_denies_access(monkeypatch):
    impl = types.ModuleType("_feishu_impl")

    async def list_members(_chat_id: str):
        return {"ok": True, "members": []}

    impl.__dict__["list_chat_members_impl"] = list_members
    monkeypatch.setitem(sys.modules, "_feishu_impl", impl)
    sys.modules.pop("_fusion_memory_membership", None)
    membership = importlib.import_module("_fusion_memory_membership")
    membership._SYNC_CACHE.clear()
    statuses: list[str] = []

    async def post_assertion(_config, assertion: str):
        statuses.append(json.loads(base64.urlsafe_b64decode(assertion.encode("ascii")))["payload"]["membership_status"])
        return {"ok": True}

    monkeypatch.setattr(membership, "_post_membership_assertion", post_assertion)
    config = SimpleNamespace(
        url="https://memory.example.invalid/mcp",
        timeout_seconds=3,
        organization_id="org-one",
        organization_chat_id="oc_group",
        feishu_app_id="cli_app",
        feishu_app_secret="secret-value",
    )

    result = await membership.sync_current_membership(config, "ou_outsider")

    assert result["error"]["code"] == "organization_access_denied"
    assert result["error"]["retryable"] is False
    assert statuses == ["disabled"]


async def test_membership_sync_hides_unexpected_feishu_failure(monkeypatch):
    impl = types.ModuleType("_feishu_impl")

    async def list_members(_chat_id: str):
        raise RuntimeError("transport internals and secret-value")

    impl.__dict__["list_chat_members_impl"] = list_members
    monkeypatch.setitem(sys.modules, "_feishu_impl", impl)
    sys.modules.pop("_fusion_memory_membership", None)
    membership = importlib.import_module("_fusion_memory_membership")
    membership._SYNC_CACHE.clear()
    config = SimpleNamespace(
        url="https://memory.example.invalid/mcp",
        timeout_seconds=3,
        organization_id="org-one",
        organization_chat_id="oc_group",
        feishu_app_id="cli_app",
        feishu_app_secret="secret-value",
    )

    result = await membership.sync_current_membership(config, "ou_member1")

    assert result["error"] == {
        "code": "organization_membership_unverified",
        "message": "Organization membership could not be verified",
        "retryable": True,
    }
    assert "secret-value" not in json.dumps(result)


async def test_membership_sync_classifies_remote_http_status(monkeypatch):
    impl = types.ModuleType("_feishu_impl")

    async def list_members(_chat_id: str):
        return {"ok": True, "members": [{"id": "ou_member1"}]}

    impl.__dict__["list_chat_members_impl"] = list_members
    monkeypatch.setitem(sys.modules, "_feishu_impl", impl)
    sys.modules.pop("_fusion_memory_membership", None)
    membership = importlib.import_module("_fusion_memory_membership")
    membership._SYNC_CACHE.clear()
    config = SimpleNamespace(
        url="https://memory.example.invalid/mcp",
        timeout_seconds=3,
        organization_id="org-one",
        organization_chat_id="oc_group",
        feishu_app_id="cli_app",
        feishu_app_secret="secret-value",
    )

    async def reject(_config, _assertion: str):
        request = httpx.Request("POST", "https://memory.example.invalid/feishu/membership")
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("secret-value", request=request, response=response)

    monkeypatch.setattr(membership, "_post_membership_assertion", reject)

    rejected = await membership.sync_current_membership(config, "ou_member1")

    assert rejected["error"] == {
        "code": "organization_membership_rejected",
        "message": "Organization membership proof was rejected",
        "retryable": False,
    }
    assert "secret-value" not in json.dumps(rejected)

    async def unavailable(_config, _assertion: str):
        request = httpx.Request("POST", "https://memory.example.invalid/feishu/membership")
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("secret-value", request=request, response=response)

    monkeypatch.setattr(membership, "_post_membership_assertion", unavailable)

    retryable = await membership.sync_current_membership(config, "ou_member1")

    assert retryable["error"] == {
        "code": "organization_membership_unverified",
        "message": "Organization membership could not be verified",
        "retryable": True,
    }
    assert "secret-value" not in json.dumps(retryable)


def test_memory_config_reads_organization_chat_id():
    config_module = importlib.import_module("_fusion_memory_config")
    config = config_module.build_memory_config(
        {
            "FUSION_MEMORY_MCP_URL": "https://memory.example.invalid/mcp",
            "FUSION_MEMORY_ORGANIZATION_ID": "org-one",
            "FUSION_MEMORY_FEISHU_ORGANIZATION_CHAT_ID": "oc_group",
        }
    )

    assert config.organization_id == "org-one"
    assert config.organization_chat_id == "oc_group"


async def test_assignment_upsert_binds_session_identity_and_normalizes_fields(monkeypatch):
    memory = _MemoryStub(
        assignment_upsert=[{"ok": True, "result": {"assignment_id": "wa-1"}}],
    )
    feishu = _FeishuStub()
    module = _import_assignment_module("assignment_upsert", memory, monkeypatch, feishu=feishu)

    result = json.loads(
        await module.assignment_upsert(
            json.dumps(
                {
                    "title": "整理会议结论",
                    "assigner": {"user_id": "untrusted", "display_name": "消息上下文姓名"},
                    "recipients": [{"user_id": "recipient"}],
                    "gaps": ["截止时间待确认"],
                    "risks": ["不要把推测写成事实"],
                    "action_items": ["提交方案"],
                    "evidence_refs": ["https://example.com/source"],
                },
                ensure_ascii=False,
            )
        )
    )

    assert result["ok"] is True
    forwarded = memory.calls[0][1]["assignment"]
    assert forwarded["assigner"] == {
        "user_id": "ou_assigner",
        "display_name": "通讯录安排者",
        "feishu_open_id": "ou_assigner",
    }
    assert forwarded["gaps"] == [{"description": "截止时间待确认"}]
    assert forwarded["risks"] == [{"description": "不要把推测写成事实"}]
    assert forwarded["action_items"] == [{"description": "提交方案"}]
    assert forwarded["evidence_refs"] == [{"uri": "https://example.com/source"}]


async def test_assignment_modules_export_only_their_declared_tools(tmp_path):
    module_names = ("assignment_upsert", "assignment_send_card", "assignment_feedback")
    for module_name in module_names:
        source = await anyio.Path(TOOLS_DIR / f"{module_name}.py").read_text(encoding="utf-8")
        await anyio.Path(tmp_path / f"{module_name}.py").write_text(source, encoding="utf-8")

    loaded = await ToolRegistry._load_from_dir(tmp_path, "assignment-export-test")

    for module_name in module_names:
        entry = loaded[str(tmp_path / f"{module_name}.py")]
        assert set(entry.tools) == {module_name}


async def test_assignment_upsert_omits_model_name_when_directory_lookup_fails(monkeypatch):
    async def unavailable(*_args, **_kwargs):
        return {"ok": False, "error": {"code": "forbidden"}}

    module = _import_assignment_module("assignment_upsert", _MemoryStub(), monkeypatch)
    monkeypatch.setattr(module, "_get_users_batch_impl", unavailable)
    assignment = {"assigner": {"user_id": "untrusted", "display_name": "模型猜测姓名"}}

    await module._bind_assigner_to_current_feishu_session(assignment)

    assert assignment["assigner"] == {
        "user_id": "ou_assigner",
        "feishu_open_id": "ou_assigner",
    }


async def test_assignment_feedback_validates_before_memory_calls(monkeypatch):
    memory = _MemoryStub()
    module = _import_assignment_module("assignment_feedback", memory, monkeypatch)

    invalid_json = json.loads(await module.assignment_feedback("ou_assigner", "wa-1", "create", "not-json"))
    unknown_action = json.loads(
        await module.assignment_feedback(
            "ou_assigner",
            "wa-1",
            "bind_card",
            json.dumps({"raw_content": "内部动作不应公开"}, ensure_ascii=False),
        )
    )
    malformed_blocking = json.loads(
        await module.assignment_feedback(
            "ou_assigner",
            "wa-1",
            "create",
            json.dumps(
                {
                    "raw_content": "请确认范围",
                    "author_role": "recipient",
                    "entry_type": "question",
                    "notification_strategy": "blocking",
                    "options": [{"label": "仅当前团队", "value": "team"}],
                },
                ensure_ascii=False,
            ),
        )
    )

    assert {invalid_json["error"]["code"], unknown_action["error"]["code"]} == {"invalid_argument"}
    assert malformed_blocking["error"]["code"] == "invalid_argument"
    assert memory.calls == []


async def test_assignment_feedback_sends_and_binds_one_blocking_card(monkeypatch):
    memory = _MemoryStub(
        assignment_feedback=[
            {"ok": True, "result": _feedback_thread()},
            {"ok": True, "result": _feedback_thread(card_id="om_feedback")},
        ],
    )
    feishu = _FeishuStub()
    module = _import_assignment_module("assignment_feedback", memory, monkeypatch, feishu=feishu)

    result = json.loads(
        await module.assignment_feedback(
            "ou_assigner",
            "wa-1",
            "create",
            json.dumps(
                {
                    "raw_content": "请确认权限范围",
                    "author_role": "recipient",
                    "entry_type": "question",
                    "notification_strategy": "blocking",
                    "attempts": ["核查任务原文"],
                    "options": [
                        {"label": "仅当前团队", "value": "team", "recommended": True},
                        {"label": "整个组织", "value": "organization"},
                    ],
                    "private_note": "仅 Agent 可见",
                },
                ensure_ascii=False,
            ),
        )
    )

    assert result["ok"] is True
    assert result["card_id"] == "om_feedback"
    assert len(feishu.sent_cards) == 1
    assert "请确认权限范围" in feishu.sent_cards[0]["card_json"]
    assert "仅 Agent 可见" not in feishu.sent_cards[0]["card_json"]
    assert [call[1]["action"] for call in memory.calls if call[0] == "assignment_feedback"] == ["create", "bind_card"]


async def test_assignment_feedback_card_names_author_and_uses_authoritative_title(monkeypatch):
    assignment = {
        **_assignment(),
        "assigner": {"display_name": "王安排", "feishu_open_id": "ou_assigner"},
        "recipients": [{"display_name": "李接收", "feishu_open_id": "ou_recipient"}],
    }
    memory = _MemoryStub(
        assignment_get=[{"ok": True, "result": assignment}],
        assignment_feedback=[
            {
                "ok": True,
                "result": _feedback_thread(
                    entries=[
                        _feedback_entry(author_open_id="ou_recipient"),
                        _feedback_entry(
                            author_role="assigner",
                            entry_type="reply",
                            raw_content="先按推荐方案推进。",
                            version=2,
                            author_open_id="ou_assigner",
                        ),
                    ]
                ),
            },
            {"ok": True, "result": _feedback_thread(card_id="om_feedback")},
        ],
    )
    feishu = _FeishuStub(user_names={"ou_assigner": "王安排", "ou_recipient": "李接收"})
    module = _import_assignment_module(
        "assignment_feedback",
        memory,
        monkeypatch,
        feishu=feishu,
        session_id="feishu-ou_recipient",
    )

    result = json.loads(await module.assignment_feedback("ou_assigner", "wa-1", "create", _blocking_payload_json()))

    assert result["ok"] is True
    card_json = feishu.sent_cards[0]["card_json"]
    # The card names the real feedback author instead of only the bare role label.
    assert "1. v1 李接收 (接收者): 如果大量人不回复怎么办?" in card_json
    assert "2. v2 王安排 (安排者): 先按推荐方案推进。" in card_json
    # The visible task line uses the authoritative assignment title, never the placeholder.
    assert "所属任务: 整理会议结论" in card_json
    assert "当前工作安排" not in card_json
    written = next(call[1]["payload"] for call in memory.calls if call[0] == "assignment_feedback")
    assert "author_display_name" not in written
    assert "author_open_id" not in written
    assert written["assignment_title"] == "整理会议结论"


async def test_assignment_feedback_uses_entry_open_id_for_multi_recipient_author(monkeypatch):
    assignment = {
        **_assignment(),
        "assigner": {"display_name": "安排人员", "feishu_open_id": "ou_assigner"},
        "recipients": [
            {"display_name": "第一接收人", "feishu_open_id": "ou_recipient_one"},
            {"display_name": "第二接收人", "feishu_open_id": "ou_recipient_two"},
        ],
    }
    memory = _MemoryStub(
        assignment_get=[{"ok": True, "result": assignment}],
        assignment_feedback=[
            {
                "ok": True,
                "result": _feedback_thread(
                    entries=[
                        _feedback_entry(
                            author_open_id="ou_recipient_two",
                            author_display_name="伪造姓名",
                        )
                    ]
                ),
            },
            {"ok": True, "result": _feedback_thread(card_id="om_feedback")},
        ],
    )
    feishu = _FeishuStub(
        user_names={
            "ou_assigner": "安排人员",
            "ou_recipient_one": "第一接收人",
            "ou_recipient_two": "第二接收人",
        }
    )
    module = _import_assignment_module(
        "assignment_feedback",
        memory,
        monkeypatch,
        feishu=feishu,
        session_id="feishu-ou_recipient_two",
    )
    payload = json.loads(_blocking_payload_json())
    payload["author_display_name"] = "另一个伪造姓名"
    payload["author_open_id"] = "ou_recipient_one"

    result = json.loads(
        await module.assignment_feedback(
            "ou_assigner",
            "wa-1",
            "create",
            json.dumps(payload, ensure_ascii=False),
        )
    )

    assert result["ok"] is True
    card_json = feishu.sent_cards[0]["card_json"]
    assert "v1 第二接收人 (接收者): 如果大量人不回复怎么办?" in card_json
    assert "第一接收人" not in card_json
    assert "伪造姓名" not in card_json
    written = next(call[1]["payload"] for call in memory.calls if call[0] == "assignment_feedback")
    assert "author_display_name" not in written
    assert "author_open_id" not in written


async def test_assignment_feedback_does_not_guess_legacy_multi_recipient_author(monkeypatch):
    assignment = {
        **_assignment(),
        "recipients": [
            {"display_name": "第一接收人", "feishu_open_id": "ou_recipient_one"},
            {"display_name": "第二接收人", "feishu_open_id": "ou_recipient_two"},
        ],
    }
    memory = _MemoryStub(
        assignment_get=[{"ok": True, "result": assignment}],
        assignment_feedback=[
            {"ok": True, "result": _feedback_thread(entries=[_feedback_entry()])},
            {"ok": True, "result": _feedback_thread(card_id="om_feedback")},
        ],
    )
    feishu = _FeishuStub()
    module = _import_assignment_module(
        "assignment_feedback",
        memory,
        monkeypatch,
        feishu=feishu,
        session_id="feishu-ou_recipient_two",
    )

    result = json.loads(await module.assignment_feedback("ou_assigner", "wa-1", "create", _blocking_payload_json()))

    assert result["ok"] is True
    card_json = feishu.sent_cards[0]["card_json"]
    assert "v1 接收者: 如果大量人不回复怎么办?" in card_json
    assert "第一接收人" not in card_json
    assert "第二接收人" not in card_json


async def test_assignment_feedback_falls_back_when_assignment_record_is_unavailable(monkeypatch):
    memory = _MemoryStub(
        assignment_feedback=[
            {"ok": True, "result": _feedback_thread(entries=[_feedback_entry()])},
            {"ok": True, "result": _feedback_thread(card_id="om_feedback")},
        ],
    )
    feishu = _FeishuStub()
    module = _import_assignment_module(
        "assignment_feedback",
        memory,
        monkeypatch,
        feishu=feishu,
        session_id="feishu-ou_recipient",
    )

    payload = json.loads(_blocking_payload_json())
    payload["assignment_title"] = "未经核实的任务标题"
    result = json.loads(
        await module.assignment_feedback(
            "ou_assigner",
            "wa-1",
            "create",
            json.dumps(payload, ensure_ascii=False),
        )
    )

    # An unreadable assignment record must never block the feedback thread or its card.
    assert result["ok"] is True
    card_json = feishu.sent_cards[0]["card_json"]
    assert "v1 接收者: 如果大量人不回复怎么办?" in card_json
    assert "所属任务: 当前工作安排" in card_json
    assert "未经核实的任务标题" not in card_json


async def test_assignment_feedback_callback_names_the_replying_assigner(monkeypatch):
    assignment = {
        **_assignment(),
        "assigner": {"display_name": "伪造安排者", "feishu_open_id": "ou_assigner"},
        "recipients": [{"display_name": "伪造接收者", "feishu_open_id": "ou_recipient"}],
    }
    replied = _feedback_thread(
        card_id="om_feedback",
        entries=[
            _feedback_entry(author_open_id="ou_recipient"),
            _feedback_entry(
                author_role="assigner",
                author_open_id="ou_assigner",
                entry_type="reply",
                raw_content="继续私信",
                version=2,
            ),
        ],
    )
    replied["state"] = "updated_waiting_recipient_confirmation"
    memory = _MemoryStub(
        assignment_get=[{"ok": True, "result": assignment}],
        assignment_feedback=[{"ok": True, "result": replied}],
    )
    feishu = _FeishuStub(user_names={"ou_assigner": "王安排", "ou_recipient": "李接收"})
    module = _import_assignment_module(
        "assignment_feedback",
        memory,
        monkeypatch,
        feishu=feishu,
        session_id="feishu-ou_assigner",
    )

    result = json.loads(
        await module.assignment_feedback(
            card_action_json=json.dumps(
                {
                    "message_id": "om_feedback",
                    "dispatch": {"matched": True, "handler": "assignment_feedback"},
                    "action": {
                        "value": {
                            "action": "assignment_feedback_reply",
                            "arrangement_id": "wa-1",
                            "feedback_action": "assigner_reply",
                            "selected_label": "继续私信",
                            "selected_value": "keep_dm",
                        }
                    },
                    "source": {"operator_open_id": "ou_assigner", "sender_open_id": "ou_recipient"},
                    "business_context": {
                        "arrangement_id": "wa-1",
                        "reply_target_open_id": "ou_assigner",
                        "projection": {"assignment_title": "当前工作安排"},
                    },
                },
                ensure_ascii=False,
            )
        )
    )

    assert result["ok"] is True
    assert len(feishu.edits) == 1
    assert len(feishu.sent_cards) == 1
    # Each entry keeps its own author; the reply must not inherit the original author's name.
    for card_json in [edit["card_json"] for edit in feishu.edits] + [card["card_json"] for card in feishu.sent_cards]:
        card = json.loads(card_json)
        entry_block = next(
            element["text"]["content"]
            for element in card["elements"]
            if isinstance(element, dict)
            and isinstance(element.get("text"), dict)
            and str(element["text"].get("content", "")).startswith("1. v1")
        )
        assert entry_block == ("1. v1 李接收 (接收者): 如果大量人不回复怎么办?\n\n2. v2 王安排 (安排者): 继续私信")
        assert "v1 李接收 (接收者): 如果大量人不回复怎么办?" in card_json
        assert "v2 王安排 (安排者): 继续私信" in card_json
        assert "伪造安排者" not in card_json
        assert "伪造接收者" not in card_json
        # A stale placeholder carried in the callback projection must not survive.
        assert "当前工作安排" not in card_json
        assert "所属任务: 整理会议结论" in card_json


async def test_assignment_feedback_never_attributes_agent_entries_to_a_person(monkeypatch):
    assignment = {
        **_assignment(),
        "assigner": {"display_name": "王安排", "feishu_open_id": "ou_assigner"},
        "recipients": [{"display_name": "李接收", "feishu_open_id": "ou_recipient"}],
    }
    memory = _MemoryStub(
        assignment_get=[{"ok": True, "result": assignment}],
        assignment_feedback=[
            {
                "ok": True,
                "result": _feedback_thread(
                    card_id="om_feedback",
                    entries=[_feedback_entry(author_role="agent", raw_content="已核查方案原文", version=1)],
                ),
            }
        ],
    )
    feishu = _FeishuStub()
    module = _import_assignment_module(
        "assignment_feedback",
        memory,
        monkeypatch,
        feishu=feishu,
        session_id="feishu-ou_recipient",
    )

    result = json.loads(
        await module.assignment_feedback(
            "ou_assigner",
            "wa-1",
            "append",
            json.dumps(
                {
                    "raw_content": "已核查方案原文",
                    "author_role": "agent",
                    "entry_type": "question",
                    "notification_strategy": "record_only",
                },
                ensure_ascii=False,
            ),
        )
    )

    assert result["ok"] is True
    written = next(call[1]["payload"] for call in memory.calls if call[0] == "assignment_feedback")
    assert "author_display_name" not in written
    assert "author_open_id" not in written
    card_json = feishu.edits[0]["card_json"]
    assert "v1 Agent: 已核查方案原文" in card_json
    assert "李接收" not in card_json


def _blocking_payload_json() -> str:
    return json.dumps(
        {
            "raw_content": "如果大量人不回复怎么办?",
            "author_role": "recipient",
            "entry_type": "question",
            "notification_strategy": "blocking",
            "attempts": ["核查任务原文"],
            "options": [
                {"label": "继续私信", "value": "keep_dm", "recommended": True},
                {"label": "改走公开渠道", "value": "public_channel"},
            ],
        },
        ensure_ascii=False,
    )


async def test_assignment_send_card_claims_before_each_external_send(monkeypatch):
    pending = _delivery()
    recipient_claimed = _delivery(recipient_status="claimed", revision=2)
    recipient_sent = _delivery(recipient_status="sent", recipient_message_id="om_recipient", revision=3)
    progress_claimed = _delivery(recipient_status="sent", progress_status="claimed", revision=4)
    complete = _delivery(
        recipient_status="sent",
        recipient_message_id="om_recipient",
        progress_status="sent",
        progress_message_id="om_progress",
        revision=5,
    )
    assignment = {
        **_assignment(),
        "assigner": {"display_name": "伪造安排者", "feishu_open_id": "ou_assigner"},
        "action_items": [{"description": "提交方案", "owner": "伪造负责人"}],
    }
    memory = _MemoryStub(
        assignment_get=[{"ok": True, "result": assignment}],
        assignment_delivery=[
            {"ok": True, "result": pending},
            _claim("recipient", recipient_claimed),
            {"ok": True, "result": recipient_sent},
            _claim("progress", progress_claimed),
            {"ok": True, "result": complete},
        ],
    )
    feishu = _FeishuStub()
    module = _import_assignment_module("assignment_send_card", memory, monkeypatch, feishu=feishu)

    result = json.loads(await module.assignment_send_card("ou_recipient", "wa-1"))

    assert result["ok"] is True
    assert [call[1]["action"] for call in memory.calls if call[0] == "assignment_delivery"] == [
        "create",
        "claim_send",
        "complete_send",
        "claim_send",
        "complete_send",
    ]
    assert [call["receive_id"] for call in feishu.cards] == ["ou_recipient", "ou_assigner"]
    recipient_card = json.loads(feishu.cards[0]["card_json"])
    recipient_card_json = feishu.cards[0]["card_json"]
    assert recipient_card["elements"][0]["text"]["content"] == "任务: 整理会议结论\n安排者: 通讯录安排者"
    assert "伪造安排者" not in recipient_card_json
    assert "伪造负责人" not in recipient_card_json
    assert "直接告诉 HaiTun" in recipient_card["elements"][-1]["text"]["content"]
    assert "反馈会保留在本任务中并同步给安排者" in recipient_card["elements"][-1]["text"]["content"]
    assert _button_values(recipient_card) == [{"action": "confirm_assignment_receipt", "assignment_id": "wa-1"}]
    assert "wa-1" not in feishu.cards[1]["card_json"]


async def test_assignment_card_hides_internal_identifiers(monkeypatch):
    module = _import_assignment_module("assignment_send_card", _MemoryStub(), monkeypatch)

    card = module._build_assignment_card(
        assignment={"original_request": "请整理会议结论。"},
        assignment_id="wa-internal",
        title="整理会议结论",
        assigner_name="ou_internal_assigner",
    )

    heading = card["elements"][0]["text"]["content"]
    assert heading == "任务: 整理会议结论\n安排者: 安排者"
    for identifier in (
        "ou_internal_user",
        "wa-51fe97853d0fee03",
        "feedback-1",
        "user-recipient",
        "8fe4097f-01a4-4e65-872c-5cbca69dd703",
        "安排者 (ou_internal_user)",
        "018f47a2-7b31-7e65-8f42-123456789abc",
    ):
        assert module.readable_name(identifier) is None
    assert module.readable_name("Jason-Lee") == "Jason-Lee"


async def test_assignment_accept_publishes_once_and_invites_discussion(monkeypatch):
    accepted = {**_assignment(), "state": "received", "revision": 2}
    memory = _MemoryStub(
        assignment_get=[{"ok": True, "result": _assignment()}],
        assignment_transition=[{"ok": True, "result": accepted}],
        assignment_publication=[
            {
                "ok": True,
                "result": {
                    "acquired": True,
                    "claim_token": "claim-publication",
                    "publication": {"status": "claimed", "channel": "feishu_task"},
                },
            },
            {
                "ok": True,
                "result": {
                    "status": "published",
                    "channel": "feishu_task",
                    "task_guid": "task-1",
                    "url": "https://example.com/task-1",
                },
            },
        ],
    )
    feishu = _FeishuStub()
    module = _import_assignment_module(
        "assignment_accept",
        memory,
        monkeypatch,
        feishu=feishu,
        task=feishu,
        session_id="feishu-ou_recipient",
    )

    result = json.loads(await module.assignment_accept("wa-1"))

    assert result["ok"] is True
    assert result["accepted"] is True
    assert result["published"] is True
    assert len(feishu.tasks) == 1
    assert feishu.tasks[0]["assignees"] == "ou_recipient"
    assert feishu.messages[0]["text"] == "任务已发布。要不要和我一起讨论一版可评审的实施方案"
    assert [call[1]["action"] for call in memory.calls if call[0] == "assignment_publication"] == [
        "claim",
        "complete",
    ]


async def test_assignment_accept_reuses_existing_publication(monkeypatch):
    memory = _MemoryStub(
        assignment_get=[{"ok": True, "result": {**_assignment(), "state": "received"}}],
        assignment_publication=[
            {
                "ok": True,
                "result": {
                    "acquired": False,
                    "claim_token": None,
                    "publication": {
                        "status": "published",
                        "channel": "feishu_task",
                        "task_guid": "task-existing",
                        "url": "https://example.com/task-existing",
                    },
                },
            }
        ],
    )
    feishu = _FeishuStub()
    module = _import_assignment_module(
        "assignment_accept",
        memory,
        monkeypatch,
        feishu=feishu,
        task=feishu,
        session_id="feishu-ou_recipient",
    )

    result = json.loads(await module.assignment_accept("wa-1"))

    assert result["ok"] is True
    assert result["already_published"] is True
    assert result["task_guid"] == "task-existing"
    assert feishu.tasks == []


async def test_assignment_delivery_refresh_advances_read_status(monkeypatch):
    pending = _delivery(recipient_status="sent", recipient_message_id="om_recipient")
    memory = _MemoryStub(
        assignment_delivery=[{"ok": True, "result": [pending]}],
        assignment_get=[{"ok": True, "result": _assignment()}],
    )
    feishu = _FeishuStub()
    module = _import_assignment_module("assignment_delivery_refresh", memory, monkeypatch, feishu=feishu)
    advanced: list[tuple[str, str]] = []

    async def _advance(_client, *, assignment_id, event, recipient_open_id=""):
        advanced.append((event, recipient_open_id))
        return {"ok": True, "result": {"assignment_id": assignment_id}}

    async def _sync(_client, *, assignment_id, title):
        assert (assignment_id, title) == ("wa-1", "整理会议结论")
        return {"ok": True, "updated": True}

    monkeypatch.setattr(module, "_advance_delivery", _advance)
    monkeypatch.setattr(module, "_sync_progress_card", _sync)

    result = json.loads(await module.assignment_delivery_refresh())

    assert result == {"ok": True, "checked": 1, "read_advanced": 1, "card_updates": 1, "errors": []}
    assert advanced == [("read", "ou_recipient")]
    assert feishu.reads == ["om_recipient"]


async def test_index_does_not_execute_tool_modules(monkeypatch):
    # Indexing must be pure AST parsing: importing a tool module could trigger
    # side effects (e.g. connecting to an MCP server). Guard by making import
    # of a side-effectful module explode; index_tools must not touch it.
    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "_mcp":
            raise AssertionError("index_tools must not import tool modules")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    metas = await _idx.index_tools()
    assert metas  # still produced a full index


# ── extraction on a synthetic tools dir ──────────────────────────────────────


async def _write(dir_path: anyio.Path, name: str, body: str) -> None:
    await (dir_path / name).write_text(body, encoding="utf-8")


class _MemoryStub:
    def __init__(self, **responses: list[dict[str, Any]]) -> None:
        self.responses = {name: list(values) for name, values in responses.items()}
        self.calls: list[tuple[str, dict[str, Any], bool]] = []
        self.organization_read_calls: list[tuple[str, dict[str, Any], bool]] = []
        self.organization_write_calls: list[tuple[str, dict[str, Any], bool]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any], *, retryable: bool) -> dict[str, Any]:
        self.calls.append((name, arguments, retryable))
        queue = self.responses.get(name)
        if queue:
            return queue.pop(0)
        return {
            "ok": False,
            "error": {"code": "not_configured", "message": f"no response for {name}", "retryable": False},
        }

    async def call_organization_read_tool(
        self, name: str, arguments: dict[str, Any], *, retryable: bool
    ) -> dict[str, Any]:
        self.organization_read_calls.append((name, arguments, retryable))
        queue = self.responses.get(name)
        if queue:
            return queue.pop(0)
        return {
            "ok": False,
            "error": {"code": "not_configured", "message": f"no response for {name}", "retryable": False},
        }

    async def call_organization_write_tool(
        self, name: str, arguments: dict[str, Any], *, retryable: bool
    ) -> dict[str, Any]:
        self.organization_write_calls.append((name, arguments, retryable))
        queue = self.responses.get(name)
        if queue:
            return queue.pop(0)
        return {
            "ok": False,
            "error": {"code": "not_configured", "message": f"no response for {name}", "retryable": False},
        }


class _FeishuStub:
    def __init__(self, *, user_names: dict[str, str] | None = None) -> None:
        self.cards: list[dict[str, str]] = []
        self.sent_cards: list[dict[str, str]] = []
        self.edits: list[dict[str, str]] = []
        self.messages: list[dict[str, str]] = []
        self.reads: list[str] = []
        self.tasks: list[dict[str, str]] = []
        self.user_names = user_names or {}

    async def get_users_batch_impl(
        self,
        user_ids: str,
        user_id_type: str = "open_id",
        department_id_type: str = "open_department_id",
    ) -> dict[str, Any]:
        del user_id_type, department_id_type
        users = [
            {"open_id": open_id, "name": self.user_names.get(open_id, "通讯录安排者")}
            for open_id in user_ids.split(",")
        ]
        return {"ok": True, "users": users, "count": len(users)}

    async def feishu_message_send_card(
        self,
        receive_id: str,
        card_json: str,
        receive_id_type: str = "chat_id",
        user_key: str = "",
        business_context_json: str = "{}",
        action_handlers_json: str = "{}",
    ) -> str:
        self.cards.append(
            {
                "receive_id": receive_id,
                "card_json": card_json,
                "receive_id_type": receive_id_type,
                "user_key": user_key,
                "business_context_json": business_context_json,
                "action_handlers_json": action_handlers_json,
            }
        )
        return json.dumps({"ok": True, "sent": True, "message_id": f"om_{len(self.cards)}"})

    async def send_card_impl(
        self,
        receive_id: str,
        card_json: str,
        receive_id_type: str,
        user_key: str | None = None,
        business_context_json: str = "{}",
        action_handlers_json: str = "{}",
    ) -> dict[str, Any]:
        self.sent_cards.append(
            {
                "receive_id": receive_id,
                "card_json": card_json,
                "receive_id_type": receive_id_type,
                "user_key": user_key or "",
                "business_context_json": business_context_json,
                "action_handlers_json": action_handlers_json,
            }
        )
        return {"ok": True, "sent": True, "message_id": "om_feedback"}

    async def edit_card_impl(self, message_id: str, card_json: str, user_key: str = "") -> dict[str, Any]:
        self.edits.append({"message_id": message_id, "card_json": card_json, "user_key": user_key})
        return {"ok": True, "edited": True, "message_id": message_id}

    async def send_message_impl(
        self,
        receive_id: str,
        text: str,
        receive_id_type: str,
        on_behalf_of: str = "",
    ) -> dict[str, Any]:
        self.messages.append(
            {
                "receive_id": receive_id,
                "text": text,
                "receive_id_type": receive_id_type,
                "on_behalf_of": on_behalf_of,
            }
        )
        return {"ok": True, "sent": True}

    async def read_status_impl(
        self,
        message_id: str,
        include_unread: bool = True,
        page_size: int = 100,
        user_key: str = "",
    ) -> dict[str, Any]:
        self.reads.append(message_id)
        return {"ok": True, "read_users": [{"open_id": "ou_recipient"}]}

    async def feishu_task_create(
        self,
        summary: str,
        description: str = "",
        due: str = "",
        assignees: str = "",
        followers: str = "",
        user_key: str = "",
        identity: str = "",
    ) -> str:
        self.tasks.append(
            {
                "summary": summary,
                "description": description,
                "due": due,
                "assignees": assignees,
                "followers": followers,
                "user_key": user_key,
                "identity": identity,
            }
        )
        return json.dumps({"ok": True, "task_guid": "task-1", "url": "https://example.com/task-1"})


def _assignment() -> dict[str, Any]:
    return {
        "assignment_id": "wa-1",
        "title": "整理会议结论",
        "state": "assigned",
        "assigner": {"display_name": "安排者", "feishu_open_id": "ou_assigner"},
        "recipients": [{"display_name": "接收者", "feishu_open_id": "ou_recipient"}],
        "original_request": "请整理会议结论。",
        "context": "会议已经结束。",
        "expected_outcome": "提交可评审方案。",
        "action_items": [{"description": "提交方案", "deadline": "2026-08-08"}],
        "delivery_records": [],
        "revision": 1,
    }


def _feedback_thread(card_id: str | None = None, entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "thread_id": "feedback-1",
        "arrangement_id": "wa-1",
        "state": "open",
        "version": 1,
        "card_id": card_id,
        "entries": entries if entries is not None else [],
    }


def _feedback_entry(**overrides: Any) -> dict[str, Any]:
    return {
        "author_role": "recipient",
        "entry_type": "question",
        "raw_content": "如果大量人不回复怎么办?",
        "version": 1,
        **overrides,
    }


def _delivery(
    *,
    recipient_status: str = "pending",
    recipient_message_id: str | None = None,
    progress_status: str = "pending",
    progress_message_id: str | None = None,
    revision: int = 1,
) -> dict[str, Any]:
    return {
        "assignment_id": "wa-1",
        "assigner_open_id": "ou_assigner",
        "assigner_progress_message_id": progress_message_id,
        "progress_status": progress_status,
        "card_rendered_revision": 0,
        "recipients": [
            {
                "open_ids": ["ou_recipient"],
                "delivery_open_id": "ou_recipient" if recipient_status != "pending" else None,
                "message_id": recipient_message_id,
                "send_status": recipient_status,
                "read_at": None,
                "accepted_at": None,
            }
        ],
        "task_published_at": None,
        "revision": revision,
    }


def _claim(target: str, delivery: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "result": {
            "acquired": True,
            "claim_token": f"claim-{target}",
            "target": target,
            "delivery": delivery,
        },
    }


def _button_values(card: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        action["value"]
        for element in card.get("elements", [])
        if isinstance(element, dict)
        for action in element.get("actions", [])
        if isinstance(action, dict) and isinstance(action.get("value"), dict)
    ]


def _import_memory_module(name: str, memory: _MemoryStub, monkeypatch) -> Any:
    mcp_path = TOOLS_DIR / "_fusion_memory_mcp.py"
    mcp_name = f"fusion_memory_tool__fusion_memory_mcp_{hashlib.sha256(str(mcp_path).encode()).hexdigest()[:12]}"
    mcp_module = types.ModuleType(mcp_name)
    mcp_module.__dict__["CLIENT"] = memory
    monkeypatch.setitem(sys.modules, mcp_name, mcp_module)
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def _import_assignment_module(
    name: str,
    memory: _MemoryStub,
    monkeypatch,
    *,
    feishu: _FeishuStub | None = None,
    task: _FeishuStub | None = None,
    session_id: str = "feishu-ou_assigner",
) -> Any:
    mcp_path = TOOLS_DIR / "_fusion_memory_mcp.py"
    mcp_name = f"fusion_memory_tool__fusion_memory_mcp_{hashlib.sha256(str(mcp_path).encode()).hexdigest()[:12]}"
    mcp_module = types.ModuleType(mcp_name)
    mcp_module.__dict__["CLIENT"] = memory
    monkeypatch.setitem(sys.modules, mcp_name, mcp_module)

    feishu = feishu or _FeishuStub()
    message_module = types.ModuleType("feishu_message")
    message_module.__dict__["feishu_message_send_card"] = feishu.feishu_message_send_card
    monkeypatch.setitem(sys.modules, "feishu_message", message_module)
    impl_module = types.ModuleType("_feishu_impl")
    impl_module.__dict__.update(
        {
            "send_card_impl": feishu.send_card_impl,
            "edit_card_impl": feishu.edit_card_impl,
            "send_message_impl": feishu.send_message_impl,
            "read_status_impl": feishu.read_status_impl,
            "get_users_batch_impl": feishu.get_users_batch_impl,
        }
    )
    monkeypatch.setitem(sys.modules, "_feishu_impl", impl_module)
    task_module = types.ModuleType("feishu_task")
    task_module.__dict__["_feishu_task_create_once"] = (task or feishu).feishu_task_create
    monkeypatch.setitem(sys.modules, "feishu_task", task_module)

    runtime_context = importlib.import_module("psi_agent.session.runtime_context")
    monkeypatch.setattr(runtime_context, "get_session_id", lambda: session_id)
    for module_name in ("_assignment_tool_common", "_assignment_delivery", name):
        sys.modules.pop(module_name, None)
    return importlib.import_module(name)


async def test_extract_signature_and_docstring(tmp_path):
    d = anyio.Path(str(tmp_path))
    await _write(
        d,
        "sample.py",
        (
            "async def sample(a: str, b: int = 3, flag: bool = False,\n"
            "                 items: list[str] | None = None) -> str:\n"
            '    """Do a sample thing.\n'
            "\n"
            "    More detail here.\n"
            "\n"
            "    Args:\n"
            "        a: first.\n"
            "    Returns:\n"
            "        text.\n"
            '    """\n'
            "    return a\n"
        ),
    )
    metas = await _idx.index_tools(d)
    assert len(metas) == 1
    m = metas[0]
    assert m.name == "sample"
    assert m.file == "sample.py"
    assert m.signature == "sample(a: str, b: int = 3, flag: bool = False, items: list[str] | None = None)"
    assert m.summary == "Do a sample thing."
    # description stops before Args:/Returns:
    assert "More detail here." in m.description
    assert "first" not in m.description
    assert "Args:" in m.docstring


async def test_syntax_error_file_is_skipped(tmp_path):
    d = anyio.Path(str(tmp_path))
    await _write(d, "good.py", 'async def good() -> str:\n    """Good."""\n    return "x"\n')
    await _write(d, "broken.py", "async def broken( : oops\n")
    metas = await _idx.index_tools(d)
    assert {m.name for m in metas} == {"good"}


async def test_only_async_top_level_public_functions(tmp_path):
    d = anyio.Path(str(tmp_path))
    await _write(
        d,
        "mixed.py",
        (
            "def sync_fn():\n    return 1\n\n"
            "async def _private():\n    return 1\n\n"
            'async def real_tool() -> str:\n    """Real."""\n    return "x"\n'
        ),
    )
    metas = await _idx.index_tools(d)
    assert {m.name for m in metas} == {"real_tool"}


# ── tool_search ──────────────────────────────────────────────────────────────


async def test_tool_search_matches_known_tool():
    out = await tool_search("fetch url markdown")
    assert "fetch" in out


async def test_tool_search_empty_result():
    out = await tool_search("zzz_nonexistent_keyword_qqq")
    assert "no tools match" in out


async def test_tool_search_limit_truncates():
    out = await tool_search("", limit=3)
    lines = [ln for ln in out.splitlines() if " — " in ln and not ln.startswith("[")]
    assert len(lines) == 3
    assert "Truncated at 3" in out


# ── tool_search_code ─────────────────────────────────────────────────────────


async def test_tool_search_code_finds_line():
    out = await tool_search_code(r"def fetch\(")
    assert "fetch.py:" in out
    assert "def fetch(" in out


async def test_tool_search_code_invalid_regex_falls_back():
    out = await tool_search_code("fetch(")  # unbalanced paren -> invalid regex
    assert "Invalid regex" in out
    assert "fetch.py:" in out


async def test_tool_search_code_limit_truncates():
    out = await tool_search_code("import", limit=2)
    hits = [ln for ln in out.splitlines() if ":" in ln and not ln.startswith("[")]
    assert len(hits) == 2
    assert "Truncated at 2" in out


# ── tool_describe ────────────────────────────────────────────────────────────


async def test_tool_describe_known_tool():
    out = await tool_describe("find_files")
    assert "Tool: find_files" in out
    assert "File: find_files.py" in out
    assert "Signature: async def find_files(" in out
    assert "glob pattern" in out


async def test_tool_describe_unknown_suggests():
    out = await tool_describe("fetc")
    assert "no tool named 'fetc'" in out
    assert "fetch" in out


async def test_tool_describe_unknown_no_suggestion():
    out = await tool_describe("zzz_nope_qqq")
    assert "no tool named 'zzz_nope_qqq'" in out
    assert "tool_search" in out


# ── tools load cleanly into the framework registry ───────────────────────────


async def test_discovery_tools_are_valid_tool_functions():
    for name in ("tool_search", "tool_search_code", "tool_describe"):
        mod = importlib.import_module(name)
        func = getattr(mod, name)
        tf = ToolFunction.from_callable(func)
        assert tf.name == name
        assert tf.description
        assert tf.parameters["type"] == "object"
