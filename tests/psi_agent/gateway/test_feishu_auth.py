from __future__ import annotations

import time
from typing import cast

import pytest

from psi_agent.gateway.feishu import _auth
from psi_agent.gateway.feishu._auth import (
    DEV_OPEN_ID_ENV,
    AuthError,
    FeishuAuth,
    Identity,
    dev_open_id,
)


def test_not_configured_without_secret() -> None:
    assert FeishuAuth().configured is False
    assert FeishuAuth(app_id="cli_x").configured is False
    assert FeishuAuth(app_id="cli_x", app_secret="s").configured is True


@pytest.mark.anyio
async def test_empty_code_rejected_before_network() -> None:
    """空 code 不该打网络 —— 未配 app_secret 时也必须是 AuthError 而非连接错误。"""
    auth = FeishuAuth(app_id="cli_x", app_secret="s")
    with pytest.raises(AuthError):
        await auth.identity_from_code("")


@pytest.mark.anyio
async def test_unconfigured_raises_auth_error() -> None:
    with pytest.raises(AuthError):
        await FeishuAuth().identity_from_code("some-code")


def test_issue_lookup_revoke() -> None:
    auth = FeishuAuth(app_id="cli_x", app_secret="s")
    sid = auth.issue(Identity(open_id="ou_alice", name="Alice"))
    assert len(sid) >= 32  # 高熵, 不可猜
    got = auth.lookup(sid)
    assert got is not None
    assert (got.open_id, got.name) == ("ou_alice", "Alice")
    auth.revoke(sid)
    assert auth.lookup(sid) is None


def test_issue_returns_distinct_sids() -> None:
    auth = FeishuAuth(app_id="cli_x", app_secret="s")
    ident = Identity(open_id="ou_alice", name="Alice")
    assert auth.issue(ident) != auth.issue(ident)


def test_lookup_unknown_sid() -> None:
    assert FeishuAuth().lookup("nope") is None
    assert FeishuAuth().lookup("") is None


def test_expired_session_is_dropped() -> None:
    auth = FeishuAuth(app_id="cli_x", app_secret="s", _ttl=-1.0)
    sid = auth.issue(Identity(open_id="ou_alice", name="Alice"))
    assert auth.lookup(sid) is None
    assert sid not in auth._sessions  # 过期即清, 不留垃圾


def test_ttl_boundary_still_valid() -> None:
    auth = FeishuAuth(app_id="cli_x", app_secret="s", _ttl=60.0)
    sid = auth.issue(Identity(open_id="ou_alice", name="Alice"))
    assert auth._sessions[sid][1] > time.time()
    assert auth.lookup(sid) is not None


def test_dev_open_id_absent_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认配置下旁路不可用 —— 这条守的是验收 7。"""
    monkeypatch.delenv(DEV_OPEN_ID_ENV, raising=False)
    assert dev_open_id() == ""


def test_dev_open_id_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEV_OPEN_ID_ENV, "ou_dev")
    assert dev_open_id() == "ou_dev"


def test_dev_open_id_blank_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """设成空串/空白等于没设, 免得 ``PSI_FEISHU_DEV_OPEN_ID=`` 变成空身份旁路。"""
    monkeypatch.setenv(DEV_OPEN_ID_ENV, "   ")
    assert dev_open_id() == ""


class _FakeUserInfoResp:
    """假的 ``http.get()`` 响应, 只用来喂 ``resp.json(content_type=None)``。"""

    def __init__(self, payload: object) -> None:
        self._payload = payload

    async def __aenter__(self) -> _FakeUserInfoResp:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self, content_type: str | None = None) -> object:
        return self._payload


class _FakeUserInfoHttp:
    """假的 ``ClientSession``, 只实现 ``_fetch_user_info`` 用到的 ``get()``。"""

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def get(self, url: str, headers: dict[str, str]) -> _FakeUserInfoResp:
        return _FakeUserInfoResp(self._payload)


@pytest.mark.anyio
async def test_user_info_non_dict_data_raises_auth_error() -> None:
    """``data`` 是真值但非 dict(如上游返回一个 list)不该冒出 AttributeError, 必须是 AuthError。"""
    auth = FeishuAuth(app_id="cli_x", app_secret="s")
    http = _FakeUserInfoHttp({"code": 0, "data": ["not", "a", "dict"]})
    with pytest.raises(AuthError):
        await auth._fetch_user_info(cast("_auth.ClientSession", http), "token")
