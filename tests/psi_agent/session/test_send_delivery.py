"""Unit tests for Session auto-fill of missing ``[SEND:]`` markers."""

from __future__ import annotations

import json
import socket as _s

import pytest
from aiohttp import web

from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.send_delivery import (
    FILE_CREATE_TOOLS,
    created_file_paths_from_turn,
    is_blocked_auto_send_path,
    missing_send_paths,
    path_from_ok_tool_result,
    send_marker_suffix,
)
from psi_agent.session.tool_registry import FileEntry, ToolFunction, ToolRegistry


def test_path_from_ok_matches_write_shapes() -> None:
    assert path_from_ok_tool_result("[OK] Written 12 bytes to C:\\ws\\a.md") == r"C:\ws\a.md"
    assert path_from_ok_tool_result("[OK] Wrote 3 row(s) to reports/out.xlsx") == "reports/out.xlsx"
    assert path_from_ok_tool_result("[OK] Wrote 8 block(s) to /tmp/doc.docx") == "/tmp/doc.docx"


def test_path_from_ok_rejects_errors_and_noise() -> None:
    assert path_from_ok_tool_result("[Error] disk full") is None
    assert path_from_ok_tool_result("Wrote something to nowhere") is None
    assert path_from_ok_tool_result("") is None


def test_blocked_paths_skip_capability_package() -> None:
    assert is_blocked_auto_send_path("skills/foo/SKILL.md") is True
    assert is_blocked_auto_send_path("/ws/tools/write.py") is True
    assert is_blocked_auto_send_path(r"C:\proj\histories\x.jsonl") is True
    assert is_blocked_auto_send_path("deliverables/report.md") is False
    assert is_blocked_auto_send_path(r"C:\Users\me\Desktop\haitun交付\方案.docx") is False


def test_created_paths_only_from_named_create_tools() -> None:
    messages = [
        {"role": "assistant", "content": "writing"},
        {
            "role": "tool",
            "name": "write",
            "content": "[OK] Written 4 bytes to out/a.md",
            "tool_call_id": "1",
        },
        {
            "role": "tool",
            "name": "edit",
            "content": "[OK] Edited out/a.md",
            "tool_call_id": "2",
        },
        {
            "role": "tool",
            "name": "bash",
            "content": "[OK] Written 1 bytes to out/secret.md",
            "tool_call_id": "3",
        },
        {
            "role": "tool",
            "name": "write_excel",
            "content": "[OK] Wrote 2 row(s) to out/b.xlsx",
            "tool_call_id": "4",
        },
    ]
    assert created_file_paths_from_turn(messages) == ["out/a.md", "out/b.xlsx"]
    assert "edit" not in FILE_CREATE_TOOLS
    assert "bash" not in FILE_CREATE_TOOLS


def test_missing_send_skips_already_marked() -> None:
    messages = [
        {
            "role": "tool",
            "name": "write",
            "content": "[OK] Written 1 bytes to /ws/a.md",
            "tool_call_id": "1",
        },
        {
            "role": "tool",
            "name": "write",
            "content": "[OK] Written 1 bytes to /ws/b.md",
            "tool_call_id": "2",
        },
    ]
    reply = "done\n[SEND:/ws/a.md]\n"
    assert missing_send_paths(messages, reply) == ["/ws/b.md"]


def test_send_marker_suffix_format() -> None:
    assert send_marker_suffix([]) == ""
    assert send_marker_suffix(["/ws/a.md", "/ws/b.docx"]) == "\n[SEND:/ws/a.md]\n[SEND:/ws/b.docx]"


def test_dedupe_same_path_case_insensitive() -> None:
    messages = [
        {
            "role": "tool",
            "name": "write",
            "content": "[OK] Written 1 bytes to C:\\WS\\A.md",
            "tool_call_id": "1",
        },
        {
            "role": "tool",
            "name": "write_word",
            "content": "[OK] Wrote 1 block(s) to c:\\ws\\a.md",
            "tool_call_id": "2",
        },
    ]
    assert created_file_paths_from_turn(messages) == [r"C:\WS\A.md"]


# --- agent stop path: auto-append is ordinary content, not a new protocol ---


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_agent_stop_auto_appends_missing_send_after_write() -> None:
    """write succeeded + reply forgot [SEND:] → suffix yielded and committed."""
    path = r"C:\ws\deliverables\report.md"
    req_count = 0

    async def write_fn(file_path: str = "", content: str = "") -> str:
        return f"[OK] Written {len(content)} bytes to {path}"

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal req_count
        req_count += 1
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        if req_count == 1:
            chunk = {
                "id": "mock",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "write",
                                        "arguments": json.dumps(
                                            {"file_path": path, "content": "hi"}
                                        ),
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        else:
            # Model forgot the marker — the bug this gate fixes.
            chunk = {
                "id": "mock",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": f"已写好: {path}"},
                        "finish_reason": "stop",
                    }
                ],
            }
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    try:
        tf = ToolFunction(
            name="write",
            description="Write a file.",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
            },
        )
        agent = SessionAgent(
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(
                files={
                    "__test__": FileEntry(
                        file_hash="",
                        tools={"write": tf},
                        funcs={"write": write_fn},
                    )
                }
            ),
        )
        chunks = [c async for c in agent.run({"role": "user", "content": "写个报告"})]
        content = "".join(c.content or "" for c in chunks)
        assert f"[SEND:{path}]" in content
        assistants = [m for m in agent._conversation.messages if m.get("role") == "assistant"]
        assert any(f"[SEND:{path}]" in (m.get("content") or "") for m in assistants)
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_agent_stop_does_not_duplicate_existing_send() -> None:
    path = "/ws/a.md"
    req_count = 0

    async def write_fn(file_path: str = "", content: str = "") -> str:
        return f"[OK] Written {len(content)} bytes to {path}"

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal req_count
        req_count += 1
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        if req_count == 1:
            chunk = {
                "id": "mock",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "write",
                                        "arguments": json.dumps(
                                            {"file_path": path, "content": "x"}
                                        ),
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
        else:
            chunk = {
                "id": "mock",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": f"ok\n[SEND:{path}]\n"},
                        "finish_reason": "stop",
                    }
                ],
            }
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    try:
        tf = ToolFunction(
            name="write",
            description="Write a file.",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
            },
        )
        agent = SessionAgent(
            ai_client=AiClient(f"http://127.0.0.1:{port}"),
            tool_registry=ToolRegistry(
                files={
                    "__test__": FileEntry(
                        file_hash="",
                        tools={"write": tf},
                        funcs={"write": write_fn},
                    )
                }
            ),
        )
        chunks = [c async for c in agent.run({"role": "user", "content": "写"})]
        content = "".join(c.content or "" for c in chunks)
        assert content.count(f"[SEND:{path}]") == 1
    finally:
        await runner.cleanup()
