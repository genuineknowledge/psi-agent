from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from psi_agent.gateway._keys import OAUTH_KEY
from psi_agent.gateway._oauth_manager import OAuthRelay
from psi_agent.gateway.server import _oauth_callback, _oauth_take_code


def _request(path: str, relay: OAuthRelay) -> web.Request:
    """构造一个只带 ``app`` 与 query 的真 Request —— handler 只碰这两样。"""
    app = web.Application()
    app[OAUTH_KEY] = relay
    return make_mocked_request("GET", path, app=app)


def _body(resp: web.Response) -> str:
    assert resp.text is not None
    return resp.text


@pytest.mark.anyio
async def test_callback_stores_code_and_shows_success_page() -> None:
    relay = OAuthRelay()

    resp = await _oauth_callback(_request("/oauth/callback?code=c0de&state=st", relay))
    assert resp.status == 200
    assert "text/html" in resp.content_type
    # 页面必须明说不用复制任何东西 —— 这正是本改动要消除的动作。
    assert "不用复制" in _body(resp)

    got = await relay.take("st")
    assert got is not None
    assert got.code == "c0de"


@pytest.mark.anyio
async def test_callback_without_state_is_rejected() -> None:
    resp = await _oauth_callback(_request("/oauth/callback?code=c0de", OAuthRelay()))
    assert resp.status == 400


@pytest.mark.anyio
async def test_callback_records_upstream_error() -> None:
    relay = OAuthRelay()
    resp = await _oauth_callback(_request("/oauth/callback?state=st&error=access_denied", relay))
    assert resp.status == 400
    got = await relay.take("st")
    assert got is not None
    assert got.error == "access_denied"


@pytest.mark.anyio
async def test_callback_with_neither_code_nor_error_is_an_error() -> None:
    relay = OAuthRelay()
    resp = await _oauth_callback(_request("/oauth/callback?state=st", relay))
    assert resp.status == 400
    got = await relay.take("st")
    assert got is not None
    assert got.code == ""
    assert got.error


@pytest.mark.anyio
async def test_take_code_returns_code_then_404s() -> None:
    relay = OAuthRelay()
    await _oauth_callback(_request("/oauth/callback?code=c0de&state=st", relay))

    resp = await _oauth_take_code(_request("/oauth/code?state=st", relay))
    assert resp.status == 200
    assert json.loads(_body(resp))["code"] == "c0de"

    # 一次性: 第二次取件必须落空, 别让同一个 code 被兑换两次。
    resp2 = await _oauth_take_code(_request("/oauth/code?state=st", relay))
    assert resp2.status == 404


@pytest.mark.anyio
async def test_take_code_requires_state() -> None:
    resp = await _oauth_take_code(_request("/oauth/code", OAuthRelay()))
    assert resp.status == 400


@pytest.mark.anyio
async def test_take_code_surfaces_error_payload() -> None:
    relay = OAuthRelay()
    await _oauth_callback(_request("/oauth/callback?state=st&error=access_denied", relay))
    resp = await _oauth_take_code(_request("/oauth/code?state=st", relay))
    assert resp.status == 200
    assert json.loads(_body(resp))["error"] == "access_denied"
