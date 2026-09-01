"""The one-time confirmation code store.

The integration tests in ``test_feishu_chat_as_data`` cover the gate as the model
meets it. These cover the store's own edges — expiry, tampering, scope collisions —
which are the cases where a bug would silently *open* the gate rather than close it.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_confirm: Any = importlib.import_module("_feishu_confirm")

ENDPOINT = "DELETE /open-apis/im/v1/chats/:chat_id"


def _scope(chat_id: str = "oc_1", session: str = "s1") -> str:
    return _confirm.scope_key(session, "DELETE", "/open-apis/im/v1/chats/:chat_id", {"chat_id": chat_id})


def test_a_fresh_code_is_six_digits() -> None:
    code = _confirm.new_code()
    assert len(code) == 6
    assert code.isdigit()


def test_issue_then_redeem_succeeds() -> None:
    async def go() -> bool:
        code = await _confirm.issue(_scope(), ENDPOINT)
        return await _confirm.redeem(_scope(), code)

    assert anyio.run(go) is True


def test_redeem_without_an_issued_code_fails() -> None:
    assert anyio.run(lambda: _confirm.redeem(_scope(), "123456")) is False


def test_an_empty_code_never_passes() -> None:
    async def go() -> bool:
        await _confirm.issue(_scope(), ENDPOINT)
        return await _confirm.redeem(_scope(), "")

    assert anyio.run(go) is False


def test_a_wrong_guess_burns_the_pending_code() -> None:
    """Fail closed: a guessed code invalidates the real one rather than leaving it up
    for another try."""

    async def go() -> tuple[bool, bool]:
        code = await _confirm.issue(_scope(), ENDPOINT)
        wrong = await _confirm.redeem(_scope(), "000000" if code != "000000" else "111111")
        return wrong, await _confirm.redeem(_scope(), code)

    wrong, then_real = anyio.run(go)
    assert wrong is False
    assert then_real is False


def test_reissue_invalidates_the_previous_code() -> None:
    """A user who ignored the first code should find it dead after a second is sent.

    The two halves are asserted in separate runs on purpose: trying the stale code
    burns whatever is pending (see the wrong-guess test), so checking both against one
    store would only re-measure that.
    """

    async def stale_is_dead() -> bool:
        first = await _confirm.issue(_scope(), ENDPOINT)
        await _confirm.issue(_scope(), ENDPOINT)
        return await _confirm.redeem(_scope(), first)

    async def fresh_works() -> bool:
        await _confirm.issue(_scope(), ENDPOINT)
        second = await _confirm.issue(_scope(), ENDPOINT)
        return await _confirm.redeem(_scope(), second)

    assert anyio.run(stale_is_dead) is False
    assert anyio.run(fresh_works) is True


def test_an_expired_code_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def go() -> bool:
        code = await _confirm.issue(_scope(), ENDPOINT)
        real = _confirm.time.time
        monkeypatch.setattr(_confirm.time, "time", lambda: real() + 10_000)
        return await _confirm.redeem(_scope(), code)

    assert anyio.run(go) is False


def test_scope_separates_targets_and_sessions() -> None:
    assert _scope("oc_1") != _scope("oc_2"), "chat_id must be part of the scope"
    assert _scope("oc_1", "s1") != _scope("oc_1", "s2"), "one session's approval is not another's"
    assert _scope("oc_1") == _scope("oc_1"), "the same operation must resolve to the same scope"


def test_scope_is_filename_safe() -> None:
    """chat ids and session ids reach the filesystem, so the scope must be a digest."""
    scope = _confirm.scope_key("s/../..", "DELETE", "/open-apis/im/v1/chats/:chat_id", {"chat_id": "../../etc"})
    assert scope.isalnum()


def test_a_corrupt_store_file_does_not_open_the_gate() -> None:
    async def go() -> bool:
        await _confirm.issue(_scope(), ENDPOINT)
        path = await _confirm._store_path(_scope(), "")
        await path.write_text("not json", encoding="utf-8")
        return await _confirm.redeem(_scope(), "123456")

    assert anyio.run(go) is False


def test_a_code_field_of_the_wrong_type_is_refused() -> None:
    async def go() -> bool:
        await _confirm.issue(_scope(), ENDPOINT)
        path = await _confirm._store_path(_scope(), "")
        await path.write_text(json.dumps({"code": 123456, "expires_at": 1e12}), encoding="utf-8")
        return await _confirm.redeem(_scope(), "123456")

    assert anyio.run(go) is False


def test_a_missing_expiry_is_refused() -> None:
    """No expiry must not read as "never expires"."""

    async def go() -> bool:
        await _confirm.issue(_scope(), ENDPOINT)
        path = await _confirm._store_path(_scope(), "")
        await path.write_text(json.dumps({"code": "123456"}), encoding="utf-8")
        return await _confirm.redeem(_scope(), "123456")

    assert anyio.run(go) is False
