"""守 ``feishu-web/vite.config.ts`` 的 ``server.strictPort`` 不被改回 ``false``。

为什么值得一条用例: ``strictPort: false`` 时端口被占**不报错**, vite 静默换到下一个空闲端口
(5173 → 5174 → 5175 ...)。文档、`AGENTS.md`、浏览器书签里写的都是 ``5173``, 于是:

* 5173 上还活着的那个**别的** dev server(常见来源: 另一个 worktree 里忘了关的 ``npm run dev``,
  Windows 上关终端并不一定收走 node 进程)继续应答。页面能开、功能能用, 改本 worktree 的
  前端**永远看不到变化** —— 因为你看的是另一棵树的源码。
* 唯一的线索只在 vite 自己的日志里(``Port 5173 is in use, trying another one...`` 和末行的
  ``Local: http://127.0.0.1:5175/feishu-web/``), 而那行常被 npm 的输出刷掉或没人细看。

表现与「proxy key 吞掉 base」那个坑(见 ``test_feishu_web_dev_proxy.py``)几乎一模一样 ——
都是「dev server 看着正常, 但改前端不生效」—— 但成因完全不同, 所以两条判据都要留:
那条守的是**服务什么内容**, 这条守的是**服务在哪个端口**。

实测证据(2026-08-31): 5173/5174 被另一个 worktree(``02192``)的 vite 占着, 本 worktree 的
``npm run dev`` 落到 5175。此时 ``curl http://127.0.0.1:5173/feishu-web/src/components/tasks-view.tsx``
返回的编译产物里 ``_jsxFileName`` 指向 ``worktrees/02192/...``, 而 5175 上指向 ``worktrees/1da92/...``。
``strictPort: true`` 让这种情况**启动即失败**(``Port 5173 is already in use``), 而不是静默错位。
"""

from __future__ import annotations

import re
from pathlib import Path

FEISHU_WEB = Path(__file__).resolve().parents[3] / "src" / "psi_agent" / "gateway" / "feishu" / "feishu-web"
VITE_CONFIG = FEISHU_WEB / "vite.config.ts"


def _server_block() -> str:
    """取 ``server: { ... }`` 那一段的文本。

    判据只在这一段里找 ``strictPort``/``port``, 免得把 ``build``/``preview`` 里同名的项
    (将来若有)算进来。
    """
    text = VITE_CONFIG.read_text(encoding="utf-8")
    start = text.index("server:")
    # 到 ``proxy:`` 为止就够了: ``port``/``strictPort``/``host`` 都在 proxy 之前。
    return text[start : text.index("proxy:", start)]


def test_server_block_is_parseable() -> None:
    """判据自身的存在性: 找不到 server 段时下面两条会「因为没找到所以通过」。"""
    assert VITE_CONFIG.is_file(), f"找不到 {VITE_CONFIG}"
    block = _server_block()
    assert "port" in block, "server 段里没有 port, 本用例的判据失效了"


def test_dev_server_uses_strict_port() -> None:
    """``strictPort`` 必须显式为 ``true``。

    不写 ``strictPort`` 也不行 —— vite 的默认值就是 ``false``, 同样会静默换端口。
    """
    block = _server_block()
    match = re.search(r"strictPort:\s*(true|false)", block)
    assert match is not None, (
        "server 段里没有显式的 strictPort。vite 默认 strictPort: false, 端口被占时会静默换到"
        "下一个空闲端口, 而文档里写的 URL 还是 5173 —— 打开它拿到的是别人(另一个 worktree 的"
        "残留 dev server)的源码, 改前端永远不生效。请显式写 strictPort: true。"
    )
    assert match.group(1) == "true", (
        "strictPort 是 false: 端口被占时 vite 不报错, 静默换端口(5173 → 5174 → ...)。"
        "文档与书签里的 5173 于是落到别的 dev server 上 —— 页面能开、功能能用, 但改本"
        "worktree 的前端看不到任何变化, 唯一线索只在 vite 日志里。改成 true 让它启动即失败。"
    )


def test_dev_port_is_documented_consistently() -> None:
    """配置里的 ``port`` 与 ``AGENTS.md`` 里写的 URL 必须是同一个端口。

    反向判据: 改了 ``port`` 却忘了改文档时, 同事照文档打开的仍是旧端口 —— 那个端口上可能
    正好有别的 dev server, 于是又变成「改前端不生效」。
    """
    port_match = re.search(r"port:\s*(\d+)", _server_block())
    assert port_match, "server 段里找不到 port, 本用例的判据失效了"
    port = port_match.group(1)

    doc = (FEISHU_WEB / "AGENTS.md").read_text(encoding="utf-8")
    assert f"127.0.0.1:{port}/feishu-web/" in doc, (
        f"vite.config.ts 里 port={port}, 但 AGENTS.md 里找不到 "
        f"http://127.0.0.1:{port}/feishu-web/ —— 文档与配置不一致, "
        "同事照文档打开的会是别的端口。"
    )
