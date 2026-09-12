"""Unit tests for Haitun feature-UAT dialogue runner."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

FEISHU_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = FEISHU_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

score_mod: Any = importlib.import_module("_feature_uat_score")
impl: Any = importlib.import_module("_feature_uat_impl")
tool: Any = importlib.import_module("poc_feature_uat")

PLAYBOOK = FEISHU_ROOT / "skills" / "haitun-feature-uat" / "playbooks" / "proposal-capabilities-natural.json"


def test_score_expect_groups_and_forbid() -> None:
    case = {
        "expect_any_of": [["中事", "大事"], ["上级", "mentor"]],
        "forbid_any": ["必须先交方案"],
    }
    ok = score_mod.score_reply("这偏中事,建议先和上级对齐多方案。", case)
    assert ok["ok"] is True
    bad = score_mod.score_reply("随便排一下就行。", case)
    assert bad["ok"] is False
    assert bad["missing_groups"]
    forbid = score_mod.score_reply("中事,找上级。必须先交方案才能试。", case)
    assert forbid["ok"] is False
    assert "必须先交方案" in forbid["forbid_hits"]


def test_playbook_file_loads_and_has_required_cases() -> None:
    playbook = json.loads(PLAYBOOK.read_text(encoding="utf-8"))
    ids = {c["id"] for c in playbook["cases"]}
    for need in ("A-N1", "A-N2", "A-N3", "A-N4", "A-N6", "B-N1", "B-N2", "B-N3"):
        assert need in ids
    assert playbook["cases"][0]["session"] == playbook["cases"][1]["session"]


@pytest.mark.anyio
async def test_runner_pass_with_scripted_replies() -> None:
    playbook = {
        "id": "t",
        "title": "t",
        "cases": [
            {
                "id": "c1",
                "session": "s1",
                "required": True,
                "user": "hello",
                "expect_any_of": [["中事"]],
                "forbid_any": [],
            },
            {
                "id": "c2",
                "session": "s1",
                "required": True,
                "user": "follow",
                "expect_any_of": [["口头"]],
                "forbid_any": ["必须先交方案"],
            },
        ],
    }
    created: list[str] = []

    async def create_session(sid_hint: str) -> dict[str, Any]:
        created.append(sid_hint)
        return {"ok": True, "session_id": f"b-{len(created)}"}

    async def send_message(target: str, message: str, timeout_seconds: float) -> dict[str, Any]:
        assert target == "b-1"
        if message == "hello":
            return {"ok": True, "reply_text": "这是中事,建议整理。"}
        return {"ok": True, "reply_text": "口头先跟 mentor 说即可,站会可以先试。"}

    result = await impl.run_feature_uat(
        playbook,
        create_session=create_session,
        send_message=send_message,
        current_session_id="caller",
    )
    assert result["verdict"] == "pass", result
    assert result["sessions_created"] == 1
    assert len(created) == 1


@pytest.mark.anyio
async def test_runner_refuses_current_session() -> None:
    playbook = {
        "id": "t",
        "cases": [
            {
                "id": "c1",
                "required": True,
                "user": "x",
                "expect_any_of": [["ok"]],
            }
        ],
    }

    async def create_session(sid_hint: str) -> dict[str, Any]:
        return {"ok": True, "session_id": "same"}

    async def send_message(target: str, message: str, timeout_seconds: float) -> dict[str, Any]:
        raise AssertionError("must not send")

    result = await impl.run_feature_uat(
        playbook,
        create_session=create_session,
        send_message=send_message,
        current_session_id="same",
    )
    assert result["verdict"] == "blocked"
    assert any("current session" in g for g in result["gaps"])


@pytest.mark.anyio
async def test_runner_fail_on_missing_expect() -> None:
    playbook = {
        "id": "t",
        "cases": [
            {
                "id": "c1",
                "required": True,
                "user": "x",
                "expect_any_of": [["中事"]],
                "forbid_any": [],
            }
        ],
    }

    async def create_session(sid_hint: str) -> dict[str, Any]:
        return {"ok": True, "session_id": "b-x"}

    async def send_message(target: str, message: str, timeout_seconds: float) -> dict[str, Any]:
        return {"ok": True, "reply_text": "我帮你排个 checklist。"}

    result = await impl.run_feature_uat(
        playbook,
        create_session=create_session,
        send_message=send_message,
    )
    assert result["verdict"] == "fail"
    assert result["ok"] is False


@pytest.mark.anyio
async def test_tool_wires_mocked_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_create(**kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "session_id": "b-tool"}

    async def fake_send(**kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "reply_text": "请假走假勤流程,差旅回来报销.",
        }

    monkeypatch.setattr(tool._h, "create_session", fake_create)
    monkeypatch.setattr(tool._h, "send_session_message", fake_send)
    monkeypatch.setattr(tool, "get_session_id", lambda: "caller-session")

    mini = {
        "id": "mini",
        "cases": [
            {
                "id": "A-N6",
                "required": True,
                "user": "请假报销",
                "expect_any_of": [["请假", "报销"]],
                "forbid_any": ["先写技术方案", "这是中事"],
            }
        ],
    }
    raw = await tool.poc_feature_uat(playbook_json=json.dumps(mini, ensure_ascii=False))
    result = json.loads(raw)
    assert result["verdict"] == "pass", result
    assert result["target_mode"] == "local_defaults"


@pytest.mark.anyio
async def test_fixed_session_skips_create() -> None:
    playbook = {
        "id": "t",
        "cases": [
            {
                "id": "c1",
                "session": "s1",
                "required": True,
                "user": "hi",
                "expect_any_of": [["ok"]],
                "forbid_any": [],
            }
        ],
    }
    creates = 0

    async def create_session(sid_hint: str) -> dict[str, Any]:
        nonlocal creates
        creates += 1
        return {"ok": True, "session_id": "should-not"}

    async def send_message(target: str, message: str, timeout_seconds: float) -> dict[str, Any]:
        assert target == "existing-b"
        return {"ok": True, "reply_text": "ok"}

    result = await impl.run_feature_uat(
        playbook,
        create_session=create_session,
        send_message=send_message,
        fixed_session_id="existing-b",
        target_mode="fixed_session",
    )
    assert creates == 0
    assert result["sessions_created"] == 0
    assert result["verdict"] == "pass"
    assert result["target_mode"] == "fixed_session"


@pytest.mark.anyio
async def test_tool_passes_agent_and_uses_http_when_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def fake_create(**kwargs: Any) -> dict[str, Any]:
        seen["create"] = kwargs
        return {"ok": True, "session_id": "remote-b"}

    async def fake_http(**kwargs: Any) -> dict[str, Any]:
        seen["http"] = kwargs
        return {"ok": True, "reply_text": "中事,建议找上级对齐.", "text": "中事,建议找上级对齐."}

    async def boom_send(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("local channel chat must not run for remote_gateway")

    monkeypatch.setattr(tool._h, "create_session", fake_create)
    monkeypatch.setattr(tool._h, "send_session_message", boom_send)
    monkeypatch.setattr(tool._sub, "chat_via_gateway", fake_http)
    monkeypatch.setattr(tool, "get_session_id", lambda: "caller")

    mini = {
        "id": "mini",
        "cases": [
            {
                "id": "A-N1",
                "required": True,
                "user": "要加预算",
                "expect_any_of": [["中事"], ["上级"]],
                "forbid_any": [],
            }
        ],
    }
    raw = await tool.poc_feature_uat(
        playbook_json=json.dumps(mini, ensure_ascii=False),
        agent="D:/wip/agents/feishu",
        target_gateway_url="http://127.0.0.1:19000",
    )
    result = json.loads(raw)
    assert result["verdict"] == "pass", result
    assert result["target_mode"] == "remote_gateway"
    assert seen["create"]["agent"] == "D:/wip/agents/feishu"
    assert seen["create"]["gateway_url"] == "http://127.0.0.1:19000"
    assert seen["create"]["wait_channel"] is False
    assert seen["http"]["gateway_url"] == "http://127.0.0.1:19000"
    assert seen["http"]["session_id"] == "remote-b"


@pytest.mark.anyio
async def test_chat_via_gateway_parses_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    sub: Any = importlib.import_module("_subagent_helpers")

    class _FakeContent:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def iter_any(self) -> Any:
            async def _gen() -> Any:
                yield self._payload

            return _gen()

    class _FakeResp:
        status = 200

        def __init__(self) -> None:
            body = b'data: {"type":"text","text":"hello "}\n\ndata: {"type":"text","text":"world"}\n\ndata: [DONE]\n\n'
            self.content = _FakeContent(body)

        async def __aenter__(self) -> _FakeResp:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    class _FakeSession:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def post(self, url: str, json: dict[str, Any]) -> _FakeResp:
            assert url.endswith("/sessions/s1/chat")
            assert json["chunks"][0]["text"] == "ping"
            return _FakeResp()

    monkeypatch.setattr(sub.aiohttp, "ClientSession", _FakeSession)
    out = await sub.chat_via_gateway(
        gateway_url="http://127.0.0.1:9",
        session_id="s1",
        message="ping",
        timeout_seconds=5.0,
    )
    assert out["ok"] is True
    assert out["reply_text"] == "hello world"
