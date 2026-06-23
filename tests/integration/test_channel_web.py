from __future__ import annotations

import contextlib
import json
import sys
from functools import partial
from pathlib import Path
from urllib.parse import quote

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, FormData

from tests.integration.conftest import MockAIServer


def _chunk(content: str = "", reasoning: str = "", finish_reason: str | None = None) -> str:
    d: dict = {}
    if content:
        d["content"] = content
    if reasoning:
        d["reasoning_content"] = reasoning
    return json.dumps(
        {
            "id": "test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test",
            "choices": [{"index": 0, "delta": d, "finish_reason": finish_reason}],
        }
    )


async def _wait_for_socket(sock_path: str, timeout_sec: float = 15.0) -> bool:
    deadline = anyio.current_time() + timeout_sec
    sock_anyio = anyio.Path(sock_path)
    while anyio.current_time() < deadline:
        if await sock_anyio.exists():
            await anyio.sleep(0.3)
            return True
        await anyio.sleep(0.1)
    return False


async def _wait_for_http(base_url: str, timeout_sec: float = 15.0) -> bool:
    deadline = anyio.current_time() + timeout_sec
    timeout = ClientTimeout(total=2)
    while anyio.current_time() < deadline:
        try:
            async with ClientSession(timeout=timeout) as session, session.get(base_url) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        await anyio.sleep(0.2)
    return False


async def _stop_process(proc) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await proc.wait()
    except Exception:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


async def _read_web_chat(
    base_url: str,
    message: str = "hello",
    attachments: list[dict[str, str]] | None = None,
) -> tuple[str, str]:
    """Send a message to the web channel /api/chat and collect content + reasoning."""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    timeout = ClientTimeout(total=15)
    async with (
        ClientSession(timeout=timeout) as session,
        session.post(
            base_url.rstrip("/") + "/api/chat",
            json={"message": message, "attachments": attachments or []},
        ) as resp,
    ):
        assert resp.status == 200, f"Got {resp.status}"
        async for raw in resp.content:
            line = raw.decode().strip()
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            evt = json.loads(data_str)
            if evt.get("content"):
                content_parts.append(evt["content"])
            if evt.get("reasoning"):
                reasoning_parts.append(evt["reasoning"])
    return "".join(content_parts), "".join(reasoning_parts)


@pytest.mark.anyio
async def test_web_channel_streams_session_reply(tmp_path, mock_ai_server: MockAIServer) -> None:
    """Web channel should serve the page and stream session content over /api/chat."""
    mock_ai_server.set_responses([_chunk(reasoning="thinking"), _chunk(content="Hello from web", finish_reason="stop")])
    base_url_ai = await mock_ai_server.start()

    ai_socket = str(tmp_path / "ai.sock")
    channel_socket = str(tmp_path / "channel.sock")
    web_listen = "http://127.0.0.1:8799"

    ai_proc = await anyio.open_process(
        [
            sys.executable,
            "-c",
            "from psi_agent.cli import main; main()",
            "ai",
            "openai-completions",
            "--session-socket",
            ai_socket,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            base_url_ai,
        ],
    )
    ses_proc = await anyio.open_process(
        [
            sys.executable,
            "-c",
            "from psi_agent.cli import main; main()",
            "session",
            "--workspace",
            "examples/a-simple-bash-only-workspace",
            "--channel-socket",
            channel_socket,
            "--ai-socket",
            ai_socket,
            "--model",
            "test",
        ],
    )

    web_proc = None
    try:
        assert await _wait_for_socket(ai_socket)
        assert await _wait_for_socket(channel_socket)

        web_proc = await anyio.open_process(
            [
                sys.executable,
                "-c",
                "from psi_agent.cli import main; main()",
                "channel",
                "web",
                "--session-socket",
                channel_socket,
                "--listen",
                web_listen,
            ],
        )
        assert await _wait_for_http(web_listen)

        content, reasoning = await _read_web_chat(web_listen, "hi")
        assert "Hello from web" in content, f"Got content: {content[:200]}"
        assert "thinking" in reasoning, f"Got reasoning: {reasoning[:200]}"

        async with ClientSession(timeout=ClientTimeout(total=5)) as session, session.get(web_listen) as resp:
            page = await resp.text()
        assert "dolphin-agent" in page
        assert '<div class="app sidebar-collapsed" id="app">' in page
        assert 'placeholder="输入消息给 dolphin-agent..."' in page
        assert ">发送</button>" in page
        assert ">Send</button>" not in page
        assert "Message dolphin-agent..." not in page
        assert 'class="context-sidebar"' not in page
        assert "280px" not in page
        assert "renderMarkdown" in page
        assert "thinking-panel" in page
        assert "tool-panel" in page
        assert "appendReasoning" in page
        assert "createTracePanel" in page
        assert "appendThinkingTrace" in page
        assert "appendToolTrace" in page
        assert "summarizeTrace" in page
        assert "trace-summary" in page
        assert "shouldAutoScroll" in page
        assert "jump-bottom" in page
        assert ">⤓</button>" in page
        assert ">Bottom</button>" not in page
        assert "toggleSidebar" in page
        assert "›" in page
        assert "‹" in page
        assert "sidebar-collapsed" in page
        assert "typing-dots" in page
        assert "createTypingDots" in page
        assert "assistant-loader" in page
        assert "assistant-output" in page
        assert "setTraceWaiting" in page
        assert "tool-waiting" in page
        assert "renderFileCards" in page
        assert "extractMediaAttachments" in page
        assert "/api/download?path=" in page
    finally:
        if web_proc is not None:
            await _stop_process(web_proc)
        await _stop_process(ses_proc)
        await _stop_process(ai_proc)


@pytest.mark.anyio
async def test_web_channel_uploads_file_and_forwards_attachment_path(tmp_path: Path) -> None:
    session_messages: list[str] = []

    async def session_handler(request):
        body = await request.json()
        session_messages.append(body["messages"][0]["content"])
        resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream"},
        )
        await resp.prepare(request)
        await resp.write(f"data: {_chunk(content='read file', finish_reason='stop')}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    from aiohttp import web

    session_app = web.Application()
    session_app.router.add_post("/v1/chat/completions", session_handler)
    runner = web.AppRunner(session_app)
    await runner.setup()
    session_sock = str(tmp_path / "session.sock")
    site = web.UnixSite(runner, session_sock)
    await site.start()

    from psi_agent.channel.web.server import serve_web_channel

    web_listen = "http://127.0.0.1:8801"
    upload_root = tmp_path / "uploads"
    web_task = None
    try:
        async with anyio.create_task_group() as tg:
            web_task = tg
            tg.start_soon(partial(serve_web_channel, session_socket=session_sock, listen=web_listen, upload_dir=str(upload_root)))
            assert await _wait_for_http(web_listen)

            data = FormData()
            data.add_field(
                "file",
                b"hello attachment",
                filename="简历 2026.pdf",
                content_type="text/plain",
            )
            async with ClientSession(timeout=ClientTimeout(total=10)) as http:
                async with http.post(web_listen + "/api/upload", data=data) as resp:
                    assert resp.status == 200
                    uploaded = await resp.json()

            assert uploaded["name"] == "简历 2026.pdf"
            uploaded_path = Path(uploaded["path"])
            assert uploaded_path.exists()
            assert uploaded_path.read_text(encoding="utf-8") == "hello attachment"

            content, _reasoning = await _read_web_chat(
                web_listen,
                "请读取附件",
                attachments=[uploaded],
            )
            assert content == "read file"
            assert len(session_messages) == 1
            assert "请读取附件" in session_messages[0]
            assert "简历 2026.pdf" in session_messages[0]
            assert f"FILE:{uploaded_path}" in session_messages[0]
            tg.cancel_scope.cancel()
    finally:
        if web_task is not None:
            web_task.cancel_scope.cancel()
        await runner.cleanup()


@pytest.mark.anyio
async def test_web_channel_downloads_allowed_file_with_unicode_name(tmp_path: Path) -> None:
    from aiohttp import web

    session_app = web.Application()
    runner = web.AppRunner(session_app)
    await runner.setup()
    session_sock = str(tmp_path / "session.sock")
    site = web.UnixSite(runner, session_sock)
    await site.start()

    from psi_agent.channel.web.server import serve_web_channel

    upload_root = tmp_path / "uploads"
    download_file = upload_root / "报告 2026.txt"
    await anyio.Path(upload_root).mkdir(parents=True, exist_ok=True)
    await anyio.Path(download_file).write_text("download body", encoding="utf-8")

    web_listen = "http://127.0.0.1:8802"
    web_task = None
    try:
        async with anyio.create_task_group() as tg:
            web_task = tg
            tg.start_soon(partial(serve_web_channel, session_socket=session_sock, listen=web_listen, upload_dir=str(upload_root)))
            assert await _wait_for_http(web_listen)

            async with ClientSession(timeout=ClientTimeout(total=10)) as http:
                url = web_listen + "/api/download?path=" + quote(str(download_file), safe="")
                async with http.get(url) as resp:
                    assert resp.status == 200
                    body = await resp.text()
                    disposition = resp.headers.get("Content-Disposition", "")

            assert body == "download body"
            assert "filename*=UTF-8''" in disposition
            assert "%E6%8A%A5%E5%91%8A%202026.txt" in disposition
            tg.cancel_scope.cancel()
    finally:
        if web_task is not None:
            web_task.cancel_scope.cancel()
        await runner.cleanup()
