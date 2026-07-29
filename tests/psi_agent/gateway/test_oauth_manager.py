from __future__ import annotations

import pytest

from psi_agent.gateway import _oauth_manager
from psi_agent.gateway._oauth_manager import OAuthRelay


@pytest.mark.anyio
async def test_deliver_then_take_returns_code_once() -> None:
    relay = OAuthRelay()
    await relay.deliver("st-1", code="the-code")

    got = await relay.take("st-1")
    assert got is not None
    assert got.code == "the-code"
    assert got.error == ""

    # 一次性: 取过即作废, 同一个 state 不能被重复兑换。
    assert await relay.take("st-1") is None


@pytest.mark.anyio
async def test_take_unknown_state_returns_none() -> None:
    relay = OAuthRelay()
    assert await relay.take("never-seen") is None
    assert await relay.take("") is None


@pytest.mark.anyio
async def test_deliver_empty_state_rejected() -> None:
    relay = OAuthRelay()
    with pytest.raises(ValueError, match="state"):
        await relay.deliver("", code="x")


@pytest.mark.anyio
async def test_error_is_carried_through() -> None:
    relay = OAuthRelay()
    await relay.deliver("st-e", error="access_denied")
    got = await relay.take("st-e")
    assert got is not None
    assert got.code == ""
    assert got.error == "access_denied"


@pytest.mark.anyio
async def test_expired_entry_is_swept(monkeypatch: pytest.MonkeyPatch) -> None:
    relay = OAuthRelay()
    await relay.deliver("st-old", code="stale")
    # 让下一次访问看到「已过 TTL」, 过期项必须被清掉而不是被取回。
    monkeypatch.setattr(_oauth_manager, "_TTL_SECONDS", -1.0)
    assert await relay.take("st-old") is None


@pytest.mark.anyio
async def test_pending_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """待取项有上限, 防止无人取件时内存无界增长。"""
    monkeypatch.setattr(_oauth_manager, "_MAX_PENDING", 3)
    relay = OAuthRelay()
    for i in range(5):
        await relay.deliver(f"st-{i}", code=str(i))
    # 最旧的被淘汰, 最新的仍在。
    assert await relay.take("st-0") is None
    assert (await relay.take("st-4")) is not None
