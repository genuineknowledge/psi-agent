# 协议层统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 psi-agent 跨组件 SSE 协议的格式定义、常量与行为规则收敛到 `psi_agent/protocol.py`，飞书路由判定收敛到 `psi_agent/_feishu_routing.py`；除 `[SEND:]` 空路径缺口修复外行为零变更。

**Architecture:** 新建两个与五个组件平级的共享模块。`protocol.py` 分三层——格式层（线格式 dataclass + 构造/解析函数）、常量层（`FINISH_REASON_*` / `REASONING_KIND_*`）、语义层（`AUXILIARY_FINISH_REASONS` frozenset + 两个判断函数）。`session/protocol.py` 改为重导出共享定义，保留 Session 专属类型，使现有 import 路径与测试全部不变。

**Tech Stack:** Python 3.14、anyio、aiohttp、loguru、pytest + pytest-asyncio（`asyncio_mode="auto"`，anyio backend）、ruff、ty

## Global Constraints

- **零抑制**：不堆 `noqa`，不设 `per-file-ignores`。代码本身应符合 ruff 规则。
- **一切异步**：所有 IO 使用 `anyio`，禁用 `asyncio` 原生 API 与 `pathlib`（本计划新增的都是纯函数，不涉及 IO）。
- **类型精确化**：避免裸 `tuple`/`dict`，用 `tuple[X, Y]` 等具体类型。
- **注释与 docstring 语言**：`src/psi_agent/` 下英文 docstring（跟随 `session/protocol.py` 既有风格）；飞书相关模块可用中文（跟随 `channel/feishu/client.py` 与 `gateway/_feishu_manager.py` 既有风格）。
- **每 chunk 都要有 DEBUG 日志**：本计划不删除任何现有 `logger.debug`；替换切片/构造逻辑时保留原有日志语句与措辞。
- **行为零变更**：除 Task 4 修复的 `[SEND:]` 空路径缺口外，任何步骤都不得改变可观测行为。现有测试一行不改并全部通过是硬性验收条件。
- **测试目录镜像 `src/psi_agent/`**，每层目录必须有 `__init__.py`（`tests/psi_agent/__init__.py` 已存在）。
- **验证命令**：`uv run pytest`、`uv run ruff check`、`uv run ty check` 三条全绿。
- **不做的事**：不建包（单 `.py` 文件）、不动五组件边界、不定义 OpenAI 标准字段结构、不引入协议版本号、不拆 Session、不做契约测试套件、不统一 `any_llm.api.ChatCompletionChunk`。

## Task Dependency Map

```
Task 1 (protocol.py + tests)
  ├─→ Task 2 (session/protocol.py 重导出)
  │     └─→ Task 5 (Session 替换)
  ├─→ Task 6 (AI 替换)
  ├─→ Task 7 (Router 替换)
  ├─→ Task 8 (Channel _stream 替换)
  └─→ Task 9 (Gateway SSE 替换)

Task 3 (_feishu_routing.py + tests)
  ├─→ Task 10 (Gateway 飞书替换)
  └─→ Task 11 (Channel 飞书替换)

Task 4 (SEND 正则收敛) —— 独立，可任意时点插入
Task 12 (session/__init__.py 导出) —— 独立
Task 13 (文档同步) —— 最后
```

---

### Task 1: 新建 `psi_agent/protocol.py` 共享协议模块

不动任何现有代码，纯新增。完成后可独立验证。

**Files:**
- Create: `src/psi_agent/protocol.py`
- Test: `tests/psi_agent/test_protocol.py`

**Interfaces:**
- Consumes: 无（本任务是所有协议替换任务的根依赖）
- Produces:
  - `DeltaMessage(content: str | None = None, role: str | None = None, reasoning: str | None = None, kind: str | None = None, tool_calls: list[dict[str, Any]] | None = None)` — dataclass，含 `to_dict() -> dict[str, Any]`
  - `StreamChoice(index: int = 0, delta: DeltaMessage = ..., finish_reason: str | None = None)` — dataclass，含 `to_dict() -> dict[str, Any]`
  - `ChatCompletionChunk(id: str = "chatcmpl-unknown", object: str = "chat.completion.chunk", created: int = 0, choices: list[StreamChoice] = ...)` — dataclass，含 `to_dict() -> dict[str, Any]` 与 `to_sse() -> str`
  - `REASONING_KIND_THINKING: str = "thinking"`、`REASONING_KIND_TOOL_CALL: str = "tool_call"`、`REASONING_KIND_TOOL_RESULT: str = "tool_result"`
  - `FINISH_REASON_STOP: str = "stop"`、`FINISH_REASON_TOOL_CALLS: str = "tool_calls"`、`FINISH_REASON_ERROR: str = "error"`、`FINISH_REASON_COMPACTION_NEEDED: str = "compaction_needed"`
  - `SSE_DONE: str = "[DONE]"`
  - `AUXILIARY_FINISH_REASONS: frozenset[str]`
  - `is_auxiliary_finish(value: str | None) -> bool`
  - `is_terminal_finish(value: str | None) -> bool`
  - `make_error_chunk(message: str) -> dict[str, Any]`
  - `make_compaction_signal(*, prompt_tokens: int, threshold: int) -> dict[str, Any]`
  - `parse_sse_data(line: str) -> str | None`

- [ ] **Step 1: 写失败测试**

创建 `tests/psi_agent/test_protocol.py`：

```python
"""Shared cross-component protocol module."""

from __future__ import annotations

import json

import pytest

from psi_agent.protocol import (
    AUXILIARY_FINISH_REASONS,
    FINISH_REASON_COMPACTION_NEEDED,
    FINISH_REASON_ERROR,
    FINISH_REASON_STOP,
    FINISH_REASON_TOOL_CALLS,
    SSE_DONE,
    ChatCompletionChunk,
    DeltaMessage,
    StreamChoice,
    is_auxiliary_finish,
    is_terminal_finish,
    make_compaction_signal,
    make_error_chunk,
    parse_sse_data,
)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("data: {\"a\":1}", '{"a":1}'),
        # The space after the colon is optional per the SSE spec.  Four call
        # sites used to require it and silently dropped whole frames without.
        ("data:{\"a\":1}", '{"a":1}'),
        ("data:   {\"a\":1}", '{"a":1}'),
        ("data: [DONE]", "[DONE]"),
        ("data:[DONE]", "[DONE]"),
        ("data:", ""),
        ("data: ", ""),
    ],
)
def test_parse_sse_data_extracts_payload(line: str, expected: str) -> None:
    assert parse_sse_data(line) == expected


@pytest.mark.parametrize("line", ["", "event: ping", ":heartbeat", "id: 1", "  data: x"])
def test_parse_sse_data_returns_none_for_non_data_lines(line: str) -> None:
    assert parse_sse_data(line) is None


@pytest.mark.parametrize(
    "value",
    [FINISH_REASON_STOP, FINISH_REASON_TOOL_CALLS, FINISH_REASON_ERROR, "length", "content_filter"],
)
def test_is_terminal_finish_accepts_terminal_and_unknown(value: str) -> None:
    """Unknown reasons count as terminal — only the auxiliary set is special."""
    assert is_terminal_finish(value) is True
    assert is_auxiliary_finish(value) is False


def test_compaction_is_auxiliary_not_terminal() -> None:
    assert is_auxiliary_finish(FINISH_REASON_COMPACTION_NEEDED) is True
    assert is_terminal_finish(FINISH_REASON_COMPACTION_NEEDED) is False


def test_none_is_neither_terminal_nor_auxiliary() -> None:
    """``None`` means the stream has not reported an end yet."""
    assert is_terminal_finish(None) is False
    assert is_auxiliary_finish(None) is False


def test_compaction_is_the_only_auxiliary_reason() -> None:
    assert AUXILIARY_FINISH_REASONS == frozenset({FINISH_REASON_COMPACTION_NEEDED})


def test_make_error_chunk_matches_shape_used_by_all_three_producers() -> None:
    """Shape must stay byte-identical to what ai/router/session emitted before."""
    assert make_error_chunk("[Upstream Error]: boom") == {
        "id": "error",
        "choices": [
            {
                "index": 0,
                "delta": {"content": "[Upstream Error]: boom"},
                "finish_reason": "error",
            }
        ],
    }


@pytest.mark.parametrize(
    "message",
    ["[Upstream Error]: boom", "[Router Error]: strategy failed", "bare agent error"],
)
def test_make_error_chunk_keeps_caller_prefix_verbatim(message: str) -> None:
    """Callers own their prefix; the helper never prepends one."""
    chunk = make_error_chunk(message)
    assert chunk["choices"][0]["delta"]["content"] == message


def test_make_compaction_signal_shape() -> None:
    assert make_compaction_signal(prompt_tokens=1234, threshold=1000) == {
        "id": "compaction",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "compaction_needed"}],
        "psi_compaction": {"needed": True, "prompt_tokens": 1234, "threshold": 1000},
    }


def test_sse_done_constant() -> None:
    assert SSE_DONE == "[DONE]"


def test_chat_completion_chunk_to_sse_round_trips() -> None:
    chunk = ChatCompletionChunk(
        id="chatcmpl-1",
        choices=[StreamChoice(delta=DeltaMessage(content="hi"), finish_reason="stop")],
    )
    sse = chunk.to_sse()
    assert sse.startswith("data: ")
    assert sse.endswith("\n\n")
    payload = parse_sse_data(sse.splitlines()[0])
    assert payload is not None
    assert json.loads(payload) == {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}],
    }


def test_delta_message_omits_unset_fields() -> None:
    assert DeltaMessage(content="x").to_dict() == {"content": "x"}
    assert DeltaMessage().to_dict() == {}


def test_stream_choice_omits_null_finish_reason() -> None:
    assert StreamChoice(delta=DeltaMessage(content="x")).to_dict() == {
        "index": 0,
        "delta": {"content": "x"},
    }
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/psi_agent/test_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'psi_agent.protocol'`

- [ ] **Step 3: 写实现**

创建 `src/psi_agent/protocol.py`：

```python
"""Wire protocol shared by every psi-agent component.

The five components (AI / Session / Channel / Router / Gateway) all speak
OpenAI Chat Completions over SSE.  This module is the single owner of that
contract: the wire-format types, every custom ``finish_reason`` value, and the
behaviour rules those values carry.  It sits beside the components rather than
inside one because it describes the agreement *between* layers.

Adding a ``finish_reason`` value, or changing how one is classified, means
editing this file and nothing else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Provenance for ``delta.reasoning`` / ``AgentChunk.reasoning`` (UI whitelist).
# Thinking + tool progress stay in one ``reasoning`` slot (Session<->AI shape
# isomorphism); ``kind`` discriminates render / filter without splitting the slot.
REASONING_KIND_THINKING = "thinking"
REASONING_KIND_TOOL_CALL = "tool_call"
REASONING_KIND_TOOL_RESULT = "tool_result"

# ``stop`` / ``tool_calls`` are OpenAI standard.  ``error`` and
# ``compaction_needed`` are psi-agent extensions used only between our own
# layers -- never exposed to an external caller.
FINISH_REASON_STOP = "stop"
FINISH_REASON_TOOL_CALLS = "tool_calls"
FINISH_REASON_ERROR = "error"
FINISH_REASON_COMPACTION_NEEDED = "compaction_needed"

SSE_DONE = "[DONE]"

# Auxiliary frames do not end the stream: they ride along *after* the model's
# real terminal frame and must never overwrite it.  Everything else -- including
# reasons we do not know about -- terminates.
AUXILIARY_FINISH_REASONS = frozenset({FINISH_REASON_COMPACTION_NEEDED})


def is_auxiliary_finish(value: str | None) -> bool:
    """Whether ``value`` is an auxiliary (non-terminating) finish reason."""
    return value in AUXILIARY_FINISH_REASONS


def is_terminal_finish(value: str | None) -> bool:
    """Whether ``value`` ends the stream.

    Unknown reasons count as terminal -- a reason we cannot classify is far
    more likely to be a real ending we have not met yet than a new auxiliary
    signal.  ``None`` is not terminal: the stream simply has not reported an
    end yet.
    """
    if value is None:
        return False
    return value not in AUXILIARY_FINISH_REASONS


@dataclass
class DeltaMessage:
    """One SSE delta fragment -- OpenAI Chat Completion Chunk format."""

    content: str | None = None
    role: str | None = None
    reasoning: str | None = None
    kind: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.content is not None:
            d["content"] = self.content
        if self.role is not None:
            d["role"] = self.role
        if self.reasoning is not None:
            d["reasoning"] = self.reasoning
        if self.kind is not None:
            d["kind"] = self.kind
        if self.tool_calls is not None:
            d["tool_calls"] = self.tool_calls
        return d


@dataclass
class StreamChoice:
    """A single choice in a streaming Chat Completion Chunk."""

    index: int = 0
    delta: DeltaMessage = field(default_factory=DeltaMessage)
    finish_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"index": self.index, "delta": self.delta.to_dict()}
        if self.finish_reason is not None:
            d["finish_reason"] = self.finish_reason
        return d


@dataclass
class ChatCompletionChunk:
    """OpenAI-compatible streaming Chat Completion Chunk."""

    id: str = "chatcmpl-unknown"
    object: str = "chat.completion.chunk"
    created: int = 0
    choices: list[StreamChoice] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": self.object,
            "created": self.created,
            "choices": [c.to_dict() for c in self.choices],
        }

    def to_sse(self) -> str:
        return f"data: {json.dumps(self.to_dict(), ensure_ascii=False)}\n\n"


def make_error_chunk(message: str) -> dict[str, Any]:
    """Build the streaming-error chunk every component sends after HTTP 200.

    ``message`` is used verbatim: each producer owns its own prefix
    (``[Upstream Error]: `` for AI, ``[Router Error]: `` for Router, the raw
    ``AgentError`` message for Session), so this helper never prepends one.

    Session detects ``finish_reason="error"`` and skips writing the turn to
    conversation history.
    """
    return {
        "id": "error",
        "choices": [
            {
                "index": 0,
                "delta": {"content": message},
                "finish_reason": FINISH_REASON_ERROR,
            }
        ],
    }


def make_compaction_signal(*, prompt_tokens: int, threshold: int) -> dict[str, Any]:
    """Build the context-compaction signal the AI layer appends after a stream.

    ``prompt_tokens`` / ``threshold`` are not log-only fields: Session feeds
    them into its compaction cooldown, and omitting them degrades the cooldown
    into fail-open (repeated back-to-back compaction).
    """
    return {
        "id": "compaction",
        "choices": [{"index": 0, "delta": {}, "finish_reason": FINISH_REASON_COMPACTION_NEEDED}],
        "psi_compaction": {"needed": True, "prompt_tokens": prompt_tokens, "threshold": threshold},
    }


def parse_sse_data(line: str) -> str | None:
    """Extract the payload of an SSE ``data:`` line.

    Returns ``None`` for blank or non-``data:`` lines.  The single space after
    the colon is *optional* per the SSE spec, so both ``data: X`` and ``data:X``
    yield ``X`` -- four call sites used to require the space and silently
    dropped whole frames without it.

    ``SSE_DONE`` is returned verbatim: callers differ on how to react to it
    (``return`` / ``continue`` / ``break``).
    """
    if not line.startswith("data:"):
        return None
    return line[5:].lstrip()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/psi_agent/test_protocol.py -v`
Expected: PASS（全部用例）

- [ ] **Step 5: Lint 与类型检查**

Run: `uv run ruff check src/psi_agent/protocol.py tests/psi_agent/test_protocol.py`
Expected: 无告警（若报 format 相关问题，跑 `uv run ruff format` 后重跑）

Run: `uv run ty check`
Expected: 通过

- [ ] **Step 6: Commit**

```bash
git add src/psi_agent/protocol.py tests/psi_agent/test_protocol.py
git commit -m "feat(protocol): 新建跨组件共享协议模块

线格式类型、finish_reason 常量、辅助帧/终止帧判断函数、error chunk 与
compaction 信号构造、SSE data 行解析集中到一处。parse_sse_data 顺带修正
data: 后空格必需的规范偏离 (SSE 规范中该空格可选)。

本提交纯新增, 不动任何现有代码。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `session/protocol.py` 改为重导出共享定义

**Files:**
- Modify: `src/psi_agent/session/protocol.py:1-92`（删除线格式类型与 `REASONING_KIND_*`，改为从 `psi_agent.protocol` 导入重导出）
- Test: `tests/psi_agent/session/test_protocol.py`（**不改**，作为迁移正确性验证）

**Interfaces:**
- Consumes: Task 1 的 `DeltaMessage` / `StreamChoice` / `ChatCompletionChunk` / `REASONING_KIND_*`
- Produces: `psi_agent.session.protocol` 继续导出上述全部符号（重导出），外加 Session 专属的 `AgentError` / `AgentRunStatus` / `AgentStopCause` / `AgentRunResult` / `AgentChunk` / `AiDelta`。**所有现有 import 路径保持有效。**

- [ ] **Step 1: 先确认现有测试通过（迁移前基线）**

Run: `uv run pytest tests/psi_agent/session/test_protocol.py -v`
Expected: PASS。记下用例数，Step 4 要对比。

- [ ] **Step 2: 改写文件头部**

把 `src/psi_agent/session/protocol.py` 的第 1-92 行（从模块 docstring 到 `ChatCompletionChunk.to_sse()` 结束）整段替换为：

```python
"""Types shared across the session layer — data models and serialisation.

The wire-format types and every shared protocol constant now live in
``psi_agent.protocol`` (the cross-component owner) and are re-exported here so
existing ``psi_agent.session.protocol`` imports keep working.  Prefer importing
shared names from ``psi_agent.protocol`` in new code; this module's own
contribution is the Session-only types below.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from psi_agent.protocol import (
    FINISH_REASON_COMPACTION_NEEDED,
    FINISH_REASON_ERROR,
    FINISH_REASON_STOP,
    FINISH_REASON_TOOL_CALLS,
    REASONING_KIND_THINKING,
    REASONING_KIND_TOOL_CALL,
    REASONING_KIND_TOOL_RESULT,
    ChatCompletionChunk,
    DeltaMessage,
    StreamChoice,
    is_auxiliary_finish,
    is_terminal_finish,
)

__all__ = [
    "FINISH_REASON_COMPACTION_NEEDED",
    "FINISH_REASON_ERROR",
    "FINISH_REASON_STOP",
    "FINISH_REASON_TOOL_CALLS",
    "REASONING_KIND_THINKING",
    "REASONING_KIND_TOOL_CALL",
    "REASONING_KIND_TOOL_RESULT",
    "AgentChunk",
    "AgentError",
    "AgentRunResult",
    "AgentRunStatus",
    "AgentStopCause",
    "AiDelta",
    "ChatCompletionChunk",
    "DeltaMessage",
    "StreamChoice",
    "is_auxiliary_finish",
    "is_terminal_finish",
]
```

保留第 95 行起的 `AgentError` 及其后全部 Session 专属类型不动。

注意：原文件的 `import json` 与 `from dataclasses import dataclass, field` 中，`json` 与 `field` 在删除线格式类型后不再被使用——上面的新 import 块已相应去掉它们。若 ruff 报未使用 import，核对是否有遗漏的使用点。

- [ ] **Step 3: 跑 session 协议测试确认零回归**

Run: `uv run pytest tests/psi_agent/session/test_protocol.py -v`
Expected: PASS，用例数与 Step 1 完全一致。**一个都不许改测试**——这就是本任务的验收标准。

- [ ] **Step 4: 跑全量测试确认无连带破坏**

Run: `uv run pytest`
Expected: 全绿。`session/channel_adapter.py` 等模块从 `session.protocol` 导入线格式类型，重导出后应无感。

- [ ] **Step 5: Lint 与类型检查**

Run: `uv run ruff check src/psi_agent/session/protocol.py`
Expected: 无告警

Run: `uv run ty check`
Expected: 通过（本步动了 `session/protocol.py` 的导出面，这是必须跑的一步）

- [ ] **Step 6: Commit**

```bash
git add src/psi_agent/session/protocol.py
git commit -m "refactor(session): protocol.py 改为重导出共享定义

线格式类型与 REASONING_KIND_* 已移入 psi_agent/protocol.py, 此处重导出以
保持现有 import 路径有效。session 专属类型 (AgentError / AgentRunStatus /
AgentStopCause / AgentRunResult / AgentChunk / AiDelta) 留在本层。

tests/psi_agent/session/test_protocol.py 一行未改并全部通过, 即迁移正确性
的验证。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 新建 `psi_agent/_feishu_routing.py`

不动任何现有代码，纯新增。与 Task 1 无依赖关系，可并行。

**Files:**
- Create: `src/psi_agent/_feishu_routing.py`
- Test: `tests/psi_agent/test_feishu_routing.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `is_group_chat(chat_id: str, chat_type: str) -> bool`
  - `route_key(open_id: str, chat_id: str, chat_type: str) -> str`
  - `GROUP_CHAT_TYPES: frozenset[str]`（公开常量，两个组件都要用，故不加下划线）

- [ ] **Step 1: 写失败测试**

创建 `tests/psi_agent/test_feishu_routing.py`：

```python
"""飞书路由判定 —— 群聊/私聊分流与路由键派生。"""

from __future__ import annotations

import pytest

from psi_agent._feishu_routing import GROUP_CHAT_TYPES, is_group_chat, route_key


@pytest.mark.parametrize("chat_type", ["group", "topic"])
def test_group_types_with_chat_id_are_group(chat_type: str) -> None:
    assert is_group_chat("oc_abc", chat_type) is True


@pytest.mark.parametrize("chat_type", ["group", "topic"])
def test_group_types_without_chat_id_fall_back_to_dm(chat_type: str) -> None:
    """chat_id 缺失时不能按群路由, 否则建出 feishu-chat- 这种无主 session。"""
    assert is_group_chat("", chat_type) is False


@pytest.mark.parametrize("chat_type", ["p2p", "", "unknown"])
def test_non_group_types_are_never_group(chat_type: str) -> None:
    assert is_group_chat("oc_abc", chat_type) is False
    assert is_group_chat("", chat_type) is False


def test_group_chat_types_membership() -> None:
    assert GROUP_CHAT_TYPES == frozenset({"group", "topic"})


@pytest.mark.parametrize("chat_type", ["group", "topic"])
def test_route_key_for_group_uses_chat_id(chat_type: str) -> None:
    assert route_key("ou_sender", "oc_abc", chat_type) == "chat:oc_abc"


@pytest.mark.parametrize(
    ("chat_id", "chat_type"),
    [("", "group"), ("", "topic"), ("oc_abc", "p2p"), ("", "p2p"), ("", "")],
)
def test_route_key_for_dm_uses_bare_open_id(chat_id: str, chat_type: str) -> None:
    assert route_key("ou_sender", chat_id, chat_type) == "ou_sender"


def test_route_key_namespaces_do_not_collide() -> None:
    """chat: 前缀隔离两个命名空间, 免得 chat_id 与 open_id 相撞。"""
    group = route_key("ou_x", "oc_x", "group")
    dm = route_key("oc_x", "", "p2p")
    assert group != dm
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/psi_agent/test_feishu_routing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'psi_agent._feishu_routing'`

- [ ] **Step 3: 写实现**

创建 `src/psi_agent/_feishu_routing.py`：

```python
"""飞书私聊/群聊路由判定 —— Gateway 与 Channel 共用。

判定曾在三处各写一遍 (``gateway/_feishu_manager.py`` 的 ``_is_group``、
``channel/feishu/client.py`` 的 ``_cache_key`` 与一处内联判定), 群聊类型常量
另在两处各定义一遍。判定漂移会让两个陌生人共享同一份上下文与 workspace ——
是**隐私事故**而非美观问题, 故收敛到此处唯一定义。

放在 ``psi_agent`` 顶层 (与 ``_appdata`` / ``_sockets`` 同级) 而非任一组件内,
避免在 Gateway 与 Channel 之间新造一条跨组件依赖。

``session_id`` / workspace 目录派生时的 ``-`` → ``_`` 转义**不在此处**: 那只
服务 Gateway 侧, Channel 不派生这些, 上提会把单方职责伪装成共享契约。
"""

from __future__ import annotations

GROUP_CHAT_TYPES = frozenset({"group", "topic"})


def is_group_chat(chat_id: str, chat_type: str) -> bool:
    """群聊判定: 类型是 group/topic **且** ``chat_id`` 非空。

    ``chat_id`` 缺失时不能按群路由 (否则会建出 ``feishu-chat-`` 这种无主
    session), 故退回按发送者 open_id —— 宁可不隔离, 也不建垃圾 session。
    """
    return chat_type in GROUP_CHAT_TYPES and bool(chat_id)


def route_key(open_id: str, chat_id: str, chat_type: str) -> str:
    """路由表 / socket 缓存共用的键: 群聊 ``chat:<chat_id>``, 私聊裸 ``open_id``。

    ``chat:`` 前缀隔离两个命名空间, 免得 chat_id 与 open_id 相撞。群聊整群共用
    一个键 (同群不同发言者须命中同一条缓存, 否则每人各打一次 Gateway)。
    """
    if is_group_chat(chat_id, chat_type):
        return f"chat:{chat_id}"
    return open_id
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/psi_agent/test_feishu_routing.py -v`
Expected: PASS（全部用例）

- [ ] **Step 5: Lint 与类型检查**

Run: `uv run ruff check src/psi_agent/_feishu_routing.py tests/psi_agent/test_feishu_routing.py`
Expected: 无告警

Run: `uv run ty check`
Expected: 通过

- [ ] **Step 6: Commit**

```bash
git add src/psi_agent/_feishu_routing.py tests/psi_agent/test_feishu_routing.py
git commit -m "feat(feishu): 新建共享路由判定模块

群聊判定此前在三处各写一遍, 群聊类型常量在两处各定义一遍; 判定漂移会让两个
陌生人共享同一份上下文 (隐私事故)。收敛到 psi_agent/_feishu_routing.py 唯一
定义, 放顶层以免在 Gateway 与 Channel 间新造跨组件依赖。

本提交纯新增, 不动任何现有代码。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: SEND 正则与空路径规则收敛到 `channel/_markers.py`

与 Task 1/3 无依赖，可任意时点插入。**这是本计划唯一改变行为的任务**——它关掉一个既有的空路径上传缺口。

背景：`channel/_markers.py:18` 的 `SEND_RE` 用 `(.+?)`（严格，`[SEND:]` 不匹配），`session/history_display.py:74` 的 `_SEND_PATH_RE` 用 `([^\]]*?)`（宽松，匹配空串后由 `if path` 过滤）。Channel 侧**没有**空路径过滤：`SendMarkerScanner.feed()` 直接产出 `FileChunk(path)`，`channel/feishu/client.py:384` 与 `channel/telegram/client.py:125` 都无 guard 地传给 `_send_file`。所以单纯统一到宽松版会让 `[SEND:]` 触发空 source path 上传。方案是宽松正则 + 共享的空路径过滤，两件事一起收敛。

**Files:**
- Modify: `src/psi_agent/channel/_markers.py:18`（正则）与 `:50-63`（`feed()` 改用新迭代器），新增 `iter_send_paths()`
- Modify: `src/psi_agent/session/history_display.py:74`（删 `_SEND_PATH_RE`）与 `:242-251`（`extract_send_paths()` 改用共享迭代器）
- Test: `tests/psi_agent/channel/test__markers.py`（**只增不改**）
- Test: `tests/psi_agent/session/test_history_display.py`（**不改**，验证 `extract_send_paths()` 行为不变）

**Interfaces:**
- Consumes: 无
- Produces: `iter_send_paths(text: str) -> Iterator[tuple[str, int]]` in `psi_agent.channel._markers` — 逐个产出 `(path, match_end)`，`path` 已 strip 且保证非空；`match_end` 是该 match 在 `text` 中的结束偏移，供 `SendMarkerScanner` 维护扫描指针。`SEND_RE` 仍公开可用。

- [ ] **Step 1: 确认现有测试通过（基线）**

Run: `uv run pytest tests/psi_agent/channel/test__markers.py tests/psi_agent/session/test_history_display.py -v`
Expected: PASS。记下用例数。

- [ ] **Step 2: 追加失败测试**

在 `tests/psi_agent/channel/test__markers.py` **末尾追加**（不动前面任何用例）：

```python
def test_scanner_ignores_empty_path_marker():
    """``[SEND:]`` 是模型笔误, 不是传输请求。

    Channel 侧对 FileChunk 无空路径过滤 —— Feishu/Telegram 的 _send_file 会拿
    空 source path 直接发起上传。故空路径必须在解码处就被丢掉。
    """
    scanner = SendMarkerScanner()
    assert scanner.feed("oops [SEND:] nothing here") == []


def test_scanner_ignores_whitespace_only_path_marker():
    scanner = SendMarkerScanner()
    assert scanner.feed("oops [ SEND:   ] nothing here") == []


def test_scanner_still_detects_real_path_after_empty_marker():
    """空标记不得吃掉扫描指针, 后续真实标记仍须被发现。"""
    scanner = SendMarkerScanner()
    assert scanner.feed("[SEND:] then [SEND:/real.py] end") == [FileChunk("/real.py")]


def test_iter_send_paths_yields_path_and_match_end():
    from psi_agent.channel._markers import iter_send_paths

    text = "a [SEND:/x.py] b"
    assert list(iter_send_paths(text)) == [("/x.py", text.index("]") + 1)]


def test_iter_send_paths_skips_empty_and_keeps_order():
    from psi_agent.channel._markers import iter_send_paths

    paths = [path for path, _ in iter_send_paths("[SEND:/a] [SEND:] [SEND:/b]")]
    assert paths == ["/a", "/b"]
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/psi_agent/channel/test__markers.py -v`
Expected: 5 条新用例中，两条 `iter_send_paths` 用例 FAIL（`ImportError: cannot import name 'iter_send_paths'`）；三条 scanner 用例视当前严格正则可能已 PASS（`(.+?)` 本就不匹配空路径）——这是预期的，它们守护的是收敛**之后**不许回退。

- [ ] **Step 4: 改 `channel/_markers.py`**

把第 11-18 行的 import 与正则段替换为：

```python
import re
from collections.abc import Iterator

from loguru import logger

from psi_agent.channel._types import FileChunk, InputChunk, TextChunk

RECV_MARKER = "[RECV:{path}]"
# Matches the space-padded variant ``[ SEND:path ]`` some models emit, and an
# empty ``[SEND:]`` -- the latter is filtered by ``iter_send_paths`` rather than
# by the pattern, so both the Channel decoder and the Gateway projection share
# one rule instead of each encoding it in its own regex.
SEND_RE = re.compile(r"\[\s*SEND\s*:\s*([^\]]*?)\s*\]", re.IGNORECASE)


def iter_send_paths(text: str) -> Iterator[tuple[str, int]]:
    """Yield ``(path, match_end)`` for each ``[SEND:…]`` carrying a real path.

    Empty / whitespace-only paths are skipped: a bare ``[SEND:]`` is a model
    slip, not a transfer request.  Forwarding one would make the Channel attempt
    an upload with an empty source path (neither ``_send_file`` implementation
    guards against it), and would make the Gateway projection emit a blank entry.

    ``match_end`` is the offset just past the marker, so a streaming caller can
    advance its scan pointer without re-deriving the match.
    """
    for match in SEND_RE.finditer(text):
        path = match.group(1).strip()
        if path:
            yield path, match.end()
```

把 `SendMarkerScanner.feed()`（原第 50-63 行）替换为：

```python
    def feed(self, text: str) -> list[FileChunk]:
        """Append a new content fragment, return newly-detected ``FileChunk``s."""
        out: list[FileChunk] = []
        self._full += text
        base = self._scan_ptr
        new = self._full[base:]
        for path, match_end in iter_send_paths(new):
            if path not in self._emitted:
                logger.debug(f"[SEND] detected → FileChunk({path})")
                out.append(FileChunk(path))
                self._emitted.add(path)
            self._scan_ptr = base + match_end
        return out
```

扫描指针语义不变：仍是 `base + match_end`（`test_scanner_third_marker_after_trailing_text_regression` 守护这一点）。唯一差别是空路径 match 不再推进指针——它们本就不该被当成已处理的标记。

- [ ] **Step 5: 跑 markers 测试确认通过**

Run: `uv run pytest tests/psi_agent/channel/test__markers.py -v`
Expected: PASS，含原有 12 条与新增 5 条。

- [ ] **Step 6: 改 `session/history_display.py`**

第 72-74 行：删掉 `_SEND_PATH_RE` 定义，保留 `_TRANSFER_MARKER_RE`（它含 RECV、服务文本清理，与协议解码是两件事）：

```python
# Tolerates the space-padded variant ``[ SEND:path ]`` emitted by some models.
_TRANSFER_MARKER_RE = re.compile(r"\[\s*(?:SEND|RECV)\s*:\s*[^\]]*?\]", re.IGNORECASE)
```

在文件的 import 段加入（跟随该文件既有 import 风格放在其他 `psi_agent` 导入旁）：

```python
from psi_agent.channel._markers import iter_send_paths
```

把 `extract_send_paths()`（原第 242-251 行）替换为：

```python
def extract_send_paths(text: str) -> list[str]:
    """Return ``[SEND:…]`` paths in order (stripped); empty / whitespace skipped.

    Decoding lives in ``channel._markers`` so the Channel transport and this
    Gateway projection cannot drift apart on what counts as a path.
    """
    if not isinstance(text, str) or not text:
        return []
    return [path for path, _ in iter_send_paths(text)]
```

- [ ] **Step 7: 跑 history_display 测试确认零回归**

Run: `uv run pytest tests/psi_agent/session/test_history_display.py -v`
Expected: PASS，用例数与 Step 1 一致，**一条都不改**。`extract_send_paths()` 的行为完全不变（原本就是宽松正则 + `if path`）。

- [ ] **Step 8: 跑全量测试**

Run: `uv run pytest`
Expected: 全绿。

- [ ] **Step 9: Lint 与类型检查**

Run: `uv run ruff check src/psi_agent/channel/_markers.py src/psi_agent/session/history_display.py tests/psi_agent/channel/test__markers.py`
Expected: 无告警。若 `re` 在 `history_display.py` 仍被 `_TRANSFER_MARKER_RE` 与 `strip_transfer_markers` 的 `re.sub` 使用，import 保留即可。

Run: `uv run ty check`
Expected: 通过

- [ ] **Step 10: Commit**

```bash
git add src/psi_agent/channel/_markers.py src/psi_agent/session/history_display.py tests/psi_agent/channel/test__markers.py
git commit -m "fix(channel): SEND 正则收敛到一处, 并堵住空路径上传缺口

正则此前两处写法不同: _markers.py 用 (.+?) 严格版, history_display.py 用
([^]]*?) 宽松版 + if path 过滤。追查发现 Channel 侧对 FileChunk **没有**空
路径过滤 —— Feishu 与 Telegram 的 _send_file 都会拿空 source path 直接发起
上传, 当前正则分歧恰好在阻止这件事。

故不是简单统一正则, 而是宽松正则 + 共享的 iter_send_paths() 空路径过滤:
正则一处定义, 空路径规则一处定义, 且关掉了这个既有缺口。

扫描指针语义不变 (base + match_end)。_TRANSFER_MARKER_RE 留在
history_display.py —— 文本清理与协议解码是两件事。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Session 组件字面量与 SSE 解析替换

依赖 Task 2（重导出必须先就位）。

**Files:**
- Modify: `src/psi_agent/session/ai_client.py:55,61-63,80`
- Modify: `src/psi_agent/session/agent.py:514,518,612,638,709`
- Modify: `src/psi_agent/session/channel_adapter.py:112`
- Test: `tests/psi_agent/session/test_ai_client.py`、`test_agent.py`、`test_channel_adapter.py`、`test_compaction_signal.py`（**全部不改**）

**Interfaces:**
- Consumes: Task 1 的 `FINISH_REASON_ERROR` / `FINISH_REASON_STOP` / `FINISH_REASON_TOOL_CALLS` / `FINISH_REASON_COMPACTION_NEEDED` / `parse_sse_data` / `SSE_DONE`
- Produces: 无新接口，纯替换

- [ ] **Step 1: 确认基线**

Run: `uv run pytest tests/psi_agent/session/ -v`
Expected: PASS。记下总用例数。

- [ ] **Step 2: 改 `session/ai_client.py`**

在 import 段加入：

```python
from psi_agent.protocol import (
    FINISH_REASON_ERROR,
    SSE_DONE,
    parse_sse_data,
)
```

第 55 行：

```python
                yield AiDelta(finish_reason=FINISH_REASON_ERROR, content=f"[AI Error: {resp.status}]")
```

第 61-65 行（整段替换，注意 `[DONE]` 这里是 `continue` 不是 `return`）：

```python
                data_str = parse_sse_data(line)
                if data_str is None:
                    continue
                if data_str == SSE_DONE:
                    continue
```

第 79-81 行：

```python
                    yield AiDelta(
                        finish_reason=FINISH_REASON_ERROR,
                        content=f"[AI Error: expected 1 choice, got {len(choices_data)}]",
                    )
```

注意：原第 60 行 `line = raw_line.decode().strip()` 保留——`parse_sse_data` 不做 decode/strip。

- [ ] **Step 3: 跑 ai_client 测试**

Run: `uv run pytest tests/psi_agent/session/test_ai_client.py tests/psi_agent/session/test_compaction_signal.py -v`
Expected: PASS，一条未改。

- [ ] **Step 4: 改 `session/agent.py`**

在 import 段加入：

```python
from psi_agent.protocol import (
    FINISH_REASON_COMPACTION_NEEDED,
    FINISH_REASON_ERROR,
    FINISH_REASON_STOP,
    FINISH_REASON_TOOL_CALLS,
)
```

第 514 行：`if finish_reason == FINISH_REASON_ERROR:`

第 518 行：`if finish_reason == FINISH_REASON_TOOL_CALLS:`

第 612 行：`if finish_reason == FINISH_REASON_STOP:`

第 638 行：

```python
                    if finish_reason not in (
                        FINISH_REASON_ERROR,
                        FINISH_REASON_STOP,
                        FINISH_REASON_TOOL_CALLS,
                        FINISH_REASON_COMPACTION_NEEDED,
                    ):
```

第 709 行：`if delta.finish_reason == FINISH_REASON_ERROR:`

**不要**把第 638 行改成 `is_terminal_finish()`——它的语义是"这个 finish_reason 是否为本层已知的四个值之一"，与"是否终止流"不同。未知值走的是 WARNING + 保存内容 + 停止的分支，这条判断必须保留穷举形式。

- [ ] **Step 5: 改 `session/channel_adapter.py`**

在 import 段的 `from psi_agent.session.protocol import ...` 之外，加入：

```python
from psi_agent.protocol import FINISH_REASON_ERROR
```

第 112 行：`finish_reason=FINISH_REASON_ERROR,`

`_write_error` 继续用 `ChatCompletionChunk` 类型构造，**不**改成 `make_error_chunk()`：它已经在用共享类型，且需要 `.to_sse()` 的字节输出；换成 dict 反而要多一次 `json.dumps`。`make_error_chunk()` 服务的是 ai/router 两处手写 dict 的场景。

- [ ] **Step 6: 跑全量 session 测试**

Run: `uv run pytest tests/psi_agent/session/ -v`
Expected: PASS，用例数与 Step 1 一致。

- [ ] **Step 7: 跑全量测试 + Lint + 类型检查**

Run: `uv run pytest`
Expected: 全绿

Run: `uv run ruff check src/psi_agent/session/`
Expected: 无告警

Run: `uv run ty check`
Expected: 通过

- [ ] **Step 8: Commit**

```bash
git add src/psi_agent/session/ai_client.py src/psi_agent/session/agent.py src/psi_agent/session/channel_adapter.py
git commit -m "refactor(session): finish_reason 字面量改常量, SSE 解析改共享函数

ai_client.py 的 line[6:] 换成 parse_sse_data() —— 顺带修正 data: 后空格必需
的规范偏离。agent.py 第 638 行的四值穷举保留穷举形式 (它判的是\"本层已知值\",
不是\"是否终止\"), 只把字面量换成常量。

channel_adapter 的 _write_error 继续用 ChatCompletionChunk 类型构造, 不改用
make_error_chunk() —— 它已在用共享类型且需要 to_sse() 字节输出。

测试一行未改并全部通过。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: AI 组件构造函数替换

依赖 Task 1。

**Files:**
- Modify: `src/psi_agent/ai/server.py:102-115`（compaction 信号）与 `:124-132`（error chunk）
- Test: `tests/psi_agent/ai/test_server.py`、`test_compaction.py`（**不改**）

**Interfaces:**
- Consumes: Task 1 的 `make_compaction_signal` / `make_error_chunk`
- Produces: 无新接口

- [ ] **Step 1: 确认基线**

Run: `uv run pytest tests/psi_agent/ai/ -v`
Expected: PASS。记下用例数。

- [ ] **Step 2: 改 `ai/server.py`**

在 import 段加入（注意本文件第 9 行已从 `any_llm.api` 导入同名的 `ChatCompletionChunk`——**只导入这两个函数**，不要导入本仓的 `ChatCompletionChunk`，否则命名冲突）：

```python
from psi_agent.protocol import make_compaction_signal, make_error_chunk
```

第 102-115 行替换为：

```python
        if compaction_needed:
            signal = json.dumps(
                make_compaction_signal(
                    prompt_tokens=compaction_usage.get("prompt_tokens", 0),
                    threshold=max_context_tokens,
                )
            )
            logger.debug(f"SSE compaction signal: {signal[:500]}")
            await response.write(f"data: {signal}\n\n".encode())
```

第 124-132 行替换为：

```python
        err_chunk = json.dumps(make_error_chunk(f"[Upstream Error]: {e}"))
        logger.debug(f"SSE error chunk: {err_chunk[:1000]}")
        try:
            await response.write(f"data: {err_chunk}\n\n".encode())
        except Exception:
            logger.warning("Failed to send upstream error chunk to client")
```

两处的 `logger.debug` 措辞与截断长度保持原样。

- [ ] **Step 3: 跑 AI 测试**

Run: `uv run pytest tests/psi_agent/ai/ -v`
Expected: PASS，用例数与 Step 1 一致。`test_compaction.py` 断言 `psi_compaction` 的形状，是这次替换的直接验证。

- [ ] **Step 4: 跑相关集成测试**

Run: `uv run pytest tests/integration/test_ai_error_handling.py -v`
Expected: PASS（`:246` 断言 content 含 "Upstream Error"）

- [ ] **Step 5: Lint + 类型检查**

Run: `uv run ruff check src/psi_agent/ai/server.py`
Expected: 无告警

Run: `uv run ty check`
Expected: 通过

- [ ] **Step 6: Commit**

```bash
git add src/psi_agent/ai/server.py
git commit -m "refactor(ai): error chunk 与 compaction 信号改用共享构造函数

两处手写 dict 换成 make_error_chunk() / make_compaction_signal()。前缀
[Upstream Error]: 仍由本层拼好后传入 —— 三个 producer 前缀各不相同且各有
测试断言, 构造函数不参与拼接。

未导入本仓 ChatCompletionChunk: 本文件第 9 行已从 any_llm.api 导入同名类型。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Router 组件行为规则与字面量替换

依赖 Task 1。**这是收益最大的一个任务**——"compaction 是辅助帧、不得覆盖终止帧"这条规则在 Router 里被独立实现了 5 处（onboarding 文档说 4 处，`fallback/strategy.py` 那处是文档写完后新增的）。

**Files:**
- Modify: `src/psi_agent/router/server.py:145-155`
- Modify: `src/psi_agent/router/client.py:63-72,126-127,182-184,222-225`
- Modify: `src/psi_agent/router/routing/strategy.py:85,95`
- Modify: `src/psi_agent/router/routing/selector.py:45`
- Modify: `src/psi_agent/router/aggregation/strategy.py:97,129`
- Modify: `src/psi_agent/router/fallback/strategy.py:99,144`
- Test: `tests/psi_agent/router/`（全部**不改**）

**Interfaces:**
- Consumes: Task 1 的 `make_error_chunk` / `parse_sse_data` / `is_terminal_finish` / `is_auxiliary_finish` / `FINISH_REASON_*`
- Produces: 无新接口

- [ ] **Step 1: 确认基线**

Run: `uv run pytest tests/psi_agent/router/ -v`
Expected: PASS。记下总用例数。

- [ ] **Step 2: 改 `router/server.py`**

import 段加入：

```python
from psi_agent.protocol import make_error_chunk
```

第 145-155 行替换为：

```python
async def _write_sse_error(*, response: web.StreamResponse, error: Exception) -> None:
    event = make_error_chunk(f"[Router Error]: {error}")
    try:
        await _write_event(response=response, event=event)
    except Exception as write_error:
        logger.warning(f"Failed to send Router SSE error: {write_error!r}")
```

- [ ] **Step 3: 改 `router/client.py`**

import 段加入：

```python
from psi_agent.protocol import (
    FINISH_REASON_ERROR,
    FINISH_REASON_TOOL_CALLS,
    SSE_DONE,
    is_auxiliary_finish,
    is_terminal_finish,
    parse_sse_data,
)
```

第 63-72 行替换为：

```python
                current_finish = choice.get("finish_reason")
                # Compaction is an auxiliary signal sent after the model's
                # actual terminal frame. It must not replace stop/tool_calls.
                if is_auxiliary_finish(current_finish):
                    continue
                if isinstance(current_finish, str):
                    if current_finish == FINISH_REASON_ERROR:
                        detail = "".join(content_parts) or "unknown upstream error"
                        raise RouterUpstreamError(f"Upstream {socket!r} reported an error: {detail}")
                    finish_reason = current_finish
```

第 125-128 行（`data:` 切片，注意本文件是**多行 data 累积**，原本就用 `line[5:].lstrip()`，是全仓唯一写对的一处；改用共享函数以统一来源）：

```python
                line = raw_line.decode(errors="replace").rstrip("\r\n")
                if line:
                    payload_part = parse_sse_data(line)
                    if payload_part is not None:
                        data_lines.append(payload_part)
                    continue
```

第 115 行与第 133 行的 `"[DONE]"` 字面量改为 `SSE_DONE`：

```python
                        if payload != SSE_DONE:
```

```python
                if payload == SSE_DONE:
                    break
```

第 182-184 行替换为：

```python
    @staticmethod
    def _is_completion_finish(value: object) -> bool:
        return isinstance(value, str) and is_terminal_finish(value)
```

保留 `isinstance` 检查：该方法接收 `object`，`is_terminal_finish` 的签名是 `str | None`。

第 224 行：

```python
        if finish_reason == FINISH_REASON_TOOL_CALLS and not tool_calls:
```

- [ ] **Step 4: 跑 client 测试**

Run: `uv run pytest tests/psi_agent/router/test_client.py tests/psi_agent/router/test_server.py -v`
Expected: PASS，一条未改。

- [ ] **Step 5: 改 `router/routing/strategy.py`**

import 段加入：

```python
from psi_agent.protocol import FINISH_REASON_TOOL_CALLS, is_terminal_finish
```

第 85 行：

```python
                    if isinstance(current_finish, str) and is_terminal_finish(current_finish):
```

第 95 行：

```python
            if scope is not None and (not completed or finish_reason != FINISH_REASON_TOOL_CALLS):
```

- [ ] **Step 6: 改 `router/routing/selector.py`**

import 段加入：

```python
from psi_agent.protocol import FINISH_REASON_STOP
```

第 45 行：

```python
        if result.finish_reason != FINISH_REASON_STOP:
```

- [ ] **Step 7: 改 `router/aggregation/strategy.py`**

import 段加入：

```python
from psi_agent.protocol import FINISH_REASON_STOP, FINISH_REASON_TOOL_CALLS, is_terminal_finish
```

第 96-99 行：

```python
        if self.config.require_all_targets and any(
            item.status != "success" or item.finish_reason not in {FINISH_REASON_STOP, FINISH_REASON_TOOL_CALLS}
            for item in feedback
        ):
```

第 129 行：

```python
                    if isinstance(current_finish, str) and is_terminal_finish(current_finish):
```

- [ ] **Step 8: 改 `router/fallback/strategy.py`**

import 段加入：

```python
from psi_agent.protocol import (
    FINISH_REASON_ERROR,
    FINISH_REASON_TOOL_CALLS,
    is_auxiliary_finish,
)
```

第 99 行：

```python
        keeps_sticky = result.completion.finish_reason == FINISH_REASON_TOOL_CALLS
```

第 144 行（这是 onboarding 文档漏掉的第 5 处）：

```python
        if not finish_reason or finish_reason == FINISH_REASON_ERROR or is_auxiliary_finish(finish_reason):
            return False
```

语义与原 `finish_reason in {"error", "compaction_needed"}` 完全等价：error 不可用、辅助帧不可用。拆成两个判断是因为二者理由不同——error 是失败，compaction 是"这不是一个完整回答的终止帧"。

- [ ] **Step 9: 跑全量 router 测试**

Run: `uv run pytest tests/psi_agent/router/ -v`
Expected: PASS，用例数与 Step 1 一致。

- [ ] **Step 10: 跑相关集成测试**

Run: `uv run pytest tests/integration/test_fallback_router_composition.py tests/integration/test_serial_multi_ai_router.py -v`
Expected: PASS

- [ ] **Step 11: 全量 + Lint + 类型检查**

Run: `uv run pytest`
Expected: 全绿

Run: `uv run ruff check src/psi_agent/router/`
Expected: 无告警

Run: `uv run ty check`
Expected: 通过

- [ ] **Step 12: Commit**

```bash
git add src/psi_agent/router/
git commit -m "refactor(router): 辅助帧规则收敛到 is_terminal_finish, 字面量改常量

\"compaction 是辅助帧、不得覆盖终止帧\" 这条规则此前在 Router 里被独立实现了
5 处 (client.py 两处、routing/strategy.py、aggregation/strategy.py、
fallback/strategy.py)。onboarding 文档记为 4 处 —— fallback 那处是文档写完后
随串行 Fallback 策略新增的, 恰好印证不收敛就会持续增殖。

将来新增辅助帧类型只需改 protocol.py 的 AUXILIARY_FINISH_REASONS。

error chunk 手写 dict 换 make_error_chunk(); data: 切片统一走
parse_sse_data() (本文件原本是全仓唯一写对的一处, 改为共享来源)。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Channel `_stream.py` SSE 解析与字面量替换

依赖 Task 1。

**Files:**
- Modify: `src/psi_agent/channel/_stream.py:33-40,68`
- Test: `tests/psi_agent/channel/test__stream.py`（**不改**——`:109` 断言 error chunk 形状）

**Interfaces:**
- Consumes: Task 1 的 `parse_sse_data` / `SSE_DONE` / `FINISH_REASON_ERROR`
- Produces: 无新接口

- [ ] **Step 1: 确认基线**

Run: `uv run pytest tests/psi_agent/channel/test__stream.py -v`
Expected: PASS。记下用例数。

- [ ] **Step 2: 改 `channel/_stream.py`**

import 段加入：

```python
from psi_agent.protocol import FINISH_REASON_ERROR, SSE_DONE, parse_sse_data
```

第 33-40 行替换为（注意此处 `[DONE]` 是 `return`，与 session/ai_client 的 `continue` 不同——这正是 `parse_sse_data` 不接管 `[DONE]` 语义的原因）：

```python
    async for raw_line in lines:
        line = raw_line.decode().strip()
        data_str = parse_sse_data(line)
        if data_str is None:
            continue
        if data_str == SSE_DONE:
            logger.debug("SSE stream ended [DONE]")
            return
```

第 68 行：

```python
        if choice.get("finish_reason") == FINISH_REASON_ERROR:
```

原第 35 行的 `if not line or ...` 判断被 `parse_sse_data` 吸收（空行不以 `data:` 开头，返回 `None`）。第 45 行 `logger.warning(f"skip malformed SSE: {line[:1000]!r}")` 保留——它记的是整行，不是 payload。

- [ ] **Step 3: 跑 channel 测试**

Run: `uv run pytest tests/psi_agent/channel/ -v`
Expected: PASS，用例数与 Step 1 一致。

- [ ] **Step 4: 跑相关集成测试**

Run: `uv run pytest tests/integration/test_channel_error.py tests/integration/test_channel_repl_cli.py -v`
Expected: PASS

- [ ] **Step 5: Lint + 类型检查**

Run: `uv run ruff check src/psi_agent/channel/_stream.py`
Expected: 无告警

Run: `uv run ty check`
Expected: 通过

- [ ] **Step 6: Commit**

```bash
git add src/psi_agent/channel/_stream.py
git commit -m "refactor(channel): SSE 解析改用 parse_sse_data

line[6:] 换成共享函数, 顺带修正 data: 后空格必需的规范偏离。此处 [DONE] 的
反应是 return (session/ai_client 是 continue, gateway 两处是 break) —— 这正
是 parse_sse_data 只管切片、不接管 [DONE] 语义的原因。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Gateway SSE 解析替换

依赖 Task 1。

**Files:**
- Modify: `src/psi_agent/gateway/_title_manager.py:59-64`
- Modify: `src/psi_agent/gateway/_summary_manager.py:74-79`
- Test: `tests/psi_agent/gateway/`（**不改**）

**Interfaces:**
- Consumes: Task 1 的 `parse_sse_data` / `SSE_DONE`
- Produces: 无新接口

- [ ] **Step 1: 确认基线**

Run: `uv run pytest tests/psi_agent/gateway/ -v`
Expected: PASS。记下总用例数。

- [ ] **Step 2: 改 `gateway/_title_manager.py`**

import 段加入：

```python
from psi_agent.protocol import SSE_DONE, parse_sse_data
```

第 59-64 行替换为（注意此处 `[DONE]` 是 `break`——它跳出的是内层 `while b"\n" in buf` 循环，语义保持不变）：

```python
                        line = line_bytes.decode().strip()
                        data_str = parse_sse_data(line)
                        if data_str is None:
                            continue
                        if data_str == SSE_DONE:
                            break
```

- [ ] **Step 3: 改 `gateway/_summary_manager.py`**

import 段加入：

```python
from psi_agent.protocol import SSE_DONE, parse_sse_data
```

第 74-79 行替换为：

```python
                        line = line_bytes.decode().strip()
                        data_str = parse_sse_data(line)
                        if data_str is None:
                            continue
                        if data_str == SSE_DONE:
                            break
```

- [ ] **Step 4: 跑 gateway 测试**

Run: `uv run pytest tests/psi_agent/gateway/ -v`
Expected: PASS，用例数与 Step 1 一致。

- [ ] **Step 5: Lint + 类型检查**

Run: `uv run ruff check src/psi_agent/gateway/_title_manager.py src/psi_agent/gateway/_summary_manager.py`
Expected: 无告警

Run: `uv run ty check`
Expected: 通过

- [ ] **Step 6: Commit**

```bash
git add src/psi_agent/gateway/_title_manager.py src/psi_agent/gateway/_summary_manager.py
git commit -m "refactor(gateway): 标题/摘要 SSE 解析改用 parse_sse_data

两处 line[6:] 换成共享函数, 顺带修正 data: 后空格必需的规范偏离。[DONE] 的
break 语义 (跳出内层 buf 分割循环) 保持不变。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Gateway 飞书判定改用共享模块

依赖 Task 3。

**Files:**
- Modify: `src/psi_agent/gateway/_feishu_manager.py:32`（删本地常量）、`:69-84`（删 `_is_group`，`_route_key` 改用共享函数）
- Test: `tests/psi_agent/gateway/test_feishu_manager.py`（**不改**）

**Interfaces:**
- Consumes: Task 3 的 `is_group_chat` / `route_key`
- Produces: 无新接口

- [ ] **Step 1: 确认基线**

Run: `uv run pytest tests/psi_agent/gateway/test_feishu_manager.py -v`
Expected: PASS。记下用例数。

- [ ] **Step 2: 检查测试是否直接调用 `_is_group`**

Run: `grep -n "_is_group\|_route_key" tests/psi_agent/gateway/test_feishu_manager.py`

若测试直接调用 `FeishuManager._is_group(...)`，**保留该方法作为一行委托**（`return is_group_chat(chat_id, chat_type)`），不删除——测试不改是硬约束。若无调用，按 Step 3 删除。

- [ ] **Step 3: 改 `gateway/_feishu_manager.py`**

import 段加入：

```python
from psi_agent._feishu_routing import is_group_chat, route_key
```

删除第 32 行 `_GROUP_CHAT_TYPES = frozenset({"group", "topic"})`。

第 69-84 行：删除 `_is_group()`（或按 Step 2 保留为委托），`_route_key()` 替换为：

```python
    def _route_key(self, open_id: str, chat_id: str, chat_type: str) -> str:
        """路由表/派生用的键 —— 判定与 Channel 侧共用 ``psi_agent._feishu_routing``。"""
        return route_key(open_id, chat_id, chat_type)
```

`_route_key` 内如另有本类特有逻辑（读第 77-84 行确认），保留那部分，只把群聊判定与键拼接换成共享函数。

第 86-96 行的 `_session_id()` 与 `_workspace_for()` **不动**：`-` → `_` 转义只服务 Gateway 侧的 session_id / workspace 派生，不上提。

- [ ] **Step 4: 检查 `is_group_chat` 的其他调用点**

Run: `grep -n "_is_group\|_GROUP_CHAT_TYPES" src/psi_agent/gateway/_feishu_manager.py`
Expected: 无残留（或仅剩 Step 2 保留的委托方法）

- [ ] **Step 5: 跑 gateway 测试**

Run: `uv run pytest tests/psi_agent/gateway/ -v`
Expected: PASS，用例数与 Step 1 一致。

- [ ] **Step 6: Lint + 类型检查**

Run: `uv run ruff check src/psi_agent/gateway/_feishu_manager.py`
Expected: 无告警（`re` 与 `_SOCKET_UNSAFE` 仍被 `_sanitize_open_id` 使用，保留）

Run: `uv run ty check`
Expected: 通过

- [ ] **Step 7: Commit**

```bash
git add src/psi_agent/gateway/_feishu_manager.py
git commit -m "refactor(gateway): 飞书群聊判定改用共享模块

删除本地 _GROUP_CHAT_TYPES 与 _is_group, 改用 psi_agent._feishu_routing。
session_id / workspace 的 - -> _ 转义留在本层 —— 它只服务 Gateway 侧派生,
Channel 不派生这些。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Channel 飞书判定改用共享模块

依赖 Task 3。

**Files:**
- Modify: `src/psi_agent/channel/feishu/client.py:89`（删本地常量）、`:113-120`（`_cache_key` 改委托）、`:832`（内联判定改函数调用）
- Test: `tests/psi_agent/channel/feishu/test_feishu.py`（**不改**）

**Interfaces:**
- Consumes: Task 3 的 `is_group_chat` / `route_key`
- Produces: 无新接口

- [ ] **Step 1: 确认基线**

Run: `uv run pytest tests/psi_agent/channel/feishu/ -v`
Expected: PASS。记下总用例数。

- [ ] **Step 2: 检查测试是否直接调用 `_cache_key`**

Run: `grep -n "_cache_key\|_GROUP_CHAT_TYPES" tests/psi_agent/channel/feishu/test_feishu.py`

若测试直接调用，保留 `_cache_key` 作为一行委托。

- [ ] **Step 3: 改 `channel/feishu/client.py`**

import 段加入：

```python
from psi_agent._feishu_routing import is_group_chat, route_key
```

删除第 89 行 `_GROUP_CHAT_TYPES = frozenset({"group", "topic"})`。

第 113-120 行替换为：

```python
    @staticmethod
    def _cache_key(open_id: str, chat_id: str, chat_type: str) -> str:
        """与 Gateway ``FeishuManager`` 同款判定 —— 共用 ``psi_agent._feishu_routing``。

        群聊按 chat_id (同群不同发言者须命中同一条缓存, 否则每人各打一次 Gateway),
        其余按 open_id。
        """
        return route_key(open_id, chat_id, chat_type)
```

第 832 行替换为：

```python
            is_group = is_group_chat(chat_id, chat_type)
```

- [ ] **Step 4: 确认无残留**

Run: `grep -n "_GROUP_CHAT_TYPES" src/psi_agent/`
Expected: 无输出（两处本地定义都已删除）

- [ ] **Step 5: 跑 channel 测试**

Run: `uv run pytest tests/psi_agent/channel/ -v`
Expected: PASS，用例数与 Step 1 一致。

- [ ] **Step 6: 跑飞书相关集成测试**

Run: `uv run pytest tests/integration/ -k feishu -v`
Expected: PASS（若无匹配用例，输出 "no tests ran"，属正常）

- [ ] **Step 7: Lint + 类型检查**

Run: `uv run ruff check src/psi_agent/channel/feishu/client.py`
Expected: 无告警

Run: `uv run ty check`
Expected: 通过

- [ ] **Step 8: Commit**

```bash
git add src/psi_agent/channel/feishu/client.py
git commit -m "refactor(channel/feishu): 群聊判定改用共享模块

删除本地 _GROUP_CHAT_TYPES, _cache_key 改为委托 route_key, 第 832 行内联判定
改为 is_group_chat。至此三处判定与两处常量全部收敛到
psi_agent/_feishu_routing.py 一处。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: `session/__init__.py` 补齐 Gateway 依赖符号的正式导出

独立任务，与其他任务无依赖。

背景：`gateway/_history_manager.py:13` 从 `session.history_display` 导入 6 个符号，`gateway/_scheduler_manager.py:40` 与 `_session_manager.py:23` 从 `session.schedule_registry` 导入 `ACTIVATE_ALL`，而 `session/__init__.py` 不导出这些——Gateway 绕过了 Session 的公开门面。

**核对结论**：onboarding 文档称此依赖"双方文档均记录并标刻意为之"，实际核对 `session/AGENTS.md:356` 与 `gateway/AGENTS.md:419` 只描述了**行为**（Gateway 投影剥标记、用了 `is_displayable_chat_message`），**没有一处说明"绕过公开门面导入内部模块"是刻意选择**。所以这条依赖比文档声称的更缺乏交待，补导出的同时要在文档写明（Task 13）。

**Files:**
- Modify: `src/psi_agent/session/__init__.py`（加 `__all__` 与重导出）
- Test: `tests/psi_agent/session/test_session.py`（**不改**）；新增一条导出面测试

**Interfaces:**
- Consumes: 无
- Produces: `psi_agent.session` 顶层导出 `Session`、`SessionAgent`、`ACTIVATE_ALL`、`KIND_CHAT`、`extract_send_paths`、`is_displayable_chat_message`、`message_kind`、`strip_transfer_markers`、`wire_role`。**Gateway 的现有 import 路径保持有效**（本任务只增加正式通道，不强制迁移）。

- [ ] **Step 1: 写失败测试**

在 `tests/psi_agent/session/test_session.py` **末尾追加**：

```python
def test_public_exports_cover_gateway_dependencies():
    """Gateway 依赖的符号必须走 Session 的公开门面。

    gateway/_history_manager.py 与 _scheduler_manager.py 此前直接从
    session.history_display / session.schedule_registry 导入 —— 依赖是刻意的,
    通道却是非正式的。这条测试钉住正式导出面。
    """
    import psi_agent.session as session_pkg

    expected = {
        "ACTIVATE_ALL",
        "KIND_CHAT",
        "Session",
        "SessionAgent",
        "extract_send_paths",
        "is_displayable_chat_message",
        "message_kind",
        "strip_transfer_markers",
        "wire_role",
    }
    assert expected <= set(session_pkg.__all__)
    for name in expected:
        assert hasattr(session_pkg, name), f"{name} declared in __all__ but not importable"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/psi_agent/session/test_session.py::test_public_exports_cover_gateway_dependencies -v`
Expected: FAIL — `AttributeError: module 'psi_agent.session' has no attribute '__all__'`

- [ ] **Step 3: 改 `session/__init__.py`**

在现有 import 段（第 10-14 行）之后加入：

```python
from psi_agent.session.history_display import (
    KIND_CHAT,
    extract_send_paths,
    is_displayable_chat_message,
    message_kind,
    strip_transfer_markers,
    wire_role,
)
```

并在 import 段之后、`@dataclass` 之前加入：

```python
__all__ = [
    "ACTIVATE_ALL",
    "KIND_CHAT",
    "Session",
    "SessionAgent",
    "extract_send_paths",
    "is_displayable_chat_message",
    "message_kind",
    "strip_transfer_markers",
    "wire_role",
]
"""Session 的公开门面。

``history_display`` 与 ``schedule_registry`` 的这几个符号由 Gateway 依赖
(``_history_manager`` 做 ``/history`` 投影, ``_scheduler_manager`` /
``_session_manager`` 用 ``ACTIVATE_ALL`` 判定调度会话)。依赖是刻意的 —— Gateway
的展示投影必须与 Session 的落盘语义逐字一致, 否则同一条历史两处渲染会分叉 ——
所以这里给它一个正式通道, 而不是让 Gateway 继续按内部模块路径导入。
"""
```

注意：`ACTIVATE_ALL` 已在第 13 行导入（`from psi_agent.session.schedule_registry import ACTIVATE_ALL`），无需重复导入。`SessionAgent` 已在第 12 行导入。

若 ruff 报 `ACTIVATE_ALL` / `SessionAgent` / 新增符号"imported but unused"，这是 `__all__` 声明后应当消失的告警；若仍报，确认符号名拼写与 `__all__` 一致。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/psi_agent/session/test_session.py -v`
Expected: PASS，含新增用例。

- [ ] **Step 5: 跑全量测试**

Run: `uv run pytest`
Expected: 全绿。Gateway 的现有 import 路径未变，应无影响。

- [ ] **Step 6: Lint + 类型检查**

Run: `uv run ruff check src/psi_agent/session/__init__.py`
Expected: 无告警

Run: `uv run ty check`
Expected: 通过

- [ ] **Step 7: Commit**

```bash
git add src/psi_agent/session/__init__.py tests/psi_agent/session/test_session.py
git commit -m "feat(session): 补齐 Gateway 依赖符号的正式导出

Gateway 此前从 session.history_display / session.schedule_registry 按内部
模块路径导入 7 个符号, 而 session/__init__.py 不导出它们 —— 依赖是刻意的
(展示投影必须与落盘语义逐字一致), 通道却是非正式的。

补 __all__ 给这条依赖一个正式门面。Gateway 现有 import 路径保持有效, 不强制
迁移。新增一条导出面测试钉住它。

核对发现: onboarding 文档称\"双方文档均记录并标刻意为之\", 实际两侧文档只描述
了行为, 没有一处说明绕过公开门面是刻意选择 —— 文档侧一并补写。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: 文档同步（根 + 四层子模块）

依赖 Task 1-12 全部完成。原则：**信息只归属一层**——根文档说协议归属在哪，子文档只引用不重复定义。

**Files:**
- Modify: `AGENTS.md`（§代码结构 `:71-93`、§核心通信协议 `:147-183`、§关键注意事项第 19 条 `:240`、§改动后自检清单 `:340`）
- Modify: `src/psi_agent/ai/AGENTS.md:69,72-82`
- Modify: `src/psi_agent/session/AGENTS.md:348-356`
- Modify: `src/psi_agent/router/AGENTS.md:61-62`
- Modify: `src/psi_agent/channel/AGENTS.md:9,39-40`
- Modify: `src/psi_agent/gateway/AGENTS.md:355-356,419`

**Interfaces:**
- Consumes: Task 1-12 的全部产出（文档引用的函数名必须与实现一致）
- Produces: 无代码接口

- [ ] **Step 1: 根 AGENTS.md §代码结构**

在 `_appdata.py` 那行（`:74`）之后插入两行，缩进与相邻行对齐：

```
    ├── protocol.py             # 跨组件 SSE 协议归属（线格式类型 + finish_reason 常量 + 辅助帧/终止帧规则）
    ├── _feishu_routing.py      # 飞书群聊/私聊判定与路由键（Gateway↔Channel 共享）
```

把 `:93` 的 session `protocol.py` 那行改为：

```
    │   ├── protocol.py             # Session 专属类型（含 `AgentRunResult`）+ 重导出 `psi_agent.protocol` 共享定义
```

- [ ] **Step 2: 根 AGENTS.md §核心通信协议 —— 新增「协议归属」小节**

在 `:183`（compaction 信号那段结束）之后、`## 日志约定` 之前插入：

```markdown
### 协议归属

上述格式的**唯一定义处**是 `psi_agent/protocol.py`（与五个组件平级，因为它描述的是层与层之间的约定）：

| 层次 | 内容 |
|------|------|
| 格式层 | `DeltaMessage` / `StreamChoice` / `ChatCompletionChunk`；`make_error_chunk()` / `make_compaction_signal()` / `parse_sse_data()` |
| 常量层 | `FINISH_REASON_STOP` / `_TOOL_CALLS` / `_ERROR` / `_COMPACTION_NEEDED`、`REASONING_KIND_*`、`SSE_DONE` |
| 语义层 | `AUXILIARY_FINISH_REASONS` frozenset、`is_terminal_finish()` / `is_auxiliary_finish()` |

三条规则：

1. **新增或改动 `finish_reason` 值，只改这一个文件。** 辅助帧（不终止流、不得覆盖终止帧）加进 `AUXILIARY_FINISH_REASONS` 即全局生效——此前这条规则在 Router 里被独立实现了 5 次，每次都是人读文档后手写的 `if`。
2. **未知 `finish_reason` 视为终止**。`is_terminal_finish()` 只把辅助帧集合排除在外；`None` 既不终止也不辅助（流尚未报告结束）。
3. **解析 `data:` 行一律用 `parse_sse_data()`**。SSE 规范中 `data:` 后的空格是**可选的**，不要写 `line[6:]` 或 `startswith("data: ")`——曾有四处这么写，无空格的帧会被整帧静默丢弃。

`session/protocol.py` 重导出这些共享定义（保持既有 import 路径有效），并额外持有 Session 专属类型（`AgentError` / `AgentRunStatus` / `AgentStopCause` / `AgentRunResult` / `AgentChunk` / `AiDelta`）。新代码优先从 `psi_agent.protocol` 导入。

`any_llm.api.ChatCompletionChunk`（`ai/server.py`）与本仓的同名 dataclass **不同源**：前者是接收上游 provider 响应的 Pydantic 模型，后者用于构造下游 SSE。刻意不统一，靠模块路径区分。
```

- [ ] **Step 3: 根 AGENTS.md 第 19 条（`:240`）**

把该条末句「channel 侧 `_GatewayRouteProvider._cache_key` 复制了同款群聊判定（同群不同发言者须命中同一条缓存，否则每人各打一次 Gateway），**两处判定改动时必须同步**。」替换为：

```
channel 侧 socket 缓存需要同款判定（同群不同发言者须命中同一条缓存，否则每人各打一次 Gateway），故群聊判定与路由键已收敛到 `psi_agent/_feishu_routing.py`（`is_group_chat()` / `route_key()`）——改那一处即全局生效，不再需要人工同步两侧。
```

**保留该条其余全部内容**：群聊整群共用一个 Session 的理由、`-` → `_` 转义的隐私事故说明、`_session_id` 与 `_workspace_for` 两处必须同步转义的要求（转义不在本次收敛范围，仍需人工维护），以及末尾的 `gateway/AGENTS.md` / `channel/AGENTS.md` 交叉引用。

- [ ] **Step 4: 根 AGENTS.md §改动后自检清单（`:340`）**

在「文档同步」那条末尾追加一句：

```
凡改协议格式 / `finish_reason` 常量 / 辅助帧规则，必须同步 `psi_agent/protocol.py` 的 docstring 与本文件「核心通信协议 → 协议归属」；子层 `AGENTS.md` 只引用函数名，不重复写格式定义。
```

- [ ] **Step 5: `ai/AGENTS.md`**

第 69 行改为：

```markdown
- **SSE 层**（`response.prepare()` 之后）：`make_error_chunk()` 构造 error chunk → `finish_reason="error"`（psi-agent 内部扩展，非 OpenAI 标准；构造函数在 `psi_agent/protocol.py`，前缀 `[Upstream Error]: ` 由本层拼好后传入）
```

§Context Compaction 里的 JSON 示例块（`:76-79`）替换为一句引用，删掉重复的格式定义：

```markdown
信号由 `psi_agent.protocol.make_compaction_signal(prompt_tokens=…, threshold=…)` 构造，形状见根 `AGENTS.md`「核心通信协议」。`prompt_tokens` / `threshold` 不是日志字段——Session 用它们做压缩冷却判断，省略会让冷却退化成 fail-open。
```

保留紧随其后的 provider 支持度说明（Groq / Mistral / Ollama strip `stream_options`）。

- [ ] **Step 6: `session/AGENTS.md`**

在 §History 展示白名单（`:348-356`）末尾，把「Gateway `HistoryManager` 同时投影剥掉 `[SEND:]`/`[RECV:]` 标记。」扩写为：

```markdown
Gateway ``HistoryManager`` 同时投影剥掉 ``[SEND:]``/``[RECV:]`` 标记。本节这几个符号（``KIND_CHAT`` / ``message_kind`` / ``wire_role`` / ``is_displayable_chat_message`` / ``strip_transfer_markers`` / ``extract_send_paths``）经 ``session/__init__.py`` 正式导出给 Gateway——**依赖是刻意的**：Gateway 的展示投影必须与 Session 的落盘语义逐字一致，否则同一条历史两处渲染会分叉。此前 Gateway 按内部模块路径导入（依赖刻意、通道非正式），现已补上公开门面。

``[SEND:]`` 的解码（正则 + 空路径过滤）归属 ``channel/_markers.py`` 的 ``iter_send_paths()``，本层不再自持正则——两处正则曾经写法不同，而 Channel 侧没有空路径过滤。
```

另在描述 `protocol.py` 的位置补一句（若该文件在本 AGENTS.md 有专门小节，加在那里；否则加在分层说明处）：

```markdown
``protocol.py`` 持有 Session 专属类型，并重导出 ``psi_agent/protocol.py`` 的共享线格式与常量。新代码优先从 ``psi_agent.protocol`` 导入共享名。
```

- [ ] **Step 7: `router/AGENTS.md` §SSE 约束（`:61-62`）**

两行替换为：

```markdown
- `finish_reason="compaction_needed"` 是辅助帧，不覆盖真实 completion finish。**判定统一用 `psi_agent.protocol.is_terminal_finish()` / `is_auxiliary_finish()`**，不要手写 `!= "compaction_needed"`——这条规则曾在本层被独立实现 5 次（`client.py` 两处、`routing/strategy.py`、`aggregation/strategy.py`、`fallback/strategy.py`）。新增辅助帧类型只改 `protocol.py`。
- `finish_reason="error"`（`FINISH_REASON_ERROR`）转换为 Router 错误；向下游发错误帧用 `make_error_chunk()`，前缀 `[Router Error]: ` 由本层拼好后传入。
```

- [ ] **Step 8: `channel/AGENTS.md`**

第 9 行改为：

```
├── _markers.py        # [RECV:]/[SEND:] 标记协议唯一定义（encode_input + iter_send_paths + 有状态扫描器 SendMarkerScanner）
```

第 39 行改为：

```markdown
- 检测输出中的 `[SEND:/path]` 标记并产生 FileChunk。解码走 `iter_send_paths()`——它同时承载正则与**空路径过滤**：裸 `[SEND:]` 是模型笔误而非传输请求，放过去会让 `_send_file` 拿空 source path 发起上传。`session/history_display.py` 的 Gateway 投影复用同一函数，两侧不会再对"什么算路径"产生分歧。
```

另在本文件描述 SSE 消费的位置补一句：

```markdown
SSE `data:` 行解析用 `psi_agent.protocol.parse_sse_data()`；`finish_reason` 比较用 `FINISH_REASON_*` 常量。
```

- [ ] **Step 9: `gateway/AGENTS.md`**

第 355-356 行（`-` 转义与 `chat_id` 为空两条刻意为之）：**保留全部理由文字**，把 `_is_group` 的引用改为共享函数：

```markdown
- **`chat_id` 为空时不按群路由（刻意为之）**：判定要求 `chat_type in {group, topic}` **且** `chat_id` 非空，否则退回按 `open_id`。宁可这条消息不隔离，也不要建出 `feishu-chat-` 这种无主 session。判定与 Channel 侧共用 `psi_agent/_feishu_routing.py` 的 `is_group_chat()` / `route_key()`（此前两侧各写一遍，判定漂移是隐私事故），本层只保留 `_sanitize_open_id` 的 `-` → `_` 转义——那只服务 session_id / workspace 派生，Channel 不派生这些。
```

第 419 行 `/history` 那行末尾追加：

```
（`is_displayable_chat_message` / `strip_transfer_markers` / `extract_send_paths` 等符号经 `psi_agent.session` 的公开导出取得，见 `session/AGENTS.md`「History 展示白名单」）
```

另在描述 SSE 消费（标题/摘要生成）的位置补一句：

```markdown
`_title_manager` / `_summary_manager` 的 SSE `data:` 行解析用 `psi_agent.protocol.parse_sse_data()`。
```

- [ ] **Step 10: 核对文档引用的符号名与实现一致**

Run: `grep -rn "parse_sse_data\|make_error_chunk\|make_compaction_signal\|is_terminal_finish\|is_auxiliary_finish\|iter_send_paths\|is_group_chat\|route_key" AGENTS.md src/psi_agent/*/AGENTS.md`

逐个核对每个函数名在 `src/psi_agent/protocol.py`、`src/psi_agent/_feishu_routing.py`、`src/psi_agent/channel/_markers.py` 中确实存在且拼写一致：

Run: `grep -n "^def \|^async def " src/psi_agent/protocol.py src/psi_agent/_feishu_routing.py src/psi_agent/channel/_markers.py`

Expected: 文档提到的每个名字都能在实现里找到。

- [ ] **Step 11: 全量验证**

Run: `uv run pytest`
Expected: 全绿

Run: `uv run ruff check`
Expected: 无告警

Run: `uv run ty check`
Expected: 通过

- [ ] **Step 12: Commit**

```bash
git add AGENTS.md src/psi_agent/ai/AGENTS.md src/psi_agent/session/AGENTS.md src/psi_agent/router/AGENTS.md src/psi_agent/channel/AGENTS.md src/psi_agent/gateway/AGENTS.md
git commit -m "docs(agents): 协议归属写进根 AGENTS.md, 子层改为引用

根 AGENTS.md 新增「核心通信协议 → 协议归属」小节, 声明 psi_agent/protocol.py
是线格式/常量/行为规则的唯一定义处, 并写明三条规则 (改一处全局生效、未知值视
为终止、data: 后空格可选)。代码结构补两个新文件。

四层子模块删掉重复的格式定义, 改为引用共享函数名 —— 信息只归属一层。飞书那条
注意事项把\"两处判定必须同步\"改为\"已收敛到 _feishu_routing\", 但隐私事故的理由
与 - 转义两处同步的要求全部保留 (转义不在本次收敛范围)。

session/AGENTS.md 与 gateway/AGENTS.md 补写 Gateway 依赖 Session 符号这条
刻意依赖的说明 —— 此前两侧文档只描述行为, 没有一处交待通道是非正式的。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 完成检查

全部任务完成后：

- [ ] `uv run pytest` 全绿
- [ ] `uv run ruff check` 无告警
- [ ] `uv run ty check` 通过
- [ ] `git diff main --stat` 确认改动范围符合预期（约 17 个源文件 + 6 个 AGENTS.md + 4 个测试文件）
- [ ] 确认无残留：`grep -rn 'line\[6:\]' src/` 无输出
- [ ] 确认无残留：`grep -rn '_GROUP_CHAT_TYPES' src/` 无输出
- [ ] 确认无残留：`grep -rn '!= "compaction_needed"\|== "compaction_needed"' src/` 无输出
- [ ] 确认现有测试未被修改：`git diff main --stat tests/` 应只显示 `test_protocol.py`（新）、`test_feishu_routing.py`（新）、`test__markers.py`（+5 用例）、`test_session.py`（+1 用例）四处，且后两者只有新增行、无删除行








