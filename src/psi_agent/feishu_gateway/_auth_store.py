"""AuthStore —— 登录凭证与设备标识的本机落盘 (系统钥匙串加密)。

与 ``_state.py`` 同区域 (AppData) 但**刻意分开一个文件**: 钥匙串是本仓库里唯一
一处平台相关代码 (Windows→Credential Manager / macOS→Keychain / Linux→Secret
Service), 隔开便于在 CI (无钥匙串) 里替换成注入的假实现。

三点设计取舍:

1. **token 不进 ``state/latest.json``。** 现有 ``GatewayState.save`` 把 AI 的
   ``api_key`` 明文写进快照; 登录凭证不再踩这个坑 —— 快照只存业务配置, 凭证走
   本文件、经钥匙串加密。

2. **钥匙串不可用时降级到明文, 但必须 warning。** 桌面环境千差万别 (Linux 无
   Secret Service、CI 容器无 D-Bus), 硬失败会让整个客户端起不来; 静默降级又
   等于骗人。故降级 + 明确告警, 并在文件里落 ``"enc": false`` 标记, 便于排查。

3. **``device_key`` 与 token 分开存。** 它不是秘密, 但必须**跨重装稳定** ——
   云端 ``devices.UNIQUE(user_id, device_key)`` 靠它保证重装不刷出新设备。放在
   同一个文件里, 即使 token 被清 (登出) 也不重新生成。

保护边界是「操作系统用户」而非「进程」: 同机恶意程序可以当前用户身份解密。这是
桌面客户端的固有限制, 不是本模块的疏漏。
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import anyio
from loguru import logger

from psi_agent._appdata import resolve_appdata_root

# 钥匙串里的条目名。service 固定, username 用来区分不同用途的密钥。
_KEYRING_SERVICE = "psi-agent"
_KEYRING_USERNAME = "auth-store-key"

_FILENAME = "auth.enc.json"


def _new_device_key() -> str:
    """高熵随机串。不含机器指纹 —— 指纹会随硬件变动而变, 反而破坏「重装稳定」。"""
    return secrets.token_urlsafe(24)


def _checksum(plain: str) -> str:
    """明文的短摘要, 用来判断解密结果是否可信。

    只存摘要前 16 位十六进制: 够用来发现「解出乱码」, 又不足以对 token 本身
    做离线暴力破解 (token 是 32 字节高熵随机串)。
    """
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()[:16]


def _xor(data: bytes, key: bytes) -> bytes:
    """用钥匙串里的密钥做流式异或。

    这里**不是**在自制加密算法: 真正的秘密保护由操作系统钥匙串承担 (密钥本身存在
    Credential Manager / Keychain 里, 磁盘上没有), 此处只需让磁盘文件不可直接读出
    token。若将来引入 cryptography 依赖, 换成 AES-GCM 即可, 文件格式已留 ``v`` 字段。
    """
    if not key:
        return data
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


@dataclass
class AuthStore:
    """凭证落盘 ``{appdata}/auth.enc.json`` + ``device_key`` 持久化。

    ``_keyring`` 可注入: 生产传 None → 用 ``keyring`` 库; CI 传假实现避开钥匙串。
    """

    _path: anyio.Path = field(default_factory=lambda: anyio.Path(_FILENAME))
    _keyring: Any = None
    _key_cache: bytes = b""
    # 盘上凭证是否加密。``None`` = 还没读写过文件, 此时只能拿"钥匙串能不能用"
    # 当预测值 (见 ``encrypted``)。**不要**给它一个乐观默认: 那样会在还没碰过盘时
    # 上报「已加密」, 而盘上可能是明文。
    _encrypted: bool | None = None

    @classmethod
    async def from_appdata(cls, appdata_root: str = "", keyring_mod: Any = None) -> AuthStore:
        """建一个落在 *appdata_root* 下的凭证仓 (空 → 自动解析)。"""
        root = appdata_root.strip() or await resolve_appdata_root()
        return cls(_path=anyio.Path(root) / _FILENAME, _keyring=keyring_mod)

    # ---- 钥匙串 ----
    def _load_keyring(self) -> Any:
        if self._keyring is not None:
            return self._keyring
        try:
            # 刻意延迟导入: keyring 是本期新增的唯一第三方依赖, 且在无钥匙串的
            # 环境 (CI 容器 / 缺 Secret Service 的 Linux) 可能装不上。放到顶层会让
            # 「没装 keyring」变成整个 Gateway 起不来, 而不是降级 + 告警。
            import keyring  # noqa: PLC0415
        except Exception as e:
            logger.warning(
                f"keyring 不可用 ({e!r}); 登录凭证将以**明文**落盘。"
                " 这是降级行为, 不是预期状态 —— 桌面环境请安装 keyring 后重启。"
            )
            self._keyring = False
            return False
        self._keyring = keyring
        return keyring

    def _secret_key(self) -> bytes:
        """取 (或首次生成) 加密密钥。密钥只在钥匙串里, 磁盘上没有。

        返回空串 = 钥匙串不可用。这里**不动** ``_encrypted``: 那个字段记的是盘上
        凭证的真实形态, 由读写路径维护; 密钥可用不等于盘上已经加密。
        """
        if self._key_cache:
            return self._key_cache
        kr = self._load_keyring()
        if not kr:
            return b""
        try:
            raw = kr.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
            if not raw:
                raw = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
                kr.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, raw)
                logger.info("已在系统钥匙串中创建凭证加密密钥")
            self._key_cache = raw.encode()
            return self._key_cache
        except Exception as e:
            logger.warning(
                f"读写系统钥匙串失败 ({e!r}); 登录凭证将以**明文**落盘。"
                " 这是降级行为, 请检查钥匙串服务 (Linux 需 Secret Service)。"
            )
            return b""

    # ---- 读写 ----
    async def _read_raw(self) -> dict[str, Any]:
        if not await self._path.is_file():
            return {}
        try:
            raw = await self._path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(f"读取凭证文件 {self._path} 失败: {e!r}")
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"凭证文件 {self._path} 已损坏, 视为未登录")
            return {}
        return data if isinstance(data, dict) else {}

    async def _write_raw(self, data: dict[str, Any]) -> None:
        text = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            await self._path.parent.mkdir(parents=True, exist_ok=True)
            await self._path.write_text(text, encoding="utf-8")
            # 只给属主读写。多用户机器上 appdata 默认权限常是 0644 —— 同机其他账号
            # 能直接读走这个文件, 而钥匙串加密挡不住这一点: 钥匙串不可用时内容就是
            # 明文, 可用时也只是异或(见 _secret_key 注释, 它防的是顺手翻看)。
            #
            # Windows 上 chmod 只影响只读位、不做 POSIX 权限, 拿不到效果也不该报错:
            # 该平台的访问控制走 ACL, 由用户目录本身的 ACL 兜底。
            with suppress(OSError, NotImplementedError):
                await self._path.chmod(0o600)
        except Exception as e:
            logger.warning(f"写入凭证文件 {self._path} 失败: {e!r}")

    async def load_token(self) -> str:
        """返回 token; 未登录、解密失败或校验不过均返回空串。

        读到明文凭证时**就地补加密**(若钥匙串此时可用) —— 用户按提示装上 keyring
        再重启, 必须真的把已有凭证加密, 而不只是让告警消失。
        """
        data = await self._read_raw()
        blob = data.get("token", "")
        if not blob:
            return ""
        if not data.get("enc", False):
            plain = str(blob)
            # 明文分支过去直接 return, 既不迁移也不修正 `_encrypted` (默认 True)
            # —— 于是装上 keyring 重启后界面上报"已加密", 盘上仍是明文, 比如实
            # 报警更坏。现在: 能加密就就地迁移, 不能就如实记明文。
            self._encrypted = False
            if self._secret_key():
                await self.save_token(plain)
                logger.info("检测到明文凭证, 已用系统钥匙串重新加密落盘")
            return plain
        self._encrypted = True
        try:
            plain = _xor(base64.b64decode(blob), self._secret_key()).decode("utf-8")
        except Exception as e:
            logger.warning(f"凭证解密失败 ({e!r}); 视为未登录, 需重新登录")
            return ""
        # 校验和是必需的: 异或本身没有完整性保证, 换了密钥 (钥匙串被重置/换机器)
        # 只会解出乱码而不会报错。没有这一步, 客户端会拿着垃圾 token 去请求,
        # 得到 401 才发现, 而且期间界面一直显示「已登录」。
        want = data.get("sum", "")
        if want and _checksum(plain) != want:
            logger.warning("凭证校验和不匹配 (钥匙串密钥可能已变更); 视为未登录")
            return ""
        return plain

    async def save_token(self, token: str) -> None:
        """写入 token, 保留已有的 ``device_key``。"""
        data = await self._read_raw()
        key = self._secret_key()
        if key:
            data["token"] = base64.b64encode(_xor(token.encode("utf-8"), key)).decode()
            data["enc"] = True
            data["sum"] = _checksum(token)
        else:
            data["token"] = token
            data["enc"] = False
            data.pop("sum", None)
        self._encrypted = bool(key)
        data["v"] = 1
        await self._write_raw(data)

    async def clear_token(self) -> None:
        """登出: 只清 token, **保留** ``device_key`` —— 否则重新登录会被云端当成新设备。"""
        data = await self._read_raw()
        data.pop("token", None)
        data.pop("enc", None)
        # 盘上已无凭证, 退回"未知": 让 ``encrypted`` 改用钥匙串可用性做预测,
        # 而不是留着上一枚凭证的形态误导下一次登录前的自检。
        self._encrypted = None
        await self._write_raw(data)

    async def device_key(self) -> str:
        """取 (或首次生成并落盘) 设备标识。跨重装稳定。"""
        data = await self._read_raw()
        existing = data.get("device_key", "")
        if isinstance(existing, str) and existing:
            return existing
        fresh = _new_device_key()
        data["device_key"] = fresh
        data.setdefault("v", 1)
        await self._write_raw(data)
        logger.info("已生成本机 device_key")
        return fresh

    @property
    def encrypted(self) -> bool:
        """盘上凭证是否真的加密 (供 ``GET /auth/status`` 自曝降级)。

        碰过盘就回真实形态; 还没碰过 (未登录) 就回"钥匙串能不能用" —— 此时没有
        凭证可谈形态, 这个预测值正好回答用户关心的"我现在登录会不会明文落盘"。
        """
        if self._encrypted is not None:
            return self._encrypted
        return bool(self._secret_key())
