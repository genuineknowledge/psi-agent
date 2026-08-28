"""``psi_agent._tls`` 的测试。

盯的是两条真实故障各自的不变量, 不是覆盖率:
  1. 收窄密钥交换曲线 (2026-08-15 握手包被分片)
  2. 补 CA 而**不替换** (2026-08-27 macOS 冻结包 CERTIFICATE_VERIFY_FAILED)

第 2 条是重点: 「加载到证书了」这个断言两种写法都过, 只有「原有的还在」能把
``cafile=`` 那种替换式写法挡回去。
"""

from __future__ import annotations

import ssl
from pathlib import Path

import certifi
import pytest

from psi_agent._tls import _CURVE, _load_ca_bundle, client_ssl_context


def test_context_keeps_verification_on() -> None:
    """校验与主机名核对必须原样保留 —— 那两条故障都不是「校验太严」。"""
    ctx = client_ssl_context()
    assert ctx.verify_mode is ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_context_has_certificates() -> None:
    """上下文里得真有根证书, 否则任何 HTTPS 都验不过。"""
    assert len(client_ssl_context().get_ca_certs()) > 0


def test_ca_bundle_is_union_not_replacement() -> None:
    """补 certifi 不能丢掉系统原有的根。

    替换式写法 (``create_default_context(cafile=...)``) 会让系统那份消失, 连带
    企业 MDM 下发的根和 TLS 审查设备的根一起消失 —— 那类机器会从「能上网」变成
    「什么都连不上」。这里用「系统集合是结果的子集」把它钉死。

    ``get_ca_certs()`` 回的 dict 不可哈希, 拿 (序列号, 颁发者) 当身份。
    """
    system_only = ssl.create_default_context()

    def ids(ctx: ssl.SSLContext) -> set[tuple[object, object]]:
        return {(c.get("serialNumber"), str(c.get("issuer"))) for c in ctx.get_ca_certs()}

    merged = ids(client_ssl_context())
    missing = ids(system_only) - merged
    assert not missing, f"并集里丢了 {len(missing)} 张系统根证书, 说明用了替换式加载"


def test_ca_bundle_actually_adds_certifi_roots() -> None:
    """并集也得真的加进去了, 否则 macOS 上照旧验不过。

    空上下文起手才量得准: 从系统默认起手时 certifi 的根大多已经在里面, 增量可能
    是 0, 断言就成了空操作。
    """
    bare = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert len(bare.get_ca_certs()) == 0
    _load_ca_bundle(bare)
    assert len(bare.get_ca_certs()) > 0


def test_curve_is_a_single_classic_curve() -> None:
    """``set_ecdh_curve`` 只认单条已知曲线名, 写成列表或 x25519 都会被拒。"""
    ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).set_ecdh_curve(_CURVE)


def test_missing_bundle_file_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """certifi 装着但数据文件没打进冻结包时, 退回系统信任库而不是崩。

    Windows 上有系统证书库兜底, 这条路仍然能连通; 在这里炸掉会让本来能用的平台
    也起不来。
    """
    monkeypatch.setattr(certifi, "where", lambda: str(Path("no", "such", "cacert.pem")))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    _load_ca_bundle(ctx)  # 不抛即通过


def test_raising_where_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """``certifi.where()`` 自己抛也不能崩 —— 这是冻结包漏收集时的真实表现。

    它走 ``importlib.resources``, 资源不存在时抛异常而不是返回一个坏路径, 所以
    「路径不存在」那条守卫拦不住这种情况, 得单独有一条。
    """

    def boom() -> str:
        raise FileNotFoundError("cacert.pem")

    monkeypatch.setattr(certifi, "where", boom)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    _load_ca_bundle(ctx)  # 不抛即通过
    assert len(ctx.get_ca_certs()) == 0, "取不到证书时不该凭空多出根证书"
