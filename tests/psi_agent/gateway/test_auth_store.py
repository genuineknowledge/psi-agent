"""AuthStore 的落盘形态与降级自曝。

重点是「盘上到底是明文还是密文」和「``credentialEncrypted`` 有没有如实上报」——
这两者一旦脱节, 界面会告诉用户凭证已加密, 而磁盘上是明文。

``_keyring`` 的注入约定: ``None`` = 用真实 keyring 库 (不在测试里走这条),
``False`` = 钥匙串不可用, 传对象 = 假钥匙串。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest

from psi_agent.gateway.desktop._auth_store import AuthStore

_TOKEN = "tok-abcdef-0123456789"


class FakeKeyring:
    """内存钥匙串。够用: AuthStore 只要 get_password / set_password。"""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._data[(service, username)] = password


def _store(tmp_path: Path, keyring: Any) -> AuthStore:
    return AuthStore(_path=anyio.Path(tmp_path) / "auth.enc.json", _keyring=keyring)


async def _raw(store: AuthStore) -> dict[str, Any]:
    return json.loads(await store._path.read_text(encoding="utf-8"))


@pytest.mark.anyio
async def test_no_keyring_saves_plaintext_and_admits_it(tmp_path: Path) -> None:
    """钥匙串不可用: 明文落盘, 且 ``encrypted`` 必须如实报 False。"""
    store = _store(tmp_path, False)
    await store.save_token(_TOKEN)

    data = await _raw(store)
    assert data["enc"] is False
    assert data["token"] == _TOKEN, "没有钥匙串时就是明文, 这是已知降级"
    assert store.encrypted is False, "降级必须自曝, 不能上报已加密"
    assert await store.load_token() == _TOKEN


@pytest.mark.anyio
async def test_keyring_available_encrypts(tmp_path: Path) -> None:
    store = _store(tmp_path, FakeKeyring())
    await store.save_token(_TOKEN)

    data = await _raw(store)
    assert data["enc"] is True
    assert _TOKEN not in json.dumps(data), "密文里不该出现原文"
    assert store.encrypted is True
    assert await store.load_token() == _TOKEN


@pytest.mark.anyio
async def test_plaintext_credential_is_migrated_on_load(tmp_path: Path) -> None:
    """装上 keyring 重启后, 已有的明文凭证必须就地加密。

    回归: 过去 ``load_token`` 遇到明文直接 return, 既不迁移也不修正
    ``_encrypted`` (默认 True) —— 用户按提示装了 keyring, 黄条消失了,
    盘上却还是明文, 比不报警更坏。
    """
    # 阶段 1: 没有钥匙串, 明文落盘
    plain_store = _store(tmp_path, False)
    await plain_store.save_token(_TOKEN)
    assert (await _raw(plain_store))["enc"] is False

    # 阶段 2: 同一个文件, 这次钥匙串可用 (等价于用户装了 keyring 后重启)
    upgraded = _store(tmp_path, FakeKeyring())
    assert await upgraded.load_token() == _TOKEN, "迁移不能丢登录态"

    data = await _raw(upgraded)
    assert data["enc"] is True, "明文凭证应已就地重新加密"
    assert _TOKEN not in json.dumps(data), "盘上不该再有明文"
    assert upgraded.encrypted is True
    # 再读一遍走的是密文分支, 仍要能解出来
    assert await upgraded.load_token() == _TOKEN


@pytest.mark.anyio
async def test_plaintext_stays_plaintext_without_keyring(tmp_path: Path) -> None:
    """钥匙串仍不可用时, 读明文不迁移, 也不谎报加密。"""
    store = _store(tmp_path, False)
    await store.save_token(_TOKEN)

    fresh = _store(tmp_path, False)
    assert await fresh.load_token() == _TOKEN
    assert (await _raw(fresh))["enc"] is False
    assert fresh.encrypted is False


@pytest.mark.anyio
async def test_encrypted_predicts_keyring_before_any_token(tmp_path: Path) -> None:
    """还没有凭证时, ``encrypted`` 回答的是「现在登录会不会明文落盘」。

    没碰过盘就没有形态可报, 乐观默认 True 会在无钥匙串的机器上瞒掉降级。
    """
    assert _store(tmp_path, False).encrypted is False
    assert _store(tmp_path, FakeKeyring()).encrypted is True


@pytest.mark.anyio
async def test_checksum_mismatch_reads_as_logged_out(tmp_path: Path) -> None:
    """钥匙串密钥换了 (重置/换机器): 解出乱码不能当成有效 token。"""
    store = _store(tmp_path, FakeKeyring())
    await store.save_token(_TOKEN)

    # 换一副空钥匙串 → 生成新密钥 → 解密结果与校验和不符
    other = _store(tmp_path, FakeKeyring())
    assert await other.load_token() == "", "校验和不匹配应视为未登录"


@pytest.mark.anyio
async def test_clear_token_keeps_device_key(tmp_path: Path) -> None:
    """登出只清 token: device_key 留着, 否则重新登录会被云端当成新设备。"""
    store = _store(tmp_path, FakeKeyring())
    key = await store.device_key()
    await store.save_token(_TOKEN)
    await store.clear_token()

    assert await store.load_token() == ""
    assert await store.device_key() == key
