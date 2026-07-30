"""Tests for the Haitun workspace tool-discovery meta-tools.

Covers ``_tool_index`` (static AST scan) and the ``tool_search`` /
``tool_search_code`` / ``tool_describe`` tools built on top of it.
"""

from __future__ import annotations

import builtins
import hashlib
import importlib
import json
import sys
import types
from pathlib import Path
from typing import Any

import anyio

from psi_agent.session.tool_registry import ToolFunction

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
        "assignment_send_card",
    } <= names
    # Private helper files (``_fetch_impl.py``) never expose a tool.
    assert "fetch_impl" not in names
    assert all(not n.startswith("_") for n in names)


async def test_assignment_read_tools_are_replayable():
    source = await (anyio.Path(str(TOOLS_DIR)) / "_fusion_memory_mcp.py").read_text(encoding="utf-8")
    assert '"assignment_get"' in source
    assert '"assignment_list"' in source
    assert '"assignment_upsert"' not in source.split("READ_TOOLS", 1)[1].split("}", 1)[0]


async def test_assignment_upsert_forwards_assignment_object(monkeypatch):
    fake_client = _FakeMemoryClient()
    module = _import_assignment_tool_with_fake_client("assignment_upsert", fake_client, monkeypatch)

    out = await module.assignment_upsert(
        json.dumps(
            {
                "title": "同步客户会议后续",
                "assigner": {"user_id": "user-a"},
                "recipients": [{"user_id": "user-b"}],
                "idempotency_key": "feishu-message-1",
            },
            ensure_ascii=False,
        )
    )

    assert json.loads(out)["ok"] is True
    assert fake_client.calls == [
        (
            "assignment_upsert",
            {
                "assignment": {
                    "title": "同步客户会议后续",
                    "assigner": {"user_id": "user-a"},
                    "recipients": [{"user_id": "user-b"}],
                    "idempotency_key": "feishu-message-1",
                }
            },
            False,
        )
    ]


async def test_assignment_list_forwards_read_filter(monkeypatch):
    fake_client = _FakeMemoryClient()
    module = _import_assignment_tool_with_fake_client("assignment_list", fake_client, monkeypatch)

    out = await module.assignment_list(participant_user_id="user-b", state="assigned", limit=200)

    assert json.loads(out)["ok"] is True
    assert fake_client.calls == [
        (
            "assignment_list",
            {"participant_user_id": "user-b", "state": "assigned", "limit": 50},
            True,
        )
    ]


async def test_assignment_transition_rejects_invalid_json(monkeypatch):
    fake_client = _FakeMemoryClient()
    module = _import_assignment_tool_with_fake_client("assignment_transition", fake_client, monkeypatch)

    out = await module.assignment_transition("wa-1", "not-json")

    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_argument"
    assert fake_client.calls == []


async def test_assignment_send_card_builds_deterministic_actions(monkeypatch):
    fake_feishu = _FakeFeishuMessage()
    module = _import_assignment_send_card_with_fake_feishu(fake_feishu, monkeypatch)

    out = await module.assignment_send_card(
        receive_id="ou_recipient",
        assignment_id="wa-123",
        title="同步客户会议后续",
        assigner_name="张浩",
        summary="请整理会议结论并给出下一步方案。",
        receive_id_type="open_id",
        user_key="ou_assigner",
    )

    assert json.loads(out)["ok"] is True
    [call] = fake_feishu.calls
    assert call["receive_id"] == "ou_recipient"
    assert call["receive_id_type"] == "open_id"
    card = json.loads(call["card_json"])
    assert card["header"]["title"]["content"] == "新的工作安排"
    assert "同步客户会议后续" in json.dumps(card, ensure_ascii=False)
    assert {
        "action": "view_assignment_detail",
        "assignment_id": "wa-123",
    } in _button_values(card)
    assert {
        "action": "confirm_assignment_receipt",
        "assignment_id": "wa-123",
    } in _button_values(card)
    assert json.loads(call["business_context_json"]) == {
        "type": "work_assignment",
        "assignment_id": "wa-123",
        "title": "同步客户会议后续",
        "assigner_name": "张浩",
    }
    assert json.loads(call["action_handlers_json"]) == {
        "view_assignment_detail": "assignment_get",
        "confirm_assignment_receipt": "assignment_transition",
    }


async def test_work_assignment_skill_documents_generic_assignment_flow():
    skill_path = WORKSPACE_ROOT / "skills" / "work-assignment-delegation" / "SKILL.md"
    source = await anyio.Path(str(skill_path)).read_text(encoding="utf-8")
    assert "assignment_upsert" in source
    assert "assignment_transition" in source
    assert "不只限于开发任务" in source
    assert "不能把推测写成确定事实" in source


async def test_work_assignment_skill_documents_recipient_plan_flow():
    skill_path = WORKSPACE_ROOT / "skills" / "work-assignment-delegation" / "SKILL.md"
    source = await anyio.Path(str(skill_path)).read_text(encoding="utf-8")
    assert "接收者流程" in source
    assert "安排者原文" in source
    assert "可评审方案" in source
    assert 'transition_type: "confirm_receipt"' in source
    assert 'transition_type: "submit_plan"' in source
    assert 'transition_type: "close"' in source
    assert "closure_reason" in source
    assert "不要调用 `closed_without_plan`" in source


async def test_work_assignment_skill_documents_scenario_templates():
    skill_path = WORKSPACE_ROOT / "skills" / "work-assignment-delegation" / "SKILL.md"
    source = await anyio.Path(str(skill_path)).read_text(encoding="utf-8")
    assert "场景模板" in source
    assert "通用工作安排" in source
    assert "开发任务" in source
    assert "交接或同步" in source
    assert "只改变表达和重点" in source
    assert "不得改变已确认事实" in source


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


class _FakeMemoryClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], bool]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any], *, retryable: bool) -> dict[str, Any]:
        self.calls.append((name, arguments, retryable))
        return {"ok": True, "result": {"name": name, "arguments": arguments}}


class _FakeFeishuMessage:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def feishu_message_send_card(
        self,
        receive_id: str,
        card_json: str,
        receive_id_type: str = "chat_id",
        user_key: str = "",
        business_context_json: str = "{}",
        action_handlers_json: str = "{}",
    ) -> str:
        self.calls.append(
            {
                "receive_id": receive_id,
                "card_json": card_json,
                "receive_id_type": receive_id_type,
                "user_key": user_key,
                "business_context_json": business_context_json,
                "action_handlers_json": action_handlers_json,
            }
        )
        return json.dumps({"ok": True, "sent": True}, ensure_ascii=False)


def _button_values(card: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for element in card.get("elements", []):
        if not isinstance(element, dict):
            continue
        for action in element.get("actions", []):
            if isinstance(action, dict) and isinstance(action.get("value"), dict):
                values.append(action["value"])
    return values


def _import_assignment_tool_with_fake_client(name: str, fake_client: _FakeMemoryClient, monkeypatch) -> Any:
    mcp_path = TOOLS_DIR / "_fusion_memory_mcp.py"
    mcp_module_name = f"fusion_memory_tool__fusion_memory_mcp_{hashlib.sha256(str(mcp_path).encode()).hexdigest()[:12]}"
    fake_mcp_module = types.ModuleType(mcp_module_name)
    fake_mcp_module.__dict__["CLIENT"] = fake_client
    monkeypatch.setitem(sys.modules, mcp_module_name, fake_mcp_module)
    sys.modules.pop(name, None)
    assignment_common = TOOLS_DIR / "_assignment_tool_common.py"
    common_name = (
        f"fusion_memory_tool__assignment_tool_common_{hashlib.sha256(str(assignment_common).encode()).hexdigest()[:12]}"
    )
    fake_common_module = types.ModuleType(common_name)
    fake_common_module.__dict__["CLIENT"] = fake_client
    monkeypatch.setitem(sys.modules, common_name, fake_common_module)
    sys.modules.pop("_assignment_tool_common", None)
    return importlib.import_module(name)


def _import_assignment_send_card_with_fake_feishu(fake_feishu: _FakeFeishuMessage, monkeypatch) -> Any:
    fake_module = types.ModuleType("feishu_message")
    fake_module.__dict__["feishu_message_send_card"] = fake_feishu.feishu_message_send_card
    monkeypatch.setitem(sys.modules, "feishu_message", fake_module)
    sys.modules.pop("assignment_send_card", None)
    return importlib.import_module("assignment_send_card")


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
