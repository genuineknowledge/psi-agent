"""免费模型: 哨兵值换登录 token。

C 端默认模型走云端转发, 上游供应商 key 只在云端持有, 客户端凭登录态换算力。
SPA 拿不到 token 也不该拿, 所以它填哨兵值, Gateway 在拉起 AI 子进程时替换。

这里守三条线:

1. 只有「哨兵 + 与认证服务同源」才替换 —— 否则 token 会被送去别的域名;
2. 换出来的 token 不进 ``AiInfo`` —— 那个对象会进明文快照, 也经 ``/ais`` 下发;
3. 登录态变化后 socket 要能重新取值 —— 去重键看不见 token, 不会自然重建。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, ClassVar

import anyio
import pytest
from anyio.abc import TaskGroup

from psi_agent.gateway.desktop._free_model import (
    PLACEHOLDER_API_KEY,
    is_cloud_free_model,
    make_key_resolver,
)
from psi_agent.runtime._ai_manager import AIManager

ENDPOINT = "https://account.genuineknowledge.cn"
FREE_BASE = f"{ENDPOINT}/llm/v1"


async def _close(tg: TaskGroup) -> None:
    tg.cancel_scope.cancel()
    await tg.__aexit__(None, None, None)


# ---- 同源判定 ----


def test_placeholder_on_same_origin_is_free_model() -> None:
    assert is_cloud_free_model(PLACEHOLDER_API_KEY, FREE_BASE, ENDPOINT)


def test_real_key_is_never_substituted() -> None:
    """用户自填的 key 走他自己的账号, 即使 base_url 指向云端。"""
    assert not is_cloud_free_model("sk-user-own", FREE_BASE, ENDPOINT)


def test_placeholder_on_foreign_origin_is_rejected() -> None:
    """** token 只能发给签发它的那台主机 **。

    否则任何人 (或一份被改过的 state 快照) 只要把 base_url 指向自己的域名并填上
    哨兵值, Gateway 就会把登录凭证送过去。
    """
    assert not is_cloud_free_model(PLACEHOLDER_API_KEY, "https://evil.example.com/llm/v1", ENDPOINT)


def test_scheme_must_match_too() -> None:
    """http 与 https 不同源 —— 降级到明文就能在链路上截到 token。"""
    assert not is_cloud_free_model(PLACEHOLDER_API_KEY, "http://account.genuineknowledge.cn/llm/v1", ENDPOINT)


def test_subdomain_is_not_same_origin() -> None:
    assert not is_cloud_free_model(PLACEHOLDER_API_KEY, "https://x.account.genuineknowledge.cn/llm/v1", ENDPOINT)


def test_auth_disabled_means_no_substitution() -> None:
    """认证关掉时 endpoint 是空串, 不能因此把任何 base_url 都当成同源。"""
    assert not is_cloud_free_model(PLACEHOLDER_API_KEY, FREE_BASE, "")


# ---- 解析函数 ----


def test_resolver_swaps_placeholder_for_token() -> None:
    resolve = make_key_resolver(lambda: "tok-abc", ENDPOINT)
    assert resolve(PLACEHOLDER_API_KEY, FREE_BASE) == "tok-abc"


def test_resolver_passes_real_key_through() -> None:
    resolve = make_key_resolver(lambda: "tok-abc", ENDPOINT)
    assert resolve("sk-user-own", FREE_BASE) == "sk-user-own"


def test_resolver_yields_empty_when_logged_out() -> None:
    """未登录不阻止 socket 起来 —— 否则用户看到「模型列表空了」而不是「请先登录」。

    plan 原先写的是「未登录 → 不拉起/明确失败」, 实现成了这样: 免费模型是默认
    配置, 拉不起来的表现是模型列表少一项, 更难懂。

    注意这条路上的报错很难看: 空 key 走不到云端, any-llm 在本地就抛
    ``No openai API key provided``。所以未登录的真正兜底是前端硬门禁 (SPA v2
    启动即拦), 这一支只在门禁被绕过或认证关闭时走到。
    """
    resolve = make_key_resolver(lambda: "", ENDPOINT)
    assert resolve(PLACEHOLDER_API_KEY, FREE_BASE) == ""


def test_resolver_reads_token_every_call() -> None:
    """** 不能缓存 **: socket 重建时要拿当时的新值, 不是接线那一刻的旧值。"""
    tokens = iter(["first", "second"])
    resolve = make_key_resolver(lambda: next(tokens), ENDPOINT)
    assert resolve(PLACEHOLDER_API_KEY, FREE_BASE) == "first"
    assert resolve(PLACEHOLDER_API_KEY, FREE_BASE) == "second"


# ---- 接进 AIManager ----


async def _noop_wait(path: str, *args: Any, **kwargs: Any) -> None:
    """替掉 ``_wait_socket``: 下面的假 Ai 不监听, 真等会撞 120s 超时。"""
    _ = (path, args, kwargs)


class _SpyAi:
    """记录每次构造收到的 api_key, 不真的起服务。"""

    seen: ClassVar[list[str]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.socket = kwargs["session_socket"]
        _SpyAi.seen.append(kwargs["api_key"])

    async def run(self) -> None:
        await anyio.sleep_forever()


@pytest.fixture
def spy_ai(monkeypatch: pytest.MonkeyPatch) -> type[_SpyAi]:
    _SpyAi.seen = []
    monkeypatch.setattr("psi_agent.runtime._ai_manager.Ai", _SpyAi)
    monkeypatch.setattr("psi_agent.runtime._ai_manager._wait_socket", _noop_wait)
    return _SpyAi


@pytest.mark.anyio
async def test_token_reaches_ai_but_not_aiinfo(spy_ai: type[_SpyAi]) -> None:
    """** 本次改动的核心不变式 **。

    ``AiInfo`` 会进 ``state/latest.json`` (api_key 明文) 也会经 ``/ais`` 下发给
    SPA, 所以它必须仍是哨兵; 只有交给 ``Ai`` 的那一份是真 token。
    """
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-free", _tg=tg, _resolve_key=make_key_resolver(lambda: "tok-secret", ENDPOINT))
        info = await mgr.create(
            provider="openai",
            model="deepseek-v4-flash",
            api_key=PLACEHOLDER_API_KEY,
            base_url=FREE_BASE,
        )
        assert spy_ai.seen == ["tok-secret"]
        assert info.api_key == PLACEHOLDER_API_KEY
        # 快照与 /ais 都是从这个对象取字段, 整个对象里不许出现 token。
        assert "tok-secret" not in str(asdict(info))
        assert (await mgr.list_all())[0].api_key == PLACEHOLDER_API_KEY
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_user_key_is_untouched(spy_ai: type[_SpyAi]) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-free", _tg=tg, _resolve_key=make_key_resolver(lambda: "tok-secret", ENDPOINT))
        info = await mgr.create(
            provider="openai",
            model="gpt-4o",
            api_key="sk-mine",
            base_url="https://api.openai.com/v1",
        )
        assert spy_ai.seen == ["sk-mine"]
        assert info.api_key == "sk-mine"
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_refresh_rebuilds_with_new_token(spy_ai: type[_SpyAi]) -> None:
    """登录态变化后重建 socket 拿到新 token, 而 ``AiInfo`` 一个字段都不变。"""
    token = "tok-old"
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-free", _tg=tg, _resolve_key=make_key_resolver(lambda: token, ENDPOINT))
        before = await mgr.create(
            provider="openai",
            model="deepseek-v4-flash",
            api_key=PLACEHOLDER_API_KEY,
            base_url=FREE_BASE,
        )
        snapshot = asdict(before)
        token = "tok-new"

        refreshed = await mgr.refresh_where(lambda i: is_cloud_free_model(i.api_key, i.base_url, ENDPOINT))

        assert refreshed == [before.id]
        assert spy_ai.seen == ["tok-old", "tok-new"]
        # 原地重建: id / socket 都不变, 用户看不到模型消失又出现。
        assert asdict((await mgr.list_all())[0]) == snapshot
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_refresh_leaves_user_keyed_ais_alone(spy_ai: type[_SpyAi]) -> None:
    """登出不该动用户自己配的模型 —— 它们和登录态无关。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-free", _tg=tg, _resolve_key=make_key_resolver(lambda: "tok", ENDPOINT))
        await mgr.create(
            provider="openai",
            model="gpt-4o",
            api_key="sk-mine",
            base_url="https://api.openai.com/v1",
        )
        assert await mgr.refresh_where(lambda i: is_cloud_free_model(i.api_key, i.base_url, ENDPOINT)) == []
        assert spy_ai.seen == ["sk-mine"]
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_logout_then_refresh_drops_the_token(spy_ai: type[_SpyAi]) -> None:
    """登出后重建, 交给 AI 的 key 必须为空 —— 否则登出了还能继续用。"""
    token = "tok-live"
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-free", _tg=tg, _resolve_key=make_key_resolver(lambda: token, ENDPOINT))
        await mgr.create(
            provider="openai",
            model="deepseek-v4-flash",
            api_key=PLACEHOLDER_API_KEY,
            base_url=FREE_BASE,
        )
        token = ""
        await mgr.refresh_where(lambda i: is_cloud_free_model(i.api_key, i.base_url, ENDPOINT))
        assert spy_ai.seen == ["tok-live", ""]
    finally:
        await _close(tg)


def test_default_resolver_is_identity() -> None:
    """没接线时 (认证关闭) 一切原样透传, 现有本地单用户流程零回归。"""
    mgr = AIManager(_prefix="gw-free", _tg=None)
    assert mgr._resolve_key(PLACEHOLDER_API_KEY, FREE_BASE) == PLACEHOLDER_API_KEY
    assert mgr._resolve_key("sk-x", "https://api.openai.com/v1") == "sk-x"
