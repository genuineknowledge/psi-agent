from __future__ import annotations

import json

import pytest

from psi_agent.gateway.feishu import _jsapi
from psi_agent.gateway.feishu._jsapi import FeishuJsapiSigner, JsapiError, signature_for


def test_signature_uses_official_field_order() -> None:
    expected = "0840145e5370a67a2080b86fe8cc30264b674014"
    assert signature_for("ticket", "nonce", "1700000000", "https://example.com/") == expected


@pytest.mark.anyio
async def test_config_for_url_strips_fragment_and_signs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeHttp()
    monkeypatch.setattr(_jsapi, "ClientSession", lambda *a, **kw: fake)
    signer = FeishuJsapiSigner(app_id="cli_x", app_secret="s")
    cfg = await signer.config_for_url("https://example.com/feishu-web/?from=work#/task")

    assert cfg["appId"] == "cli_x"
    assert cfg["url"] == "https://example.com/feishu-web/?from=work"
    assert cfg["signature"] == signature_for(
        "ticket-1",
        cfg["nonceStr"],
        cfg["timestamp"],
        cfg["url"],
    )


@pytest.mark.anyio
async def test_invalid_url_is_rejected() -> None:
    signer = FeishuJsapiSigner(app_id="cli_x", app_secret="s")
    with pytest.raises(JsapiError):
        await signer.config_for_url("javascript:alert(1)")


@pytest.mark.anyio
async def test_any_host_is_signed_on_purpose(monkeypatch: pytest.MonkeyPatch) -> None:
    """**刻意为之, 别加 host 白名单**: 只为前端交来的那个 URL 签名, 不判来源 host。

    前端传的是 ``location.href``, 同一个部署可能经公网域名 / 内网域名 / 带端口 / 飞书客户端
    内嵌浏览器等多种地址被打开 —— 硬编码白名单会在**合法**场景下打断免登, 却挡不住攻击者
    (签名只在那一个 URL 的页面上有效)。理由全文见 ``_jsapi`` 模块 docstring。

    这条用例同时钉住**安全性质**: 该接口不下发任何凭据 —— 拿任意域名来要签名, 拿回去的
    也只有 appId(公开值)/时间戳/随机串/签名, 没有 jsapi_ticket, 没有 app_secret。
    """
    fake = _FakeHttp()
    monkeypatch.setattr(_jsapi, "ClientSession", lambda *a, **kw: fake)
    signer = FeishuJsapiSigner(app_id="cli_x", app_secret="top-secret")

    cfg = await signer.config_for_url("https://evil.example.com/phish.html")

    assert cfg["url"] == "https://evil.example.com/phish.html"
    assert cfg["signature"] == signature_for("ticket-1", cfg["nonceStr"], cfg["timestamp"], cfg["url"])
    # 没有凭据跟着出去: 返回体里只有那四个字段。
    assert set(cfg) == {"appId", "timestamp", "nonceStr", "signature", "url"}
    assert "ticket" not in json.dumps(cfg)
    assert "top-secret" not in json.dumps(cfg)


@pytest.mark.anyio
async def test_unconfigured_signer_is_rejected() -> None:
    with pytest.raises(JsapiError):
        await FeishuJsapiSigner().config_for_url("https://example.com/")


class _FakeResp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    async def __aenter__(self) -> _FakeResp:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self, content_type: str | None = None) -> object:
        return self._payload


class _FakeHttp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def __aenter__(self) -> _FakeHttp:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def post(self, url: str, **kwargs: object) -> _FakeResp:
        self.calls.append((url, kwargs.get("headers")))
        if "tenant_access_token" in url:
            return _FakeResp({"code": 0, "msg": "ok", "tenant_access_token": "t-1", "expire": 7200})
        return _FakeResp({"code": 0, "msg": "ok", "data": {"ticket": "ticket-1", "expire_in": 7200}})


@pytest.mark.anyio
async def test_tokens_are_cached_after_first_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeHttp()
    monkeypatch.setattr(_jsapi, "ClientSession", lambda *a, **kw: fake)

    signer = FeishuJsapiSigner(app_id="cli_x", app_secret="s")
    first = await signer.config_for_url("https://example.com/feishu-web/")
    second = await signer.config_for_url("https://example.com/feishu-web/")

    assert first["appId"] == second["appId"] == "cli_x"
    assert first["url"] == second["url"] == "https://example.com/feishu-web/"
    # 每次配置只各取一次 tenant token 与 jsapi_ticket, 第二次命中缓存。
    assert len(fake.calls) == 2
