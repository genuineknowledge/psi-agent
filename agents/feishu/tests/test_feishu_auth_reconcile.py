"""Reconciliation tests for the Feishu authorization "two brains".

Covers the three offline/online drift fixes:
1. 99991679-class revocation shrinks the offline capability ledger and
   surfaces ``need_auth`` instead of a raw API error (and never falls back
   to bot-owned writes after the user chose user ownership).
2. A successful user-token call unions the observed capabilities into the
   ledger, so a stale/missing record stops causing false ``need_auth``.
3. A watcher that times out re-checks ``uat.json`` (shared file, per-process
   inbox) before telling the user "还没收到你的授权".
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_feishu_impl")
_watch: Any = importlib.import_module("_feishu_auth_watch")
# ``_notify_auth_outcome`` resolves ``_core``/``live_agent`` in its own module's
# globals, so stubs must be applied on ``_feishu.auth`` and on the real
# ``_feishu_impl`` module (the alias both of them import).
_auth: Any = importlib.import_module("_feishu.auth")


def _seed_granted(tmp_path: Path, user_key: str, caps: list[str]) -> None:
    path = tmp_path / "granted_scopes.json"
    path.write_text(json.dumps({user_key: caps}), encoding="utf-8")
    _impl._granted_scopes_path = lambda: str(path)  # type: ignore[attr-defined]


def _mk_result(code: int | None, msg: str = "") -> dict[str, Any]:
    return {"ok": False, "code": code, "msg": msg, "message": f"Feishu API error {code}: {msg}"}


def test_revoked_code_detected_and_ledger_dropped(tmp_path: Path) -> None:
    user_key = "ou_revoked_user"
    _seed_granted(tmp_path, user_key, ["mindnote_read", "docs_read"])
    assert _impl.granted_capabilities(user_key) == ["docs_read", "mindnote_read"]

    result = _impl._reconcile_user_result(
        _mk_result(99991679, "invalid scope"), user_key, capabilities=["mindnote_read"]
    )

    assert result.get("need_auth") is True
    assert "99991679" in result.get("msg", "")
    # The ledger entry is gone: the next offline check re-prompts with the
    # exact capabilities instead of claiming permissions Feishu no longer grants.
    assert _impl.granted_capabilities(user_key) == []
    assert _impl.missing_capabilities(user_key, ["mindnote_read"]) == ["mindnote_read"]


def test_other_codes_leave_ledger_untouched(tmp_path: Path) -> None:
    user_key = "ou_plain_denial"
    _seed_granted(tmp_path, user_key, ["docs_read"])
    result = _impl._reconcile_user_result(_mk_result(1254302, "RolePermNotAllow"), user_key, capabilities=["docs_read"])
    assert result.get("need_auth") is None
    assert _impl.granted_capabilities(user_key) == ["docs_read"]


def test_success_records_observed_capabilities(tmp_path: Path) -> None:
    user_key = "ou_success_user"
    _seed_granted(tmp_path, user_key, [])
    ok = {"ok": True, "code": 0, "msg": "success", "data": {}}
    result = _impl._reconcile_user_result(ok, user_key, capabilities=["mindnote_read", "docs_read"])
    assert result.get("ok") is True
    assert _impl.granted_capabilities(user_key) == ["docs_read", "mindnote_read"]


def test_success_records_union_not_shrink(tmp_path: Path) -> None:
    user_key = "ou_union_user"
    _seed_granted(tmp_path, user_key, ["docs_read"])
    _impl._reconcile_user_result({"ok": True}, user_key, capabilities=["mindnote_read"])
    assert _impl.granted_capabilities(user_key) == ["docs_read", "mindnote_read"]


async def test_timeout_with_token_present_treated_as_granted(monkeypatch: Any) -> None:
    """Cross-instance grant: another process wrote uat.json while we polled an
    empty inbox - the user must not hear "还没收到你的授权"."""

    class _FakeUat:
        access_token = "t-user-ok"

    async def _valid_uat(user_key: str) -> Any:
        return _FakeUat()

    dm: list[tuple[str, str]] = []

    async def _send(receive_id: str, text: str, receive_id_type: str, on_behalf_of: str = "") -> dict[str, Any]:
        dm.append((receive_id, text))
        return {"ok": True, "message_id": "om_x"}

    monkeypatch.setattr(_impl, "_get_valid_uat", _valid_uat)
    monkeypatch.setattr(_impl, "send_message_impl", _send)
    state = _watch.WatchState(
        user_key="ou_timeout_user",
        started_at=0.0,
        timeout_seconds=10.0,
        status=_watch.STATUS_TIMEOUT,
        message="授权未完成",
    )
    await _auth._notify_auth_outcome("ou_timeout_user", state)
    assert state.status == _watch.STATUS_GRANTED
    assert dm and "还没收到" not in dm[0][1]
    assert "授权" in dm[0][1]


async def test_timeout_without_token_keeps_timeout_message(monkeypatch: Any) -> None:
    async def _no_uat(user_key: str) -> Any:
        return None

    dm: list[tuple[str, str]] = []

    async def _send(receive_id: str, text: str, receive_id_type: str, on_behalf_of: str = "") -> dict[str, Any]:
        dm.append((receive_id, text))
        return {"ok": True, "message_id": "om_x"}

    monkeypatch.setattr(_impl, "_get_valid_uat", _no_uat)
    monkeypatch.setattr(_impl, "send_message_impl", _send)
    state = _watch.WatchState(
        user_key="ou_still_waiting",
        started_at=0.0,
        timeout_seconds=10.0,
        status=_watch.STATUS_TIMEOUT,
        message="授权未完成",
    )
    await _auth._notify_auth_outcome("ou_still_waiting", state)
    assert state.status == _watch.STATUS_TIMEOUT
    assert dm and "还没收到" in dm[0][1]


@pytest.mark.anyio
async def test_write_ownership_never_falls_back_after_revocation(monkeypatch: Any) -> None:
    """After 99991679 the user chose user ownership - surface need_auth, do not
    silently produce the write under the bot's identity."""

    async def _user_call(request: Any, key: str) -> dict[str, Any]:
        return _mk_result(99991679, "invalid scope")

    monkeypatch.setattr(_impl, "_send_as_user", _user_call)
    monkeypatch.setattr(_impl, "_send_as_tenant", lambda request: {"ok": True})

    class _FakeRequest:
        def __init__(self) -> None:
            self.token_types = {"tenant_access_token"}
            self.body: dict[str, Any] = {}
            self.files = None

    req = _FakeRequest()
    result = await _impl._invoke_once(
        req, user_key="ou_writer", prefer="user", identity="user", capabilities=["docs_read"]
    )
    assert result.get("need_auth") is True
    assert result.get("ok") is not True
