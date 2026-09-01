"""aiohttp server that binds ``agent.handle_request`` to the channel socket."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
from aiohttp import web
from aiohttp.typedefs import Handler
from loguru import logger

from psi_agent._sockets import create_site
from psi_agent.session import live_agent
from psi_agent.session.file_serving import FileServingError, resolve_within_root

if TYPE_CHECKING:
    from psi_agent.session.agent import SessionAgent


def _make_files_handler(agent: SessionAgent) -> Handler:
    """``GET /files?path=…`` —— 把 workspace 内的文件当字节流交出去。

    出向跨容器发文件用: channel 在 gateway 容器里, 读不到本容器的文件系统, 于是取字节
    自己上传 (缘由见 ``session.file_serving``)。读取范围严格限在本 Session 的 workspace
    根内, 越界 403。

    **不加鉴权, 与同口的 ``/chat/completions`` 一致 —— 但理由不是「端口安全」。** 生产上
    该端口未映射到宿主, 然而同一 docker 网络里的**其他 Session 容器彼此可达**, 而威胁模型
    里的不可信方 (被 prompt 注入的 agent, 手上有 ``bash`` / ``fetch``) 正在那个网络里面。
    所以「只在 docker 网络内」挡的是外人, 挡不住邻居, 不能当成安全依据。

    真实依据是**加了也不改变暴露面**: 同口的 ``POST /chat/completions`` 无鉴权且能驱动本
    容器 agent 执行任意 tool —— 邻居想要本容器的文件, 让本容器 agent 自己读了交出来即可,
    比本端点更强。单给这一个加密钥只是把绕路变长一步。真正的边界只能在网络层 (compose 里
    把 Session 之间拆开) 或给该端口上的**所有**路由统一加鉴权, 两者都要改部署, 见
    ``session/AGENTS.md``「已知缺口」。

    本端点因此只做力所能及的两件事: 限 workspace 根内, 且**私密区一律不供字节** ——
    后者是本侧独有的能力, channel 那道守卫在跨容器时判不出 (见 ``file_serving``)。
    """

    async def handle_files(request: web.Request) -> web.StreamResponse:
        raw = request.query.get("path") or ""
        try:
            resolved = await resolve_within_root(raw, agent.workspace_path)
        except FileServingError as e:
            logger.warning(f"GET /files rejected ({e.status}): {e}")
            return web.json_response({"error": str(e)}, status=e.status)
        except OSError as e:
            logger.warning(f"GET /files failed for {raw!r}: {e}")
            return web.json_response({"error": str(e)}, status=400)
        logger.info(f"GET /files serving {str(resolved)!r}")
        # FileResponse 走 sendfile, 不把整份读进本进程内存。
        return web.FileResponse(
            resolved,
            headers={"Content-Disposition": f'attachment; filename="{resolved.name}"'},
        )

    return handle_files


async def serve_session(*, channel_socket: str, agent: SessionAgent) -> None:
    """Create an aiohttp server that routes channel traffic to the agent.

    - ``POST /chat/completions`` → ``agent.handle_request`` (chat SSE)
    - ``POST /events`` → ``agent.handle_event`` (normalized event envelopes)
    - ``GET /files`` → workspace-confined bytes (outbound cross-container files)
    """
    logger.info(f"Starting session server on {channel_socket}")

    # Large conversation contexts (long histories, tool outputs) routinely exceed
    # aiohttp's 1 MiB default body limit, which would reject the request with
    # HTTPRequestEntityTooLarge before it reaches the agent. Match the gateway
    # and AI-forwarder apps' 100 MiB ceiling so the same payloads flow through.
    app = web.Application(client_max_size=100 * 1024 * 1024)
    app.router.add_post("/chat/completions", agent.handle_request)
    app.router.add_post("/events", agent.handle_event)
    app.router.add_get("/files", _make_files_handler(agent))

    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = create_site(runner, channel_socket)
        await site.start()
    except Exception as e:
        logger.error(f"Failed to start session server on {channel_socket}: {e}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        raise

    logger.info(f"Session server listening on {channel_socket}")

    try:
        # Reachable for out-of-band resumes only while actually serving: work that
        # outlived its turn (Feishu authorization) needs a turn to finish in, and a
        # registration outliving the server would resume a conversation with no
        # listener. See ``session.live_agent``.
        with live_agent.register(agent.session_id, agent):
            await anyio.sleep_forever()
    finally:
        logger.info(f"Shutting down session server on {channel_socket}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
