"""``GET /files`` —— 出向跨容器发文件的字节供给端。

分两层测: ``resolve_within_root`` 的路径判定脱离 HTTP 直接测 (逃逸、越界、超限),
端点本身用真 aiohttp server 起在 TCP 上测 —— 生产里独立容器就是 TCP
(``channel_socket: http://0.0.0.0:8081``), 而 Windows 上 Unix socket 根本不可用。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from aiohttp import ClientSession, ClientTimeout, web

from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.file_serving import MAX_FILE_BYTES, FileServingError, resolve_within_root
from psi_agent.session.server import _make_files_handler
from psi_agent.session.tool_registry import ToolRegistry


def _agent(workspace: Path | None) -> SessionAgent:
    return SessionAgent(
        ai_client=AiClient("http://nonexistent/v1"),
        tool_registry=ToolRegistry(),
        workspace_path=workspace,
    )


# -- 路径判定 (无 HTTP) ---------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_accepts_file_inside_root(tmp_path: Path) -> None:
    target = tmp_path / "deliverable.md"
    target.write_text("hello", encoding="utf-8")
    resolved = await resolve_within_root(str(target), tmp_path)
    assert resolved.read_text(encoding="utf-8") == "hello"


@pytest.mark.anyio
async def test_resolve_rejects_dotdot_escape(tmp_path: Path) -> None:
    """``..`` 逃逸必须在 resolve 之后被拦 —— 少了 resolve 这一步它能过判定。"""
    root = tmp_path / "workspace"
    (root / "pub").mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    escape = str(root / "pub" / ".." / ".." / "secret.txt")
    with pytest.raises(FileServingError) as excinfo:
        await resolve_within_root(escape, root)
    assert excinfo.value.status == 403


@pytest.mark.anyio
async def test_resolve_rejects_symlink_escape(tmp_path: Path) -> None:
    """指向根外的软链同样要拦: 路径字面在根内, 真实位置不在。"""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "innocent.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlink not available: {e}")

    with pytest.raises(FileServingError) as excinfo:
        await resolve_within_root(str(link), root)
    assert excinfo.value.status == 403


@pytest.mark.anyio
async def test_resolve_rejects_when_no_workspace_root(tmp_path: Path) -> None:
    """没有 workspace 根时一律拒 —— 「没有边界」不等于「边界是整个文件系统」。"""
    target = tmp_path / "f.md"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(FileServingError) as excinfo:
        await resolve_within_root(str(target), None)
    assert excinfo.value.status == 403


@pytest.mark.anyio
async def test_resolve_rejects_empty_path(tmp_path: Path) -> None:
    with pytest.raises(FileServingError) as excinfo:
        await resolve_within_root("   ", tmp_path)
    assert excinfo.value.status == 400


@pytest.mark.anyio
async def test_resolve_missing_file_is_404(tmp_path: Path) -> None:
    with pytest.raises(FileServingError) as excinfo:
        await resolve_within_root(str(tmp_path / "nope.md"), tmp_path)
    assert excinfo.value.status == 404


@pytest.mark.anyio
async def test_resolve_directory_is_400(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    with pytest.raises(FileServingError) as excinfo:
        await resolve_within_root(str(sub), tmp_path)
    assert excinfo.value.status == 400


@pytest.mark.anyio
async def test_resolve_oversize_is_413(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """超限在读进内存**之前**就拒, 所以只需要一个比上限大的 st_size。"""
    monkeypatch.setattr("psi_agent.session.file_serving.MAX_FILE_BYTES", 8)
    target = tmp_path / "big.bin"
    target.write_bytes(b"0123456789")
    with pytest.raises(FileServingError) as excinfo:
        await resolve_within_root(str(target), tmp_path)
    assert excinfo.value.status == 413


def test_max_file_bytes_matches_feishu_limit() -> None:
    """飞书单文件上限 30MB; 与 channel 侧的 ``_MAX_OUTBOUND_FILE_BYTES`` 一致。"""
    assert MAX_FILE_BYTES == 30 * 1024 * 1024


# -- 端点 (真 aiohttp server, TCP) ---------------------------------------------


async def _serve(agent: SessionAgent) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_get("/files", _make_files_handler(agent))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return runner, f"http://127.0.0.1:{port}"


@pytest.mark.anyio
async def test_endpoint_serves_bytes_verbatim(tmp_path: Path) -> None:
    """字节要逐字节一致, 且带 filename —— 用户点开的附件名从这里来。"""
    payload = "标题\n正文 with ünïcode\n".encode()
    target = tmp_path / "交付物.md"
    target.write_bytes(payload)

    runner, base = await _serve(_agent(tmp_path))
    try:
        async with (
            ClientSession(timeout=ClientTimeout(total=10)) as http,
            http.get(f"{base}/files", params={"path": str(target)}) as resp,
        ):
            assert resp.status == 200
            assert await resp.read() == payload
            assert "交付物.md" in resp.headers.get("Content-Disposition", "")
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_endpoint_rejects_escape_with_403(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    # 内容用一个不会出现在路径里的串, 才能证明「没泄漏文件内容」——
    # 错误信息**会**回显请求路径 (那是给调用方的诊断), 拿文件名断言只会自证。
    outside.write_text("CLASSIFIED-PAYLOAD", encoding="utf-8")

    runner, base = await _serve(_agent(root))
    try:
        async with (
            ClientSession(timeout=ClientTimeout(total=10)) as http,
            http.get(f"{base}/files", params={"path": str(outside)}) as resp,
        ):
            assert resp.status == 403
            assert "CLASSIFIED-PAYLOAD" not in await resp.text()
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_endpoint_missing_file_is_404(tmp_path: Path) -> None:
    runner, base = await _serve(_agent(tmp_path))
    try:
        async with (
            ClientSession(timeout=ClientTimeout(total=10)) as http,
            http.get(f"{base}/files", params={"path": str(tmp_path / "nope.md")}) as resp,
        ):
            assert resp.status == 404
    finally:
        await runner.cleanup()


# -- 私密区不经本端点外流 --
#
# 判在这一侧的理由: channel 那道守卫判的是模型输出的路径字符串, 跨容器时那串路径在
# gateway 上不存在, realpath 退化成字符串规范化。故「软链指进私密区」只有源容器判得出。


@pytest.mark.anyio
async def test_private_space_file_is_403(tmp_path: Path) -> None:
    """根内但落在 .private/ 下 —— 路径完全合法, 仍不供字节。"""
    private = tmp_path / ".private" / "ou_owner"
    private.mkdir(parents=True)
    target = private / "salary.xlsx"
    target.write_text("PRIVATE-PAYLOAD", encoding="utf-8")

    with pytest.raises(FileServingError) as excinfo:
        await resolve_within_root(str(target), tmp_path)
    assert excinfo.value.status == 403


@pytest.mark.anyio
async def test_private_space_blocked_without_whitelist_configured(tmp_path: Path, monkeypatch) -> None:
    """白名单没配也要拦。

    这是与 ``_private_space.owner_of`` 刻意分道的地方: 那个未配白名单时返回 None = 放行
    (它判的是「谁是主人」)。本端点判的是「是不是私密区」—— 配置缺失不该等于把私密目录
    敞开供字节。
    """
    monkeypatch.delenv("PSI_PRIVATE_OPEN_IDS", raising=False)
    private = tmp_path / ".private" / "ou_whoever"
    private.mkdir(parents=True)
    target = private / "notes.md"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(FileServingError) as excinfo:
        await resolve_within_root(str(target), tmp_path)
    assert excinfo.value.status == 403


@pytest.mark.anyio
async def test_symlink_into_private_space_is_403(tmp_path: Path) -> None:
    """公共区里的软链指进私密区 —— 字符串上看不出, resolve 后拦住。

    这条正是 channel 侧守卫在跨容器时判不出的那种写法。
    """
    private = tmp_path / ".private" / "ou_owner"
    private.mkdir(parents=True)
    secret = private / "secret.md"
    secret.write_text("PRIVATE-PAYLOAD", encoding="utf-8")

    link = tmp_path / "innocent.md"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("Windows 上无 SeCreateSymbolicLinkPrivilege")

    with pytest.raises(FileServingError) as excinfo:
        await resolve_within_root(str(link), tmp_path)
    assert excinfo.value.status == 403


@pytest.mark.anyio
async def test_public_file_named_like_private_is_served(tmp_path: Path) -> None:
    """判的是**目录层级**, 不是名字里带 .private —— 别把正常文件误伤。"""
    target = tmp_path / "how-to-use-.private-dirs.md"
    target.write_text("public doc", encoding="utf-8")

    resolved = await resolve_within_root(str(target), tmp_path)
    assert resolved.name == target.name


@pytest.mark.anyio
async def test_endpoint_private_file_403_without_leaking(tmp_path: Path) -> None:
    """端点层同样拦, 且不回显文件内容。"""
    private = tmp_path / ".private" / "ou_owner"
    private.mkdir(parents=True)
    target = private / "payroll.md"
    target.write_text("CLASSIFIED-PAYLOAD", encoding="utf-8")

    runner, base = await _serve(_agent(tmp_path))
    try:
        async with (
            ClientSession(timeout=ClientTimeout(total=10)) as http,
            http.get(f"{base}/files", params={"path": str(target)}) as resp,
        ):
            assert resp.status == 403
            assert "CLASSIFIED-PAYLOAD" not in await resp.text()
    finally:
        await runner.cleanup()
