"""OAuth 回调自动接收 —— 让授权码自己回来, 免用户手工复制。

授权码流程的痛点不在「点同意」, 而在**同意之后**: 第三方只把 ``code`` 拼在
``redirect_uri`` 上跳一次浏览器, 若没人监听那个地址, 用户就得自己看地址栏、把 code
粘回给 agent。本模块提供两条自动接收通道, 由 :func:`plan_receiver` 按环境自动选:

- ``gateway``: 回调打到 Gateway 的 ``/oauth/callback`` (见 ``psi_agent.gateway._oauth_manager``),
  工具侧用同一个 ``state`` 去 ``/oauth/code`` 取件。**浏览器和 agent 不必同机** ——
  手机上点授权也能自动回流, 是飞书多用户部署唯一可行的一条。需要一个用户浏览器可达
  的回调基址 (``PSI_OAUTH_CALLBACK_BASE``, 公网域名或内网地址)。
- ``loopback``: 在 ``127.0.0.1`` 上临时起一个一次性 HTTP 监听 (RFC 8252 的标准做法,
  gh / gcloud / aws sso 同款)。只在**浏览器和 agent 同机**时成立, 适合本机开发。

两条都不可用时回落到原来的手工贴码 —— 行为不变, 只是不再是唯一选择。

无论走哪条, ``redirect_uri`` 都必须先登记到应用后台的重定向 URL 列表, 否则第三方
在跳转前就会拒绝。
"""

from __future__ import annotations

import contextlib
import os
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import anyio
import anyio.abc

# 回调基址: 用户浏览器能打开的 Gateway 地址 (如 https://haitun.example.com)。
_CALLBACK_BASE_ENV = "PSI_OAUTH_CALLBACK_BASE"
# 本机回环监听端口。必须固定 —— redirect_uri 要能提前登记到应用后台。
_LOOPBACK_PORT_ENV = "PSI_OAUTH_LOOPBACK_PORT"
_DEFAULT_LOOPBACK_PORT = 17860
_CALLBACK_PATH = "/oauth/callback"

_DONE_HTML = (
    "<!doctype html><meta charset=utf-8><title>{title}</title>"
    "<body style='font:16px/1.7 system-ui;padding:3rem;text-align:center'>"
    "<h2>{title}</h2><p style='color:#666'>{note}</p></body>"
)
_OK_TITLE = "授权成功 ✅"
_OK_NOTE = "可以关掉这个页面, 回到对话继续 -- 不用复制任何东西."
_FAIL_TITLE = "授权未完成"
_FAIL_NOTE = "可以回到对话里重新发起授权."


def callback_base() -> str:
    """Gateway 回调基址 (无尾斜杠); 未配置返回空串。"""
    return os.environ.get(_CALLBACK_BASE_ENV, "").strip().rstrip("/")


def loopback_port() -> int:
    """本机回环监听端口 (环境变量非法时用默认值)。"""
    raw = os.environ.get(_LOOPBACK_PORT_ENV, "").strip()
    if raw.isdigit() and 1 <= int(raw) <= 65535:
        return int(raw)
    return _DEFAULT_LOOPBACK_PORT


def gateway_redirect_uri() -> str:
    """Gateway 通道的 ``redirect_uri``; 未配置基址返回空串。"""
    base = callback_base()
    return f"{base}{_CALLBACK_PATH}" if base else ""


def loopback_redirect_uri() -> str:
    """回环通道的 ``redirect_uri`` (固定端口, 便于提前登记)。"""
    return f"http://127.0.0.1:{loopback_port()}{_CALLBACK_PATH}"


def _port_is_free(port: int) -> bool:
    """端口能否绑定。占用即视为回环通道不可用 (别抢别人的端口)。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, contextlib.suppress(OSError):
        s.bind(("127.0.0.1", port))
        return True
    return False


@dataclass
class ReceiverPlan:
    """本次授权用哪条自动接收通道, 以及配套的 ``redirect_uri``。"""

    mode: str  # "gateway" | "loopback" | "manual"
    redirect_uri: str

    @property
    def automatic(self) -> bool:
        return self.mode != "manual"


def plan_receiver(explicit_redirect: str = "") -> ReceiverPlan:
    """按环境选自动接收通道: gateway → loopback → manual。

    ``explicit_redirect`` (来自 ``PSI_FEISHU_REDIRECT_URI``) 一旦设置就尊重它 ——
    那是用户在应用后台登记过的地址; 若它正好是本机回环且端口空闲, 仍可自动接收,
    否则只能手工贴码。
    """
    if explicit_redirect:
        host = (urlsplit(explicit_redirect).hostname or "").lower()
        port = urlsplit(explicit_redirect).port
        if host in ("127.0.0.1", "localhost") and port and _port_is_free(port):
            return ReceiverPlan(mode="loopback", redirect_uri=explicit_redirect)
        return ReceiverPlan(mode="manual", redirect_uri=explicit_redirect)
    gw = gateway_redirect_uri()
    if gw:
        return ReceiverPlan(mode="gateway", redirect_uri=gw)
    if _port_is_free(loopback_port()):
        return ReceiverPlan(mode="loopback", redirect_uri=loopback_redirect_uri())
    return ReceiverPlan(mode="manual", redirect_uri="http://localhost/")


def _parse_request_target(request_line: str) -> dict[str, str]:
    """从 HTTP 请求行里取出 query 参数 (只关心 code / state / error)。"""
    parts = request_line.split(" ")
    if len(parts) < 2:
        return {}
    qs = parse_qs(urlsplit(parts[1]).query)
    return {k: v[0] for k, v in qs.items() if v}


async def _serve_one_callback(port: int, expected_state: str, result: dict[str, str]) -> None:
    """接一次回调就收工: 校验 ``state``, 记下 code/error, 回一张成功页。

    不匹配的 ``state`` 一律回 400 且**不写** ``result`` —— 别的进程或恶意页面打过来
    的回调不能顶替真正的授权结果, 监听继续等真回调。
    """
    done = anyio.Event()

    async def _handle(stream: anyio.abc.SocketStream) -> None:
        async with stream:
            raw = b""
            with contextlib.suppress(Exception):
                while b"\r\n\r\n" not in raw and len(raw) < 8192:
                    chunk = await stream.receive(4096)
                    if not chunk:
                        break
                    raw += chunk
            query = _parse_request_target(raw.split(b"\r\n", 1)[0].decode("latin-1"))
            state = query.get("state", "")
            if not state or state != expected_state:
                body = _DONE_HTML.format(title=_FAIL_TITLE, note="state 不匹配, 请重新发起授权.")
                status = "400 Bad Request"
            else:
                code = query.get("code", "")
                error = query.get("error", "") or query.get("error_description", "")
                if code:
                    result["code"] = code
                    body, status = _DONE_HTML.format(title=_OK_TITLE, note=_OK_NOTE), "200 OK"
                else:
                    result["error"] = error or "callback carried neither code nor error"
                    body = _DONE_HTML.format(title=_FAIL_TITLE, note=_FAIL_NOTE)
                    status = "400 Bad Request"
            payload = body.encode("utf-8")
            head = (
                f"HTTP/1.1 {status}\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(payload)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("latin-1")
            with contextlib.suppress(Exception):
                await stream.send(head + payload)
            # 回完页面再收工, 否则浏览器可能拿不到成功页。
            if result:
                done.set()

    listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=port)
    async with listener, anyio.create_task_group() as tg:
        tg.start_soon(listener.serve, _handle, tg)
        await done.wait()
        tg.cancel_scope.cancel()


async def wait_loopback(port: int, expected_state: str, timeout_seconds: float) -> dict[str, str]:
    """起一次性回环监听等回调; 超时返回空 dict。"""
    result: dict[str, str] = {}
    with anyio.move_on_after(timeout_seconds), contextlib.suppress(OSError):
        await _serve_one_callback(port, expected_state, result)
    return result


async def poll_gateway(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
    """轮询 Gateway 的 ``/oauth/code`` 直到取到 code/error 或超时。"""
    base = callback_base()
    if not base:
        return {}
    import httpx  # noqa: PLC0415

    result: dict[str, str] = {}
    with anyio.move_on_after(timeout_seconds):
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                with contextlib.suppress(Exception):
                    resp = await client.get(f"{base}/oauth/code", params={"state": state})
                    if resp.status_code == 200:
                        data: Any = resp.json()
                        if isinstance(data, dict) and (data.get("code") or data.get("error")):
                            result = {k: str(v) for k, v in data.items() if k in ("code", "error")}
                            break
                await anyio.sleep(interval)
    return result
