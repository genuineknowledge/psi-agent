"""Reconciliation tests for the Feishu authorization "two brains".

Covers the offline/online drift fixes:
1. 99991679-class revocation shrinks the offline capability ledger and
   surfaces ``need_auth`` instead of a raw API error (and never falls back
   to bot-owned writes after the user chose user ownership).
2. A successful user-token call unions the observed capabilities into the
   ledger, so a stale/missing record stops causing false ``need_auth``.
3. A watcher that times out re-checks ``uat.json`` (shared file, per-process
   inbox) before telling the user "还没收到你的授权".
4. A ledger *gap* no longer vetoes the call before the token gets to prove the
   grant. All three share one principle — **the token is the ground truth and
   the ledger is only a cache** — so the fixes live in one file: "有 token 就当
   已授权" was established for the timeout path first and is extended to the
   capability gate here.

The invariant all of this defends: every key in ``uat.json`` must also be a key
in ``granted_scopes.json``. Production broke it 12 ways (21 tokens vs 9 ledger
entries) and nothing was watching, which is why it got that far.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

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


async def _stub_token_exchange(monkeypatch: Any, tmp_path: Path) -> None:
    """让 ``auth_complete_impl`` 走到两次落盘那一步, 不碰网络也不碰真的 token 目录。

    只桩掉换 token 那段 (app_access_token + POST); pending 文件缺失是被兜住的, 能力
    列表退回默认集, 正是本判据关心的两次写。
    """
    monkeypatch.setattr(_impl, "_uat_store_path", lambda: str(tmp_path / "uat.json"))
    monkeypatch.setattr(_impl, "_get_app_access_token", _stub_app_token)

    async def _token_response(url: str, body: dict[str, Any], headers: Any = None) -> dict[str, Any]:
        return {"code": 0, "data": {"access_token": "u-at-1", "expires_in": 7200, "open_id": "ou_x", "scope": ""}}

    monkeypatch.setattr(_impl, "_post_json", _token_response)


async def _stub_app_token() -> str:
    return "app-at-1"


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
async def test_write_ownership_never_falls_back_after_revocation(tmp_path: Path, monkeypatch: Any) -> None:
    """After 99991679 the user chose user ownership - surface need_auth, do not
    silently produce the write under the bot's identity.

    The ledger is seeded *complete* on purpose: this is about revocation, so the
    capability gap must not be what produces ``need_auth``. Before the self-lock fix
    this test passed through the gate's early return instead of the revocation path
    it names — the ledger had no entry for this user, so the call never happened at
    all. The stub likewise goes through ``_reconcile_user_result``, because that is
    where the real ``_send_as_user`` annotates a revocation; a stub returning a raw
    99991679 tests a shape the code never produces.
    """
    user_key = "ou_writer"
    _seed_granted(tmp_path, user_key, ["docs_read"])

    async def _user_call(request: Any, key: str) -> dict[str, Any]:
        return _impl._reconcile_user_result(_mk_result(99991679, "invalid scope"), key, capabilities=["docs_read"])

    monkeypatch.setattr(_impl, "_send_as_user", _user_call)
    monkeypatch.setattr(_impl, "_send_as_tenant", lambda request: {"ok": True})

    class _FakeRequest:
        def __init__(self) -> None:
            self.token_types = {"tenant_access_token"}
            self.body: dict[str, Any] = {}
            self.files = None

    req = _FakeRequest()
    result = await _impl._invoke_once(
        req, user_key=user_key, prefer="user", identity="user", capabilities=["docs_read"]
    )
    assert result.get("need_auth") is True
    assert result.get("ok") is not True
    # Specifically the revocation path, not the capability gate: 99991679 must be
    # what came back, otherwise this passes on the wrong mechanism again.
    assert result.get("code") == 99991679


# -- 判据 4: 账本缺记不再否决调用 (自锁) ----------------------------------------
#
# 闸门 (``_invoke_write`` 里的 ``missing_capabilities``) 曾经在账本缺记时直接返回
# ``need_auth``, 而账本的自愈 (``_record_observed_capabilities``) 挂在
# ``_send_as_user`` 里 —— 闸门的**下游**。于是缺记 → 被拦 → 永远不会有成功调用 →
# 账本永远补不上 → 用户永远被要求授权。生产上 21 个 token 对 9 条账本。
#
# 这批判据全部走 ``_invoke_once`` 而不是直接调闸门函数: 那条链上有多处兜底
# (``_is_permission_error`` 退回 bot、``user_res is None``、``need_auth``), 只测闸门
# 单函数会让「兜底提前吃掉结论」的假绿照旧成立。


class _FakeWriteRequest:
    """``_invoke_once`` 会 ``_restorable`` 它, 故三个字段都得在。"""

    def __init__(self) -> None:
        self.token_types = {"tenant_access_token"}
        self.body: dict[str, Any] = {}
        self.files = None


async def _invoke_user_write(capabilities: list[str], user_key: str = "ou_gap") -> dict[str, Any]:
    return await _impl._invoke_once(
        _FakeWriteRequest(), user_key=user_key, prefer="user", identity="user", capabilities=capabilities
    )


@pytest.mark.anyio
async def test_ledger_gap_with_working_token_goes_through_and_heals(tmp_path: Path, monkeypatch: Any) -> None:
    """自愈闭环: 账本缺记 + token 可用 → 打通, 且账本被补上。

    这是自锁的正面解: 修好之前, 这次调用连 ``_send_as_user`` 都到不了。
    """
    user_key = "ou_gap_heals"
    _seed_granted(tmp_path, user_key, [])  # 账本查无此人
    assert _impl.missing_capabilities(user_key, ["docs_read"]) == ["docs_read"]

    sent: list[str] = []

    async def _user_call(request: Any, key: str) -> dict[str, Any]:
        sent.append(key)
        # 真实的 ``_send_as_user`` 会在成功后自己对账; 这里照它的样子调一次, 否则
        # 测的就不是「账本靠成功调用补上」而是「桩函数顺手写了个文件」。
        return _impl._reconcile_user_result({"ok": True, "code": 0}, key, capabilities=["docs_read"])

    monkeypatch.setattr(_impl, "_send_as_user", _user_call)

    async def _tenant_must_not_run(request: Any) -> dict[str, Any]:
        raise AssertionError("用户身份的写入退回了 bot 身份")

    monkeypatch.setattr(_impl, "_send_as_tenant", _tenant_must_not_run)

    result = await _invoke_user_write(["docs_read"], user_key)

    assert result.get("ok") is True, f"账本缺记仍然否决了调用: {result}"
    assert result.get("need_auth") is not True
    assert sent == [user_key], "根本没拿 token 试打"
    # 账本补上了 —— 下一次不必再试打。
    assert _impl.granted_capabilities(user_key) == ["docs_read"]
    assert _impl.missing_capabilities(user_key, ["docs_read"]) == []


@pytest.mark.anyio
async def test_healed_ledger_means_the_second_call_is_not_a_probe(tmp_path: Path, monkeypatch: Any) -> None:
    """补上之后第二次调用不再走试打路径 —— 账本重新起到加速缓存的作用。

    缺这条, 「把闸门整个删掉」也能让上一条全绿, 而那意味着账本从此永不被读, 每次
    写入都白花一趟往返去发现权限不够。判据是**差分**: 同样两次调用, 第一次账本有
    意见、第二次没有。
    """
    user_key = "ou_gap_second_call"
    _seed_granted(tmp_path, user_key, [])
    missing_seen: list[list[str]] = []

    async def _user_call(request: Any, key: str) -> dict[str, Any]:
        missing_seen.append(_impl.missing_capabilities(key, ["docs_read"]))
        return _impl._reconcile_user_result({"ok": True, "code": 0}, key, capabilities=["docs_read"])

    monkeypatch.setattr(_impl, "_send_as_user", _user_call)

    assert (await _invoke_user_write(["docs_read"], user_key)).get("ok") is True
    assert (await _invoke_user_write(["docs_read"], user_key)).get("ok") is True

    assert missing_seen == [["docs_read"], []], (
        f"第一次该是「账本缺记、靠 token 证明」, 第二次该是「账本已知」: {missing_seen}"
    )


@pytest.mark.anyio
async def test_ledger_gap_without_any_token_still_asks_for_auth(tmp_path: Path, monkeypatch: Any) -> None:
    """阴性: 账本缺记且压根没有 token → 仍然提示授权。

    修法不能退化成「永远不提示授权」。``_send_as_user`` 返回 ``None`` 表示没有可用
    token, 那时没有任何东西能证明这个 grant 存在。
    """
    user_key = "ou_gap_no_token"
    _seed_granted(tmp_path, user_key, [])

    async def _no_token(request: Any, key: str) -> None:
        return None

    monkeypatch.setattr(_impl, "_send_as_user", _no_token)

    async def _tenant_must_not_run(request: Any) -> dict[str, Any]:
        raise AssertionError("没有用户 token 时悄悄用 bot 身份完成了写入")

    monkeypatch.setattr(_impl, "_send_as_tenant", _tenant_must_not_run)

    result = await _invoke_user_write(["docs_read"], user_key)

    assert result.get("need_auth") is True, f"没有 token 却不提示授权: {result}"
    assert result.get("ok") is not True
    # 账本有意见时要点名缺哪个能力, 授权页才不会要一整套。
    assert result.get("need_capabilities") == ["docs_read"]


@pytest.mark.anyio
async def test_ledger_gap_with_dead_token_still_asks_for_auth(tmp_path: Path, monkeypatch: Any) -> None:
    """阴性: 账本缺记 + token 已死 (99991679 撤销) → 仍然提示授权, 且不退回 bot。

    与上一条分开是必须的: 上一条走的是 ``user_res is None``, 这一条走的是
    ``need_auth`` 那个兜底分支 —— 实测把其中任一条删掉, 另一条照旧全绿。
    """
    user_key = "ou_gap_dead_token"
    _seed_granted(tmp_path, user_key, [])

    async def _revoked(request: Any, key: str) -> dict[str, Any]:
        return _impl._reconcile_user_result(_mk_result(99991679, "invalid scope"), key, capabilities=["docs_read"])

    monkeypatch.setattr(_impl, "_send_as_user", _revoked)

    async def _tenant_must_not_run(request: Any) -> dict[str, Any]:
        raise AssertionError("撤销后退回了 bot 身份")

    monkeypatch.setattr(_impl, "_send_as_tenant", _tenant_must_not_run)

    result = await _invoke_user_write(["docs_read"], user_key)

    assert result.get("need_auth") is True, f"token 已撤销却不提示授权: {result}"
    assert result.get("ok") is not True


@pytest.mark.anyio
async def test_genuine_missing_scope_asks_for_auth_instead_of_bot_fallback(tmp_path: Path, monkeypatch: Any) -> None:
    """真的缺权限 (账本说缺 + 飞书也拒) → 提示授权, **不**掉进 bot 兜底。

    这条盯的是去掉闸门早退之后新出现的风险: 权限类失败原本一律退回 bot 身份
    (「是关于目标文档的事实」), 可当账本也说这个能力没授权过时, 两个事实一致, 那
    就真是缺 grant —— 用户选了自己署名, 悄悄用 bot 做完等于换了归属。
    """
    user_key = "ou_genuine_missing"
    _seed_granted(tmp_path, user_key, [])

    async def _denied(request: Any, key: str) -> dict[str, Any]:
        return _mk_result(99991672, "permission denied")

    monkeypatch.setattr(_impl, "_send_as_user", _denied)

    async def _tenant_must_not_run(request: Any) -> dict[str, Any]:
        raise AssertionError("账本与飞书都说缺权限, 却用 bot 身份完成了写入")

    monkeypatch.setattr(_impl, "_send_as_tenant", _tenant_must_not_run)

    result = await _invoke_user_write(["docs_read"], user_key)

    assert result.get("need_auth") is True, f"真缺权限却没提示授权: {result}"
    assert result.get("need_capabilities") == ["docs_read"]


@pytest.mark.anyio
async def test_resource_denial_with_complete_ledger_still_falls_back_to_bot(tmp_path: Path, monkeypatch: Any) -> None:
    """反向判据: 账本齐全时的资源级拒绝照旧退回 bot —— 上一条不许把这条一起改掉。

    账本说能力都在 + 飞书拒这一篇文档 = 关于**目标**的事实, 不是关于授权的。那条
    退回 bot 完成写入的路径 (图片进去了标题没进去那个事故) 必须还在。
    """
    user_key = "ou_resource_denied"
    _seed_granted(tmp_path, user_key, ["docs_read"])

    async def _denied(request: Any, key: str) -> dict[str, Any]:
        return _mk_result(1770032, "no permission on this block")

    monkeypatch.setattr(_impl, "_send_as_user", _denied)
    tenant_ran: list[bool] = []

    async def _tenant(request: Any) -> dict[str, Any]:
        tenant_ran.append(True)
        return {"ok": True, "code": 0}

    monkeypatch.setattr(_impl, "_send_as_tenant", _tenant)

    result = await _invoke_user_write(["docs_read"], user_key)

    assert tenant_ran == [True], "账本齐全时的资源级拒绝没有退回 bot"
    assert result.get("ok") is True


@pytest.mark.anyio
async def test_bot_identity_never_touches_the_user_token_even_with_a_ledger_gap(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """``identity="bot"`` 那条分支仍然绝不去碰用户 token —— 账本缺记也不例外。

    改动把「先拿 token 试打」加进了用户身份那条路; 这条钉住它没有溢到 bot 分支。
    """
    user_key = "ou_bot_choice"
    _seed_granted(tmp_path, user_key, [])

    async def _user_must_not_run(request: Any, key: str) -> dict[str, Any]:
        raise AssertionError("identity=bot 却读了用户 token")

    monkeypatch.setattr(_impl, "_send_as_user", _user_must_not_run)

    async def _tenant(request: Any) -> dict[str, Any]:
        return {"ok": True, "code": 0}

    monkeypatch.setattr(_impl, "_send_as_tenant", _tenant)

    result = await _impl._invoke_once(
        _FakeWriteRequest(), user_key=user_key, prefer="user", identity="bot", capabilities=["docs_read"]
    )
    assert result.get("ok") is True


# -- 判据 5: uat.json 的 key 必须是 granted_scopes.json 的子集 ------------------


def _write_maps(tmp_path: Path, tokens: dict[str, Any], ledger: dict[str, Any]) -> tuple[Path, Path]:
    uat = tmp_path / "uat.json"
    granted = tmp_path / "granted_scopes.json"
    uat.write_text(json.dumps(tokens), encoding="utf-8")
    granted.write_text(json.dumps(ledger), encoding="utf-8")
    return uat, granted


def _keys_missing_from_ledger(uat_path: Path, granted_path: Path) -> list[str]:
    """``uat.json`` 里有、``granted_scopes.json`` 里没有的 key。

    读原始 JSON 而不是走 ``granted_capabilities``: 后者对「key 不存在」和「key 存在
    但能力列表为空」返回同一个 ``[]``, 而这两件事正是本不变量要区分的 —— 前者是自锁,
    后者是已记录的空授权。
    """
    tokens = json.loads(uat_path.read_text(encoding="utf-8"))
    ledger = json.loads(granted_path.read_text(encoding="utf-8"))
    return sorted(set(tokens) - set(ledger))


def test_token_keys_must_be_a_subset_of_ledger_keys(tmp_path: Path) -> None:
    """不变量: 有 token 的用户必须在账本里有条目。

    一破就是用户被反复要求授权, 而生产上积到 12 个才被发现, 正因为无人盯。
    """
    uat, granted = _write_maps(
        tmp_path,
        {"ou_a": {"access_token": "t1"}, "ou_b": {"access_token": "t2"}},
        {"ou_a": ["docs_read"], "ou_b": []},
    )
    assert _keys_missing_from_ledger(uat, granted) == []


def test_the_subset_invariant_catches_the_production_skew(tmp_path: Path) -> None:
    """反例: 生产那个形状 (token 多、账本少) 必须被判为失败。

    缺这条, 上一条无法区分「不变量成立」和「判据压根没在比较」—— 后者同样全绿。
    """
    uat, granted = _write_maps(
        tmp_path,
        {"ou_a": {"access_token": "t1"}, "ou_luolin": {"access_token": "t2"}},
        {"ou_a": ["docs_read"]},
    )
    assert _keys_missing_from_ledger(uat, granted) == ["ou_luolin"]


@pytest.mark.anyio
async def test_auth_complete_writes_the_ledger_before_the_token(tmp_path: Path, monkeypatch: Any) -> None:
    """两次写的**顺序**就是修法: 账本先落盘, token 后落盘。

    两个文件做不成一次原子写 (token 那半属于 SDK 的 ``FileTokenStore``), 能选的只有
    半途失败时倒向哪一边。账本先写 → 最坏留下「账本知道一个没 token 的能力」, 白花
    一次调用后如实提示授权; token 先写 → 留下「有 token 无账本」, 也就是自锁。
    """
    order: list[str] = []
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted_scopes.json"))

    def _record(user_key: str, capabilities: list[str]) -> bool:
        order.append("ledger")
        return True

    class _Store:
        async def set(self, key: str, uat: Any) -> None:
            order.append("token")

    monkeypatch.setattr(_auth, "_record_granted_capabilities", _record)
    monkeypatch.setattr(_impl, "_get_token_store", lambda: _Store())
    await _stub_token_exchange(monkeypatch, tmp_path)

    result = await _auth.auth_complete_impl("code-1", "ou_order")

    assert result.get("ok") is True, f"授权没走通: {result}"
    assert order == ["ledger", "token"], f"顺序反了 (token 先写就是自锁的成因): {order}"


@pytest.mark.anyio
async def test_auth_complete_aborts_when_the_ledger_write_fails(tmp_path: Path, monkeypatch: Any) -> None:
    """账本写失败 → 整个授权不生效, 且**留痕**。

    原先 ``contextlib.suppress(OSError)`` 把写失败整个吞掉, 静默产出「有 token 无
    账本」。日志断言不用 ``caplog`` —— loguru 不进 caplog, 阴性用例会假绿; 自己挂
    一个 sink 并同时记 level。
    """
    records: list[tuple[str, str]] = []
    sink_id = logger.add(lambda msg: records.append((msg.record["level"].name, msg.record["message"])), level="DEBUG")
    try:
        monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted_scopes.json"))
        token_written: list[str] = []

        class _Store:
            async def set(self, key: str, uat: Any) -> None:
                token_written.append(key)

        def _boom(path: str, data: dict[str, Any]) -> None:
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(_auth, "_write_json_map", _boom)
        monkeypatch.setattr(_impl, "_get_token_store", lambda: _Store())
        await _stub_token_exchange(monkeypatch, tmp_path)

        result = await _auth.auth_complete_impl("code-1", "ou_ledger_fails")
    finally:
        logger.remove(sink_id)

    assert result.get("ok") is not True, f"账本写失败却报授权成功: {result}"
    assert token_written == [], "账本写失败后仍然存了 token —— 正是要避免的那个偏斜"
    assert any(level == "ERROR" and "granted_scopes" in message for level, message in records), (
        f"账本写失败没留下 ERROR 痕迹: {records}"
    )
