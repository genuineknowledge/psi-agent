"""守 ``feishu-web/vite.config.ts`` 的 proxy key 不被改回前缀写法。

为什么值得一条用例: vite 的字符串 proxy key 是**前缀**匹配, 而本应用的 ``base`` 是
``/feishu-web/`` —— 也以 ``/feishu`` 开头。写成 ``'/feishu'`` 时整个前端路径连同
``/@vite/client`` 一起被代理到 gateway, ``npm run dev`` 于是一行自己的东西都不服务:

* 打开 5173 拿到的是 gateway 里**上一次 build 的 ``dist/``** —— 页面能开、功能能用, 只是改
  前端永远看不到变化。同事「每改一行都得上云」就是这个。
* ``/feishu-web/`` 带斜杠时 aiohttp 的 ``show_index=False`` 直接回 **403**。

两个表现都不像「代理配错了」, 而 dev server 一切正常的日志更会把人带偏, 所以靠读代码发现不了,
只能靠判据钉住。这条用例是纯文本检查(不起 vite, 那要 node + 一次真实 HTTP 往返), 判据取
「``/feishu`` 这一项必须排除 ``-web``」, 不写死具体正则 —— 换个等价写法不该让用例红。
"""

from __future__ import annotations

import re
from pathlib import Path

VITE_CONFIG = (
    Path(__file__).resolve().parents[3] / "src" / "psi_agent" / "gateway" / "feishu" / "feishu-web" / "vite.config.ts"
)

# 只取 proxy 表里的 key(行首若干空白 + 引号包住的 key + 冒号), 注释里出现的字面量不算。
_KEY_RE = re.compile(r"^\s*'([^']+)'\s*:", re.MULTILINE)


def _proxy_keys() -> list[str]:
    text = VITE_CONFIG.read_text(encoding="utf-8")
    proxy_at = text.index("proxy:")
    return _KEY_RE.findall(text[proxy_at:])


def test_vite_config_exists() -> None:
    """路径写错时下面两条会「没找到 key 所以通过」, 先把存在性钉住。"""
    assert VITE_CONFIG.is_file(), f"找不到 {VITE_CONFIG}"
    assert _proxy_keys(), "proxy 表里一个 key 都没解析出来, 说明本用例的判据失效了"


def test_feishu_proxy_key_does_not_swallow_feishu_web() -> None:
    """``/feishu`` 那一项必须把 ``/feishu-web`` 排除掉。"""
    feishu_keys = [k for k in _proxy_keys() if "feishu" in k]
    assert feishu_keys, "proxy 表里没有 feishu 相关的 key"

    # 裸前缀 ``/feishu`` (或 ``^/feishu`` 后面直接结束/加 ``/``) 会吞掉 ``/feishu-web``。
    swallowing = [k for k in feishu_keys if "-web" not in k and "feishu-web" not in k]
    assert swallowing == [], (
        f"proxy key {swallowing} 是前缀匹配, 会把前端自己的 /feishu-web/ 一起代理到 gateway: "
        "dev server 不再服务源码, 热更新失效且 /feishu-web/ 回 403。"
        "用 '^/feishu(?!-web)' 这类排除掉 -web 的写法。"
    )


def test_feishu_web_base_is_not_proxied() -> None:
    """反向判据: 拿 ``base`` 的真实字面量去撞每个 key, 确认它不会被代理走。

    比上一条更贴近真实危害 —— 上一条查的是 key 长什么样, 这条查的是 ``/feishu-web/`` 这个
    具体路径会不会命中。``base`` 从配置里读, 改 base 忘了改 proxy 时这条会红。
    """
    text = VITE_CONFIG.read_text(encoding="utf-8")
    base_match = re.search(r"base:\s*'([^']+)'", text)
    assert base_match, "vite.config.ts 里找不到 base, 本用例的判据失效了"
    base = base_match.group(1)
    probe = f"{base.rstrip('/')}/index.html"

    for key in _proxy_keys():
        # vite 的 proxy key 有两种写法: '^' 开头是正则, 其余是前缀匹配。
        hit = re.match(key, probe) is not None if key.startswith("^") else probe.startswith(key)
        assert not hit, (
            f"前端自己的路径 {probe!r} 命中了 proxy key {key!r} —— "
            "它会被代理到 gateway, dev server 于是不服务源码(热更新失效)。"
        )
