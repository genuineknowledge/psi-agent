# Haitun 发送文件能力 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 haitun-workspace 的 system prompt 中新增一段说明，教会 Haitun agent 用已有的 `[SEND:/绝对路径]` 标记把文件发到用户的 Telegram / 飞书聊天窗口。

**Architecture:** 纯 prompt 层改动。框架已内置 `[SEND:path]` → `FileChunk` → channel 上传文件的能力（Telegram 与飞书均支持），本计划只在 workspace 里加一个 prompt section 常量并注入到稳定前缀，不改任何框架代码、不加工具。

**Tech Stack:** Python 3.14+, psi-agent workspace prompt engine（`systems/prompt_sections.py` + `systems/system.py`），uv。

## Global Constraints

- 只修改 `examples/haitun-workspace/` 下的文件，绝不改动框架代码（`src/psi_agent/**`）。
- 不新增工具文件、不加第三方依赖、不做邮箱集成。
- section 正文用英文，风格对齐现有 section（如 `TOOL_CALL_STYLE_SECTION`）；渠道中立措辞（"user's chat window"），同时覆盖 Telegram 与飞书。
- 注入位置：稳定前缀内、`TOOL_CALL_STYLE_SECTION` 之后、`EXECUTION_BIAS_SECTION` 之前。
- 工作目录：worktree `C:/Users/12815/psi-agent-inputfiles`（分支 `fix/input-files-directly`）。

---

### Task 1: 新增 SEND_FILES_SECTION 常量并注入系统提示

**Files:**
- Modify: `examples/haitun-workspace/systems/prompt_sections.py`（在 `TOOL_CALL_STYLE_SECTION` 定义之后新增常量）
- Modify: `examples/haitun-workspace/systems/system.py`（import 列表 + `build_system_prompt` 的 `stable_parts`）
- Test: `examples/haitun-workspace/systems/system.py` 的 `__main__` smoke test（无独立测试框架，用断言脚本验证）

**Interfaces:**
- Produces: 模块常量 `prompt_sections.SEND_FILES_SECTION: str`，其首行为 `## Sending Files to the User`。由 `system.py` import 并插入稳定前缀。
- Consumes: 现有 `TOOL_CALL_STYLE_SECTION`（注入锚点）、`build_system_prompt()` 的 `stable_parts` 列表结构。

- [ ] **Step 1: 写验证脚本（失败测试）**

在 worktree 根目录创建临时验证脚本 `/tmp/verify_send_files.py`：

```python
import anyio
import sys
from pathlib import Path

WS = Path("examples/haitun-workspace/systems")
sys.path.insert(0, str(WS))

from system import system_prompt_builder, CACHE_BOUNDARY

prompt = anyio.run(system_prompt_builder)

# 1) section 存在
assert "## Sending Files to the User" in prompt, "缺少 Sending Files section"
# 2) 教了 [SEND:...] 标记
assert "[SEND:" in prompt, "section 未提及 [SEND:] 标记"
# 3) 位于稳定前缀（cache boundary 之前）
head = prompt.split(CACHE_BOUNDARY)[0]
assert "## Sending Files to the User" in head, "section 不在稳定前缀里"
# 4) 位置在 Tool Call Style 之后、Execution Bias 之前
i_tcs = head.index("## Tool Call Style")
i_send = head.index("## Sending Files to the User")
i_exec = head.index("## Execution Bias")
assert i_tcs < i_send < i_exec, f"顺序错误: tcs={i_tcs} send={i_send} exec={i_exec}"
print("OK: SEND_FILES_SECTION 已正确注入")
```

- [ ] **Step 2: 运行验证脚本，确认失败**

Run（在 worktree 根 `C:/Users/12815/psi-agent-inputfiles`）：
```bash
uv run python /tmp/verify_send_files.py
```
Expected: FAIL —— `AssertionError: 缺少 Sending Files section`

- [ ] **Step 3: 在 prompt_sections.py 新增常量**

在 `examples/haitun-workspace/systems/prompt_sections.py` 中，`TOOL_CALL_STYLE_SECTION` 定义结束（第 72 行的 `"""` 之后）与 `# Execution Bias` 注释块之间，插入：

```python
# ---------------------------------------------------------------------------
# Sending Files
# ---------------------------------------------------------------------------

SEND_FILES_SECTION = """\
## Sending Files to the User
You can deliver files straight to the user's chat window (Telegram and Feishu both support this). To send a file, put a marker on its own line: [SEND:<absolute-path>]. The channel detects it and uploads the file to the user — images show inline, other types arrive as a document.

- Generate or locate the file first (write / bash / powershell), then reference it by ABSOLUTE path.
- One marker per file; put each marker on its own line at the end of your reply.
- Only send files that exist and that the user asked for or would expect.
- The marker text itself may stay visible in the chat, so keep the prose above it self-contained; do not rely on the marker reading like part of a sentence.
- If the user asks for a document (for example a Word .docx), create it locally with your tools first, then send it with the marker. If a needed library is missing, install it or fall back to a format you can produce.\
"""
```

- [ ] **Step 4: 在 system.py 的 import 列表加入 SEND_FILES_SECTION**

在 `examples/haitun-workspace/systems/system.py` 的 `from prompt_sections import (...)` 块中，按字母/就近位置加入一行。找到：

```python
    SAFETY_SECTION,
    SILENT_REPLIES_SECTION,
```
改为：
```python
    SAFETY_SECTION,
    SEND_FILES_SECTION,
    SILENT_REPLIES_SECTION,
```

- [ ] **Step 5: 在 build_system_prompt 的 stable_parts 注入 section**

在 `examples/haitun-workspace/systems/system.py` 的 `build_system_prompt` 中，找到当前的稳定前缀块：

```python
        stable_parts += [
            "",
            build_tooling_section(tools),
            "",
            TOOL_CALL_STYLE_SECTION,
            "",
            EXECUTION_BIAS_SECTION,
            "",
            SAFETY_SECTION,
            "",
            FUSION_MEMORY_SECTION,
        ]
```
改为（在 `TOOL_CALL_STYLE_SECTION` 之后插入 `SEND_FILES_SECTION`）：
```python
        stable_parts += [
            "",
            build_tooling_section(tools),
            "",
            TOOL_CALL_STYLE_SECTION,
            "",
            SEND_FILES_SECTION,
            "",
            EXECUTION_BIAS_SECTION,
            "",
            SAFETY_SECTION,
            "",
            FUSION_MEMORY_SECTION,
        ]
```

- [ ] **Step 6: 运行验证脚本，确认通过**

Run：
```bash
uv run python /tmp/verify_send_files.py
```
Expected: PASS —— 打印 `OK: SEND_FILES_SECTION 已正确注入`

- [ ] **Step 7: 运行 workspace 自带 smoke test，确认 prompt 正常组装**

Run：
```bash
uv run python examples/haitun-workspace/systems/system.py
```
Expected: 正常打印完整 system prompt（无异常），且包含 `## Sending Files to the User`。

- [ ] **Step 8: 清理临时脚本并提交**

```bash
rm -f /tmp/verify_send_files.py
git add examples/haitun-workspace/systems/prompt_sections.py examples/haitun-workspace/systems/system.py
git commit -m "feat(haitun): teach agent to send files to user via [SEND:] marker"
```
