"""私有助手: 在**共享浏览器的当前页面**上跑一段 JS, 把结果取回来。

`saving_read` 要的是"页面上的事实"。承载页面的那个窗口由 `_browser_shared` 独占, 由
`_browser_impl` 起的 Playwright MCP 驱动 —— 所以取事实的正路是复用那条链路上的
`browser_evaluate`, 而不是再自己接一个 CDP 客户端: 同一个窗口挂两个驱动源, 谁的状态新、
谁负责导航会立刻变成说不清的问题, 而"说不清"在这个场景里就等于给出错的价。

**本模块不做的事**: 不导航、不等待、不点击、不判断"这一页是什么"。导航是 agent 的活
(它看得见页面, 也要在登录墙 / 验证码前停下), 页面身份判定与事实成形是 `saving_read` 的活。
这里只回答一个问题: 现在这一页, 这段 JS 读出什么。

失败一律抛异常, 不返回空串 —— 调用方据此把"读不到"降级成 unknown。把读失败悄悄变成
"页面上没有", 正是这个场景最贵的一类错。
"""

from __future__ import annotations

from typing import Any

import _browser_impl
import _browser_shared
import _mcp
import anyio
from loguru import logger


class BrowserEvalError(RuntimeError):
    """跑 JS 这件事失败了: MCP 起不来或断了 / 页面上的 JS 抛错。"""


class BrowserGoneError(BrowserEvalError):
    """用户关掉了浏览器窗口。调用方应停下并告知用户, **不要擅自重开**。"""


def _is_closed(exc: BaseException) -> bool:
    """这条异常是不是"用户关了窗口"。

    `_browser_impl.ensure_server()` 会把 `_browser_shared.BrowserUnavailableError` 包成
    `BrowserServerError(str(exc))` 抛出, 只剩消息体可比 —— 所以拿模块常量做等值比较,
    而不是匹配文案片段。
    """
    return str(exc) == _browser_shared.CLOSED_MESSAGE


def _config() -> dict[str, Any]:
    """解析一次 MCP 传输配置。**阻塞**(冷启 npx 最多 ~90s), 只许在线程里调。

    与 `browser.py` 的声明同形: 同一个 endpoint, 同一个 `terminate_on_close=False`
    (每调用一次就连一次、断一次, 但**不能**顺带把页面重置掉)。
    """
    endpoint = _browser_impl.ensure_server()
    return _mcp._resolve({"transport": "http", "url": endpoint, "prefix": "", "terminate_on_close": False})


async def evaluate(function: str) -> str:
    """在**当前页面**执行 JS 表达式 *function*, 返回结果文本。

    失败抛 :class:`BrowserEvalError`; 窗口被用户关掉时抛 :class:`BrowserGoneError`。
    两者都必须由调用方降级成 unknown, 不能当成"页面上没有"。
    """
    if not (function or "").strip():
        raise BrowserEvalError("evaluate() 需要一段非空 JS")

    try:
        # 与 _mcp.py 同一处 anyio 类型怪癖: ty 把 to_thread 的 worker 解析成
        # BrokenWorkerInterpreter, 于是看不到 run_sync。运行期没有问题。
        config = await anyio.to_thread.run_sync(_config)  # ty: ignore
    except BaseException as exc:  # 含 MCP/anyio 的 teardown 组, 见 _mcp._is_fatal
        if _mcp._is_fatal(exc):
            raise
        logger.warning(f"Browser MCP server unavailable: {exc!r}")
        if _is_closed(exc):
            raise BrowserGoneError(_browser_shared.CLOSED_MESSAGE) from exc
        raise BrowserEvalError(_mcp._describe(exc)) from exc

    try:
        async with _mcp._connect(config) as session:
            await session.initialize()
            result = await session.call_tool("browser_evaluate", {"function": function})
    except BaseException as exc:
        if _mcp._is_fatal(exc):
            raise
        logger.warning(f"browser_evaluate failed: {exc!r}")
        raise BrowserEvalError(_mcp._describe(exc)) from exc

    text = _mcp._fmt(result)
    if getattr(result, "isError", False):
        # Playwright 侧把 JS 抛错 / 找不到 target 都标成 isError, 文案在 text 里。
        raise BrowserEvalError(text)
    return text
