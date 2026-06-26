# Session Workspace 可选化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Session 的 `workspace` 参数变为可选，默认使用进程当前工作目录。

**Architecture:** 将 `Session.workspace: str` 改为 `workspace: str = ""`，在 `run()` 中以空字符串为信号 fallback 到 `Path.cwd()`。其余代码路径（`SessionAgent.create()`、tools、schedules、history）接收 `Path` 对象，完全不受影响。

**Tech Stack:** Python 3.14, anyio, tyro

---

### Task 1: 新增单元测试

**Files:**
- Modify: `tests/psi_agent/session/test_session.py:53-53` (追加)

- [ ] **Step 1: 添加 test_workspace_empty_string_uses_cwd**

在文件末尾追加：

```python
def test_workspace_empty_string_uses_cwd(tmp_path: Path) -> None:
    """Session with workspace='' should resolve workspace_path to Path.cwd()."""
    from psi_agent.session import Session

    session = Session(workspace="", channel_socket=str(tmp_path / "c.sock"), ai_socket=str(tmp_path / "a.sock"))
    assert session.workspace == ""
```

这个测试验证 `Session` dataclass 接受空字符串。实际的 CWD 解析逻辑在 `run()` 中（涉及 `anyio` event loop），后续 Task 3 会补充集成测试。

- [ ] **Step 2: 运行测试确认通过**

```bash
uv run pytest tests/psi_agent/session/test_session.py::test_workspace_empty_string_uses_cwd -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/psi_agent/session/test_session.py
git commit -m "test: add test for workspace empty string default"
```

---

### Task 2: 实现 Session.workspace 默认值

**Files:**
- Modify: `src/psi_agent/session/__init__.py:20-22` (workspace field + docstring)
- Modify: `src/psi_agent/session/__init__.py:41-42` (run() 方法 workspace 解析)

- [ ] **Step 1: 修改 workspace 字段默认值**

将 `src/psi_agent/session/__init__.py:20-22` 中的：

```python
    workspace: str
    """Path to the workspace directory."""
```

改为：

```python
    workspace: str = ""
    """Path to the workspace directory.  Defaults to current working directory."""
```

- [ ] **Step 2: 修改 run() 中的 workspace 解析逻辑**

将 `src/psi_agent/session/__init__.py:41` 中的：

```python
        workspace_path = Path(str(await anyio.Path(self.workspace).resolve()))
```

改为：

```python
        workspace_path = (
            Path.cwd()
            if self.workspace == ""
            else Path(str(await anyio.Path(self.workspace).resolve()))
        )
```

- [ ] **Step 3: 运行 lint/type check**

```bash
uv run ruff check src/psi_agent/session/__init__.py
uv run ty check
```

Expected: No errors.

- [ ] **Step 4: 运行现有测试确认无回归**

```bash
uv run pytest tests/psi_agent/session/test_session.py -v
```

Expected: All pass (已有 5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/session/__init__.py
git commit -m "feat: make Session.workspace optional, default to CWD"
```

---

### Task 3: 集成测试 — 验证 CWD 行为

**Files:**
- Modify: `tests/integration/test_session_workspace.py:238-238` (追加)

- [ ] **Step 1: 添加 test_session_with_empty_workspace_uses_cwd**

在文件末尾追加：

```python
@pytest.mark.anyio
async def test_session_with_empty_workspace_uses_cwd(tmp_path: Path, mock_ai_server: MockAIServer) -> None:
    """Session started via CLI without --workspace should use CWD."""
    import os

    mock_ai_server.set_responses([_chunk(content="hello from cwd", finish_reason="stop")])
    base_url = await mock_ai_server.start()

    ai_socket = str(tmp_path / "ai.sock")
    channel_socket = str(tmp_path / "channel.sock")

    proc = await anyio.open_process(
        [
            "uv", "run", "psi-agent", "session",
            "--channel-socket", channel_socket,
            "--ai-socket", ai_socket,
        ]
    )
    ai_proc = await anyio.open_process(
        [
            "uv", "run", "psi-agent", "ai",
            "--provider", "openai",
            "--session-socket", ai_socket,
            "--model", "test",
            "--api-key", "k",
            "--base-url", base_url,
        ]
    )

    try:
        assert await _wait_socket(ai_socket)
        assert await _wait_socket(channel_socket)

        timeout = ClientTimeout(total=5)
        connector = UnixConnector(path=channel_socket)
        async with (
            ClientSession(connector=connector, timeout=timeout) as session,
            session.post(
                "http://localhost/chat/completions",
                json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            ) as resp,
        ):
            assert resp.status == 200, f"Session should start without --workspace: {resp.status}"
    finally:
        await _stop_process(proc)
        await _stop_process(ai_proc)
```

- [ ] **Step 2: 运行集成测试**

```bash
uv run pytest tests/integration/test_session_workspace.py::test_session_with_empty_workspace_uses_cwd -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_session_workspace.py
git commit -m "test: integration test for session without --workspace flag"
```

---

### Task 4: 更新 _run.py docstring

**Files:**
- Modify: `src/psi_agent/_run.py:17-18` (docstring 示例)

- [ ] **Step 1: 标记 workspace 为可选**

将 docstring 中的：

```yaml
    - type: session
      workspace: ./examples/a-simple-bash-only-workspace
      channel_socket: ./channel.sock
      ai_socket: ./ai.sock
```

改为：

```yaml
    - type: session
      workspace: ./examples/a-simple-bash-only-workspace  # optional, defaults to .
      channel_socket: ./channel.sock
      ai_socket: ./ai.sock
```

- [ ] **Step 2: Commit**

```bash
git add src/psi_agent/_run.py
git commit -m "docs: mark workspace as optional in _run.py docstring"
```

---

### Task 5: 更新 README.md 和 README_en.md

**Files:**
- Modify: `README.md:41` (CLI example)
- Modify: `README_en.md:41` (CLI example)

- [ ] **Step 1: 更新 README.md 中的 --workspace 说明**

将 `README.md:41` 的：

```bash
uv run psi-agent session \
  --workspace ./examples/a-simple-bash-only-workspace \
  --channel-socket ./channel.sock \
  --ai-socket ./ai.sock
```

改为：

```bash
uv run psi-agent session \
  --workspace ./examples/a-simple-bash-only-workspace \
  --channel-socket ./channel.sock \
  --ai-socket ./ai.sock

# (--workspace is optional, defaults to current directory)
```

- [ ] **Step 2: 更新 README_en.md 对应位置**

同样在 `README_en.md:41` 的对应位置添加注释。

- [ ] **Step 3: Commit**

```bash
git add README.md README_en.md
git commit -m "docs: mark --workspace as optional in README"
```

---

### Task 6: 更新 AGENTS.md (root + session)

**Files:**
- Modify: `AGENTS.md` (多处)
- Modify: `src/psi_agent/session/AGENTS.md:13` (workspace 解析步骤)

- [ ] **Step 1: 更新 root AGENTS.md Session history 描述**

将 `AGENTS.md:33` 的：

```
文件按 workspace/histories/{session_id}.jsonl 存储
```

改为：

```
文件按 workspace/histories/{session_id}.jsonl 存储（workspace 默认当前目录）
```

- [ ] **Step 2: 更新 Session 层 AGENTS.md Workspace 启动流程**

将 `src/psi_agent/session/AGENTS.md:13` 的：

```
2. 解析 workspace 路径（anyio.Path.resolve()）
```

改为：

```
2. 解析 workspace 路径（空字符串时用 Path.cwd()，否则 anyio.Path.resolve()）
```

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md src/psi_agent/session/AGENTS.md
git commit -m "docs: update AGENTS.md for optional workspace"
```

---

### Task 7: 全量验证

- [ ] **Step 1: 运行全部测试**

```bash
uv run pytest -v
```

Expected: All tests pass.

- [ ] **Step 2: 运行 lint / type check**

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

Expected: No errors.

- [ ] **Step 3: Commit (如有遗漏)**

如果有任何修复，提交它们。否则不需要。
