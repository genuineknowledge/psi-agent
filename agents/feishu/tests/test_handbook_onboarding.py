"""Handbook onboarding card validation + Session event-arg injection."""

from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path
from typing import Any, cast

import anyio
import pytest

from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.event_protocol import EVENT_FEISHU_CHAT_MEMBER_ADDED, parse_event_envelope
from psi_agent.session.tool_registry import FileEntry, ToolFunction, ToolRegistry
from psi_agent.session.trigger_registry import TriggerRegistry, merge_event_tool_args

HAITUN = Path(__file__).resolve().parents[1]
TOOLS = HAITUN / "tools"


def _load_handbook_module() -> Any:
    path = TOOLS / "handbook_onboarding.py"
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location("handbook_onboarding_under_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_validate_form_pass_and_fail() -> None:
    mod = _load_handbook_module()
    cfg = {
        "required_form_fields": [
            {"name": "acked", "equals": "true", "fail_message": "need ack"},
            {
                "name": "confirm_text",
                "contains": "我已阅读并同意",
                "fail_message": "need phrase",
            },
        ]
    }
    ok, reasons = mod._validate_form(cfg, {"acked": "true", "confirm_text": "我已阅读并同意管理制度"})
    assert ok is True
    assert reasons == []

    ok2, reasons2 = mod._validate_form(cfg, {"acked": "false", "confirm_text": "ok"})
    assert ok2 is False
    assert "need ack" in reasons2
    assert "need phrase" in reasons2


def test_build_card_includes_form_and_fail_banner() -> None:
    mod = _load_handbook_module()
    cfg = {
        "company_name": "测试司",
        "welcome_title": "请确认",
        "welcome_intro": "请阅读",
        "handbook_links": [{"title": "手册", "url": "https://example.com/h"}],
        "fail_employee_prefix": "失败: ",
    }
    card = mod._build_card(cfg, open_id="ou_1", name="张三", fail_reason="缺确认语", attempt=2)
    assert card["header"]["template"] == "orange"
    elements = card["elements"]
    assert any(e.get("tag") == "form" for e in elements)
    form = next(e for e in elements if e.get("tag") == "form")
    names = {c.get("name") for c in form["elements"]}
    assert "acked" in names
    assert "confirm_text" in names
    md = elements[0]["text"]["content"]
    assert "失败:缺确认语" in md
    assert "https://example.com/h" in md


def test_merge_event_tool_args_injects_payload() -> None:
    async def sample(
        text: str = "",
        event_payload_json: str = "",
        event_name: str = "",
    ) -> str:
        return text

    env = parse_event_envelope(
        {
            "schema_version": 1,
            "source": "feishu",
            "event": "feishu.hr.user_created",
            "payload": {"open_id": "ou_new", "name": "李四"},
            "raw_event": "contact.user.created_v3",
        }
    )
    merged = merge_event_tool_args(sample, {"text": "x"}, env)
    assert merged["text"] == "x"
    assert json.loads(merged["event_payload_json"])["open_id"] == "ou_new"
    assert merged["event_name"] == "feishu.hr.user_created"


@pytest.mark.anyio
async def test_dispatch_injects_event_payload_json(tmp_path: Path) -> None:
    called: dict[str, str] = {}

    async def welcome_tool(event_payload_json: str = "", open_id: str = "") -> str:
        called["payload"] = event_payload_json
        called["open_id"] = open_id
        return "ok"

    tools = ToolRegistry()
    tools._files["welcome.py"] = FileEntry(
        file_hash="x",
        tools={"welcome_tool": ToolFunction.from_callable(welcome_tool)},
        funcs={"welcome_tool": welcome_tool},
        fresh=True,
    )

    trig_dir = tmp_path / "triggers" / "welcome"
    await anyio.Path(trig_dir).mkdir(parents=True)
    await anyio.Path(trig_dir / "TRIGGER.md").write_text(
        textwrap.dedent(
            f"""\
            ---
            name: welcome
            event: {EVENT_FEISHU_CHAT_MEMBER_ADDED}
            fire: tool
            tool: welcome_tool
            tool_args: {{}}
            visibility: silent
            ---
            """
        ),
        encoding="utf-8",
    )
    registry = await TriggerRegistry.load(tmp_path / "triggers")
    agent = SessionAgent(
        ai_client=AiClient("http://nonexistent/v1"),
        tool_registry=tools,
        trigger_registry=registry,
        workspace_path=tmp_path,
    )
    env = parse_event_envelope(
        {
            "schema_version": 1,
            "source": "feishu",
            "event": EVENT_FEISHU_CHAT_MEMBER_ADDED,
            "payload": {"chat_id": "oc_1", "member_open_id": "ou_1"},
            "idempotency_key": "inject-1",
        }
    )
    async with agent._lock:
        fired = await registry.dispatch(env, agent)
    assert fired == ["welcome"]
    assert json.loads(called["payload"])["member_open_id"] == "ou_1"


@pytest.mark.anyio
async def test_send_welcome_uses_payload_without_feishu(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_handbook_module()
    captured: dict[str, Any] = {}

    async def fake_send_card(
        receive_id: str,
        card_json: str,
        receive_id_type: str = "chat_id",
        user_key: str = "",
        business_context_json: str = "{}",
        action_handlers_json: str = "{}",
    ) -> str:
        captured["receive_id"] = receive_id
        captured["receive_id_type"] = receive_id_type
        captured["card"] = json.loads(card_json)
        captured["business"] = json.loads(business_context_json)
        captured["handlers"] = json.loads(action_handlers_json)
        return json.dumps({"ok": True, "message_id": "om_test"})

    monkeypatch.setattr(mod, "feishu_message_send_card", fake_send_card)
    monkeypatch.setattr(
        mod,
        "_load_config",
        lambda: _async_value(
            {
                "company_name": "X",
                "welcome_title": "欢迎",
                "welcome_intro": "hi",
                "handbook_links": [],
            }
        ),
    )

    # _load_config is async — patch properly
    async def fake_cfg() -> dict[str, Any]:
        return {
            "company_name": "X",
            "welcome_title": "欢迎",
            "welcome_intro": "hi",
            "handbook_links": [{"title": "手册", "url": "https://example.com"}],
        }

    monkeypatch.setattr(mod, "_load_config", fake_cfg)

    result = await mod.handbook_onboarding_send_welcome(
        event_payload_json=json.dumps({"open_id": "ou_hire", "name": "王五"})
    )
    assert json.loads(result)["ok"] is True
    assert captured["receive_id"] == "ou_hire"
    assert captured["receive_id_type"] == "open_id"
    assert captured["handlers"]["handbook_submit"] == "handbook_onboarding_process_submit"
    assert captured["business"]["open_id"] == "ou_hire"


def _async_value(value: dict[str, Any]) -> Any:
    """Placeholder removed — tests use real async fake_cfg."""
    return value


@pytest.mark.anyio
async def test_process_submit_fail_resends(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_handbook_module()
    sends: list[dict[str, Any]] = []

    async def fake_send_card(*args: Any, **kwargs: Any) -> str:
        sends.append({"args": args, "kwargs": kwargs})
        return json.dumps({"ok": True, "message_id": f"om_{len(sends)}"})

    async def fake_cfg() -> dict[str, Any]:
        return {
            "company_name": "X",
            "welcome_title": "欢迎",
            "welcome_intro": "hi",
            "handbook_links": [],
            "required_form_fields": [
                {"name": "acked", "equals": "true", "fail_message": "need ack"},
            ],
        }

    monkeypatch.setattr(mod, "feishu_message_send_card", fake_send_card)
    monkeypatch.setattr(mod, "_load_config", fake_cfg)

    payload = {
        "dispatch": {"matched": True, "handler": "handbook_onboarding_process_submit"},
        "business_context": {"open_id": "ou_hire", "name": "王五", "attempt": 1},
        "action": {
            "form_value": {"acked": "false"},
            "value": {"action": "handbook_submit"},
        },
    }
    out = json.loads(await mod.handbook_onboarding_process_submit(json.dumps(payload)))
    assert out["passed"] is False
    assert out["resent_card"] is True
    assert len(sends) == 1


@pytest.mark.anyio
async def test_process_submit_pass_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_handbook_module()
    texts: list[tuple[str, str]] = []

    async def fake_send(receive_id: str, text: str, receive_id_type: str = "chat_id") -> str:
        texts.append((receive_id, text))
        return json.dumps({"ok": True})

    async def fake_cfg() -> dict[str, Any]:
        return {
            "required_form_fields": [
                {"name": "acked", "equals": "true", "fail_message": "need ack"},
                {
                    "name": "confirm_text",
                    "contains": "我已阅读并同意",
                    "fail_message": "need phrase",
                },
            ],
            "pass_employee_text": "员工通过",
            "pass_hr_text_template": "HR看{name}",
            "hr_notify_id": "ou_hr",
            "hr_notify_id_type": "open_id",
        }

    monkeypatch.setattr(mod, "feishu_message_send", fake_send)
    monkeypatch.setattr(mod, "_load_config", fake_cfg)

    payload = {
        "dispatch": {"matched": True, "handler": "handbook_onboarding_process_submit"},
        "business_context": {"open_id": "ou_hire", "name": "王五", "attempt": 1},
        "action": {
            "form_value": {"acked": "true", "confirm_text": "我已阅读并同意"},
            "value": {"action": "handbook_submit"},
        },
    }
    out = json.loads(await mod.handbook_onboarding_process_submit(json.dumps(payload)))
    assert out["passed"] is True
    assert out["ok"] is True
    assert ("ou_hire", "员工通过") in texts
    assert ("ou_hr", "HR看王五") in texts


# silence unused cast import if ruff complains — keep for typing clarity elsewhere
_ = cast
