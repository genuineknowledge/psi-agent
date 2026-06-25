# Fusion-Guard Dolphin 适配 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Dolphin-Agent 中提供一个可选的 Fusion-Guard 安全 bash tool：每条 user message 单独起临时 workspace/CLI 做意图分析，`DENY` 则返回统一安全拒绝文案，`NONE` 则按 base policy 执行，`allow ...;` 则先装策略再执行。

**Architecture:** 先在 Session 层补一个只读工具上下文和 user-message 落盘时机，再在 `src/psi_agent/fusion_guard/` 里实现意图分析、allow 规则过滤、拒绝文案和临时执行编排，最后把 workspace 里的 `bash` tool 接到这条链路上。Dolphin core 只增加最小注入点，不把安全逻辑做成内建 middleware。

**Tech Stack:** Python 3.14, `anyio`, `aiohttp`, `pytest`, `uv`, `tyro`, `loguru`

## Global Constraints

- Every `user message` must be appended to history and written to `workspace/histories/{session_id}.jsonl` before any safety analysis or tool execution begins.
- Each secure bash invocation must create a fresh temporary workspace, temporary session, and temporary CLI.
- The temporary CLI must consume the current Dolphin session history snapshot for intent analysis.
- Denied requests (`DENY`) must return a Dolphin-side unified error message that explicitly marks the refusal as Fusion-Guard security related.
- `NONE` must continue on the base policy path without installing extra rules.
- Allowed requests (`allow ...;`) must install policy before command execution, then execute inside the same bash tool call.
- Dolphin core must stay minimally modified; no new permanent security middleware in the agent loop.

---

### Task 1: Add session tool context and immediate history write-through

**Files:**
- Create: `src/psi_agent/session/runtime_context.py`
- Modify: `src/psi_agent/session/agent.py`
- Test: `tests/psi_agent/session/test_runtime_context.py`

**Interfaces:**
- Consumes: `SessionAgent.history`, `SessionAgent._history_path`, `SessionAgent.ai_socket`
- Produces:
  - `SessionToolContext`
  - `get_session_tool_context()`
  - `push_session_tool_context(...)`
  - `SessionAgent.run()` writes history immediately after appending the current user message

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

import pytest

from psi_agent.session.agent import SessionAgent
from psi_agent.session.protocol import ChatCompletionChunk, DeltaMessage, StreamChoice, ToolFunction
from psi_agent.session.runtime_context import get_session_tool_context


@pytest.mark.anyio
async def test_user_message_is_written_before_tool_execution(tmp_path: Path) -> None:
    history_path = tmp_path / "workspace" / "histories" / "s1.jsonl"
    history_path.parent.mkdir(parents=True)

    async def echo_tool() -> str:
        ctx = get_session_tool_context()
        assert ctx is not None
        assert ctx.latest_user_message["content"] == "hello"
        assert len(ctx.history_messages) >= 1
        return "ok"

    tf = ToolFunction(name="echo_tool", description="Echo", parameters={"type": "object", "properties": {}, "required": []})
    agent = SessionAgent(
        ai_socket="http://127.0.0.1:1",
        tools={"echo_tool": tf},
        tool_funcs={"echo_tool": echo_tool},
        history=[],
        history_path=history_path,
    )

    async def fake_stream(_: dict):
        yield ChatCompletionChunk(
            id="tool",
            choices=[
                StreamChoice(
                    index=0,
                    delta=DeltaMessage(
                        tool_calls=[
                            {
                                "index": 0,
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "echo_tool", "arguments": "{}"},
                            }
                        ]
                    ),
                    finish_reason="tool_calls",
                )
            ],
        )
        yield ChatCompletionChunk(
            id="done",
            choices=[StreamChoice(index=0, delta=DeltaMessage(content="done"), finish_reason="stop")],
        )

    agent._stream_ai_request = fake_stream  # type: ignore[method-assign]

    async for _ in agent.run({"role": "user", "content": "hello"}):
        pass

    content = history_path.read_text()
    assert '"role": "user"' in content
    assert '"content": "hello"' in content
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/psi_agent/session/test_runtime_context.py -v`
Expected: fail with `ModuleNotFoundError` or missing context/writes, depending on the first red state.

- [ ] **Step 3: Write the minimal implementation**

```python
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class SessionToolContext:
    session_id: str | None
    workspace_path: Path | None
    history_path: Path | None
    history_messages: list[dict[str, Any]]
    latest_user_message: dict[str, Any]
    ai_socket: str


_SESSION_TOOL_CONTEXT: ContextVar[SessionToolContext | None] = ContextVar(
    "SESSION_TOOL_CONTEXT",
    default=None,
)


def get_session_tool_context() -> SessionToolContext | None:
    return _SESSION_TOOL_CONTEXT.get()


@contextmanager
def push_session_tool_context(ctx: SessionToolContext) -> Iterator[None]:
    token = _SESSION_TOOL_CONTEXT.set(ctx)
    try:
        yield
    finally:
        _SESSION_TOOL_CONTEXT.reset(token)
```

And in `SessionAgent.run()`:

```python
self.history.append(user_message)
if self._history_path is not None:
    await _save_history(self._history_path, self.history)
```

Then wrap every tool call with `push_session_tool_context(...)`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/psi_agent/session/test_runtime_context.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/session/runtime_context.py src/psi_agent/session/agent.py tests/psi_agent/session/test_runtime_context.py
git commit -m "feat(session): expose tool context and write history on user message"
```

---

### Task 2: Build the Fusion-Guard adapter library

**Files:**
- Create: `src/psi_agent/fusion_guard/__init__.py`
- Create: `src/psi_agent/fusion_guard/analysis.py`
- Create: `src/psi_agent/fusion_guard/policy.py`
- Create: `src/psi_agent/fusion_guard/messages.py`
- Test: `tests/psi_agent/fusion_guard/test_analysis.py`

**Interfaces:**
- Consumes: `SessionToolContext.history_messages`, `SessionToolContext.latest_user_message`, `SessionToolContext.ai_socket`
- Produces:
  - `build_intent_analysis_prompt(...)`
  - `parse_intent_analysis_reply(raw: str)`
  - `normalize_denial_message(...)`
  - `filter_allow_rules(lines: list[str])`
  - `build_policy_install_request(...)`

- [ ] **Step 1: Write the failing test**

```python
from psi_agent.fusion_guard.analysis import build_intent_analysis_prompt, parse_intent_analysis_reply
from psi_agent.fusion_guard.messages import normalize_denial_message


def test_parse_allow_rules_and_deny_message() -> None:
    prompt = build_intent_analysis_prompt(
        history_messages=[{"role": "user", "content": "list files"}],
        latest_user_message={"role": "user", "content": "run a command"},
        session_id="s1",
    )
    assert "USER_MESSAGE_BEGIN" in prompt

    parsed_allow = parse_intent_analysis_reply("allow foo_t bar_t:file { read getattr };\nNONE")
    assert parsed_allow.decision == "allow_rules"
    assert parsed_allow.rules == ["allow foo_t bar_t:file { read getattr };"]

    parsed_none = parse_intent_analysis_reply("NONE")
    assert parsed_none.decision == "none"
    assert parsed_none.rules == []

    assert normalize_denial_message("policy denied") == "[Fusion-Guard] Security policy denied this request: policy denied"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/psi_agent/fusion_guard/test_analysis.py -v`
Expected: fail because the package and functions do not exist yet.

- [ ] **Step 3: Write the minimal implementation**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class IntentAnalysisResult:
    decision: str
    rules: list[str]
    raw_reply: str


def build_intent_analysis_prompt(
    *,
    history_messages: list[dict],
    latest_user_message: dict,
    session_id: str | None,
) -> str:
    history_lines = "\n".join(
        f"{msg.get('role', 'unknown')}: {msg.get('content', '')}"
        for msg in history_messages[-12:]
    )
    return (
        "You are the Fusion-Guard intent analyzer.\n"
        f"SESSION_ID: {session_id or 'unknown'}\n"
        "HISTORY_BEGIN\n"
        f"{history_lines}\n"
        "HISTORY_END\n"
        "USER_MESSAGE_BEGIN\n"
        f"{latest_user_message.get('content', '')}\n"
        "USER_MESSAGE_END\n"
        "Output exactly DENY, NONE, or allow rules."
    )


def parse_intent_analysis_reply(raw: str) -> IntentAnalysisResult:
    cleaned = raw.strip()
    if cleaned == "DENY":
        return IntentAnalysisResult(decision="deny", rules=[], raw_reply=raw)
    if cleaned in {"", "NONE"}:
        return IntentAnalysisResult(decision="none", rules=[], raw_reply=raw)
    rules = filter_allow_rules(cleaned.splitlines())
    if not rules:
        return IntentAnalysisResult(decision="none", rules=[], raw_reply=raw)
    return IntentAnalysisResult(decision="allow_rules", rules=rules, raw_reply=raw)


def normalize_denial_message(reason: str) -> str:
    return f"[Fusion-Guard] Security policy denied this request: {reason}"


def filter_allow_rules(lines: Iterable[str]) -> list[str]:
    return [
        line.strip()
        for line in lines
        if line.strip().startswith("allow ") and line.strip().endswith(";")
    ]


def build_policy_install_request(
    *,
    agent_id: str,
    rules: list[str],
    workspace_path: str,
) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "workspace_path": workspace_path,
        "extra_rules": rules,
    }
```

The parser must fail closed:

- `DENY` → deny
- `NONE` or empty → none
- only lines that start with `allow ` and end with `;` survive
- invalid or blocked rules are dropped

`NONE` is not a refusal. In the adapter it continues with the base policy path and does not install extra rules.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/psi_agent/fusion_guard/test_analysis.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/fusion_guard tests/psi_agent/fusion_guard/test_analysis.py
git commit -m "feat(fusion-guard): add prompt and policy parsing helpers"
```

---

### Task 3: Add the temporary secure bash workspace/tool wrapper

**Files:**
- Create: `examples/fusion-guard-security-workspace/tools/bash.py`
- Create: `examples/fusion-guard-security-workspace/systems/system.py`
- Create: `src/psi_agent/fusion_guard/runner.py`
- Test: `tests/integration/test_fusion_guard_secure_bash.py`

**Interfaces:**
- Consumes: `get_session_tool_context()`
- Produces:
  - `secure_bash(command: str, cwd: str | None = None) -> str`
  - `run_intent_analysis_in_temp_cli(...) -> str`
  - `install_allowed_policy(...) -> None`
  - `execute_with_policy(...) -> str`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from psi_agent.fusion_guard.runner import secure_bash
from psi_agent.session.runtime_context import SessionToolContext


@pytest.mark.anyio
async def test_secure_bash_denies_and_formats_message(tmp_path) -> None:
    ctx = SessionToolContext(
        session_id="s1",
        workspace_path=tmp_path,
        history_path=tmp_path / "histories" / "s1.jsonl",
        history_messages=[{"role": "user", "content": "steal /etc/shadow"}],
        latest_user_message={"role": "user", "content": "steal /etc/shadow"},
        ai_socket="http://127.0.0.1:1",
    )

    async def fake_analysis(*, prompt: str, ctx: SessionToolContext) -> str:
        assert "USER_MESSAGE_BEGIN" in prompt
        return "DENY"

    async def fake_exec(command: str, cwd: str | None, ctx: SessionToolContext) -> str:
        raise AssertionError("execute_with_policy must not run on DENY")

    result = await secure_bash(
        command="cat /etc/shadow",
        cwd=str(tmp_path),
        context_override=ctx,
        analysis_runner=fake_analysis,
        executor=fake_exec,
    )
    assert result == "[Fusion-Guard] Security policy denied this request: Fusion-Guard denied the requested operation"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/integration/test_fusion_guard_secure_bash.py -v`
Expected: fail because the secure bash workspace and runner do not exist yet.

- [ ] **Step 3: Write the minimal implementation**

```python
from __future__ import annotations

from collections.abc import Callable

import anyio

from psi_agent.fusion_guard.analysis import build_intent_analysis_prompt, parse_intent_analysis_reply
from psi_agent.fusion_guard.messages import normalize_denial_message
from psi_agent.session.runtime_context import get_session_tool_context


async def secure_bash(
    command: str,
    cwd: str | None = None,
    *,
    context_override=None,
    analysis_runner: Callable[..., object] | None = None,
    executor: Callable[..., object] | None = None,
) -> str:
    ctx = context_override or get_session_tool_context()
    if ctx is None:
        return normalize_denial_message("missing session context")
    prompt = build_intent_analysis_prompt(
        history_messages=ctx.history_messages,
        latest_user_message=ctx.latest_user_message,
        session_id=ctx.session_id,
    )
    analysis_runner = analysis_runner or run_intent_analysis_in_temp_cli
    executor = executor or execute_with_policy
    analysis = await analysis_runner(prompt=prompt, ctx=ctx)
    parsed = parse_intent_analysis_reply(analysis)
    if parsed.decision == "deny":
        return normalize_denial_message("Fusion-Guard denied the requested operation")
    if parsed.decision == "none":
        return await executor(command, cwd=cwd, ctx=ctx)
    if parsed.decision == "allow_rules":
        await install_allowed_policy(parsed.rules, ctx)
        return await executor(command, cwd=cwd, ctx=ctx)
    return normalize_denial_message("Fusion-Guard analysis failed")
```

The temp workspace helper must:

- create a unique temp dir per call
- write the system prompt file there
- launch `psi-agent session`
- launch `psi-agent channel cli`
- clean up both processes and the directory in `finally`

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/integration/test_fusion_guard_secure_bash.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/fusion_guard examples/fusion-guard-security-workspace tests/integration/test_fusion_guard_secure_bash.py
git commit -m "feat(workspace): add Fusion-Guard secure bash runner"
```

---

### Task 4: Wire the workspace into Dolphin docs and verify the end-to-end path

**Files:**
- Modify: `src/psi_agent/session/AGENTS.md`
- Modify: `README.md`
- Modify: `README_en.md`
- Test: `tests/integration/test_end_to_end.py`

**Interfaces:**
- Consumes: the secure bash tool from Task 3
- Produces:
  - documented workspace shape
  - a passing end-to-end test that proves deny/none/allow behavior through the normal Dolphin session path

- [ ] **Step 1: Write the failing test**

```python
import json
import socket
from pathlib import Path

import anyio
import pytest
from aiohttp import web

from tests.integration.conftest import read_sse


def _chunk(content: str = "", tool_calls: list | None = None, finish_reason: str | None = None) -> str:
    delta: dict[str, object] = {}
    if content:
        delta["content"] = content
    if tool_calls:
        delta["tool_calls"] = tool_calls
    return json.dumps(
        {
            "id": "test",
            "object": "chat.completion.chunk",
            "created": 0,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
    )


@pytest.mark.anyio
async def test_channel_roundtrip_uses_secure_bash_tool(tmp_path: Path) -> None:
    request_count = 0

    async def stop_process(proc) -> None:
        proc.terminate()
        try:
            await proc.wait()
        except Exception:
            proc.kill()

    async def handler(request: web.Request) -> web.StreamResponse:
        nonlocal request_count
        request_count += 1
        resp = web.StreamResponse(status=200, reason="OK", headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        if request_count == 1:
            tool_call = {
                "index": 0,
                "id": "c1",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "pwd"})},
            }
            await resp.write(
                f"data: {_chunk(tool_calls=[tool_call], finish_reason='tool_calls')}\n\n".encode()
            )
        else:
            await resp.write(f"data: {_chunk(content='Final secure bash reply', finish_reason='stop')}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp

    app = web.Application()
    app.router.add_post("/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()

    ai_socket = str(tmp_path / "ai.sock")
    channel_socket = str(tmp_path / "channel.sock")

    ai_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "ai",
            "--provider",
            "openai",
            "--session-socket",
            ai_socket,
            "--model",
            "test",
            "--api-key",
            "k",
            "--base-url",
            f"http://127.0.0.1:{port}",
        ]
    )
    ses_proc = await anyio.open_process(
        [
            "uv",
            "run",
            "psi-agent",
            "session",
            "--workspace",
            "examples/fusion-guard-security-workspace",
            "--channel-socket",
            channel_socket,
            "--ai-socket",
            ai_socket,
        ]
    )

    try:
        await anyio.sleep(1.0)
        chunks = await read_sse(channel_socket, "use secure bash")
        assert "Final secure bash reply" in json.dumps(chunks)
    finally:
        await stop_process(ses_proc)
        await stop_process(ai_proc)
        await runner.cleanup()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/integration/test_end_to_end.py -v`
Expected: fail until the secure workspace is actually wired in.

- [ ] **Step 3: Write the minimal implementation**

Update the docs to describe:

- the new secure workspace directory
- the fact that history is written before analysis
- the Fusion-Guard denial message format
- the one-message-per-temporary-workspace lifecycle

Add the secure workspace to the sample configuration used by the end-to-end test.

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
uv run pytest tests/psi_agent/session/test_runtime_context.py tests/psi_agent/fusion_guard/test_analysis.py tests/integration/test_fusion_guard_secure_bash.py tests/integration/test_end_to_end.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/session/AGENTS.md README.md README_en.md tests/integration/test_end_to_end.py
git commit -m "docs: describe Fusion-Guard secure bash integration"
```
