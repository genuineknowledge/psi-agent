"""``fetch_file_bytes`` —— 跨容器取出向文件字节。

放在 channel 通用层而非 feishu 下: 被测函数与任何平台无关, telegram 将来同样部署时
复用同一份实现, 测试也该跟着实现走。
"""

from __future__ import annotations

import anyio
import pytest
from aiohttp import web

from psi_agent.channel._file_bytes import MAX_FILE_BYTES, fetch_file_bytes
from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.file_serving import MAX_FILE_BYTES as SESSION_MAX_FILE_BYTES
from psi_agent.session.server import _make_files_handler
from psi_agent.session.tool_registry import ToolRegistry


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _serve(workspace) -> tuple[web.AppRunner, str]:
    """真起一个只挂 ``GET /files`` 的 session server, 返回 runner 与 base URL。"""
    agent = SessionAgent(
        ai_client=AiClient("http://nonexistent/v1"),
        tool_registry=ToolRegistry(),
        workspace_path=workspace,
    )
    app = web.Application()
    app.router.add_get("/files", _make_files_handler(agent))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{runner.addresses[0][1]}"


@pytest.mark.anyio
async def test_reads_bytes_from_session_files_endpoint(tmp_path):
    """端到端一条: 真起 session server, 从它取字节, 逐字节比对。

    只 mock 到「另一个容器」这一层为止 —— HTTP、路径判定、字节完整性都真跑。
    """
    payload = "标题\n正文\n".encode()
    target = tmp_path / "交付物.md"
    await anyio.Path(target).write_bytes(payload)

    runner, base = await _serve(tmp_path)
    try:
        assert await fetch_file_bytes(base, str(target)) == payload
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_out_of_root_path_returns_none(tmp_path):
    """根外路径取不到 (端点 403) → None, 调用方退回老路而不是发一个空附件。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.md"
    await anyio.Path(outside).write_bytes(b"nope")

    runner, base = await _serve(workspace)
    try:
        assert await fetch_file_bytes(base, str(outside)) is None
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_returns_none_when_unreachable():
    """对端容器连不上时返回 None 而不是抛 —— 发送流程要能继续走到 fallback。"""
    # 127.0.0.1:1 上不会有服务在听。
    assert await fetch_file_bytes("http://127.0.0.1:1", "/workspace/x.md") is None


@pytest.mark.anyio
async def test_trailing_slash_in_source_is_tolerated(tmp_path):
    """``source`` 带尾斜杠也要能取到 —— 拼 URL 时不能拼出 ``//files``。"""
    target = tmp_path / "x.md"
    await anyio.Path(target).write_bytes(b"OK")

    runner, base = await _serve(tmp_path)
    try:
        assert await fetch_file_bytes(base + "/", str(target)) == b"OK"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_empty_body_treated_as_failure(tmp_path):
    """0 字节文件当失败: 平台必拒空附件, 让调用方走 fallback 比发个空附件诚实。"""
    target = tmp_path / "empty.md"
    await anyio.Path(target).write_bytes(b"")

    runner, base = await _serve(tmp_path)
    try:
        assert await fetch_file_bytes(base, str(target)) is None
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_oversize_response_returns_none(tmp_path, monkeypatch):
    """客户端侧也要挡体积 —— 服务端上限是独立的另一道防线, 不能只靠它。"""
    target = tmp_path / "big.md"
    await anyio.Path(target).write_bytes(b"x" * 64)

    runner, base = await _serve(tmp_path)
    try:
        monkeypatch.setattr("psi_agent.channel._file_bytes.MAX_FILE_BYTES", 8)
        assert await fetch_file_bytes(base, str(target)) is None
    finally:
        await runner.cleanup()


def test_max_bytes_is_30mb():
    """写死 30MB; 改动要显式。"""
    assert MAX_FILE_BYTES == 30 * 1024 * 1024


def test_max_bytes_agrees_with_session_side() -> None:
    """两侧上限必须相等 —— 这条是唯一锁住它们的东西。

    上限刻意写两份 (channel 不该 import session 包, 且两侧是各自独立的一道防线:
    服务端拒绝供字节 / 客户端拒绝接收), 代价就是能改一个忘一个。各自那条
    ``== 30MB`` 的断言锁不住这个: 改动方只会改自己那侧的字面量与断言, 两条依旧全绿,
    而**不一致的后果是静默的** —— 谁小谁生效, 大的那侧白设。故在此显式比对。

    真要改上限: 两处常量与本条断言一起改, 本测试红是提醒而非阻碍。
    """
    assert MAX_FILE_BYTES == SESSION_MAX_FILE_BYTES
