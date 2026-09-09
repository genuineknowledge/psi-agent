"""Tests for ``feishu_proposal_nudge`` (message + progress card in one fire)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

nudge: Any = importlib.import_module("feishu_proposal_nudge")


@pytest.mark.anyio
async def test_nudge_sends_text_then_card(monkeypatch: pytest.MonkeyPatch) -> None:
    send_msg = AsyncMock(return_value={"ok": True, "message_id": "om_text"})
    send_card = AsyncMock(return_value=json.dumps({"ok": True, "message_id": "om_card"}))
    monkeypatch.setattr(nudge._f, "send_message_impl", send_msg)
    monkeypatch.setattr(nudge, "feishu_todo_card_send", send_card)

    items = json.dumps(
        [{"title": "M1 预备", "detail": "DDL 2099-01-01 · POC", "done": False}],
        ensure_ascii=False,
    )
    raw = await nudge.feishu_proposal_nudge(
        receive_id="ou_realuser",
        text="【方案提醒】M1 还有半天",
        items_json=items,
        title="方案进度 · demo",
        subtitle="s1-pre",
        receive_id_type="open_id",
    )
    out = json.loads(raw)
    assert out["ok"] is True
    assert out["message"]["message_id"] == "om_text"
    assert out["card"]["message_id"] == "om_card"
    send_msg.assert_awaited_once()
    send_card.assert_awaited_once()
    assert send_msg.await_args.args[0] == "ou_realuser"
    assert "方案提醒" in send_msg.await_args.args[1]
    assert send_card.await_args.kwargs["items_json"] == items


@pytest.mark.anyio
async def test_nudge_rejects_empty_items() -> None:
    raw = await nudge.feishu_proposal_nudge(
        receive_id="ou_x",
        text="hi",
        items_json="  ",
    )
    out = json.loads(raw)
    assert out["ok"] is False
    assert "items_json" in out["error"]


@pytest.mark.anyio
async def test_nudge_partial_failure_when_card_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        nudge._f,
        "send_message_impl",
        AsyncMock(return_value={"ok": True, "message_id": "om_text"}),
    )
    monkeypatch.setattr(
        nudge,
        "feishu_todo_card_send",
        AsyncMock(return_value="[Error] items_json must be a non-empty JSON array"),
    )
    raw = await nudge.feishu_proposal_nudge(
        receive_id="ou_x",
        text="hi",
        items_json='[{"title":"a","done":false}]',
    )
    out = json.loads(raw)
    assert out["ok"] is False
    assert out["message"]["ok"] is True
