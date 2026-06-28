# Channel 层 ReasoningChunk 设计规格

**日期**: 2026-06-28
**状态**: 已实现

> **演进说明（截至 2026-06-29）**：本规格为当时的设计快照。正文中的 `Chunk = FileChunk | TextChunk | ReasoningChunk` 已被后续重构取代——`Chunk` 拆分为 `InputChunk`/`OutputChunk`，marker 编解码、SSE 解析与 interval 缓冲分别抽到 `_markers.py` / `_stream.py`，错误统一为 `ChannelError`。当前权威状态以 `src/psi_agent/channel/AGENTS.md` 为准。

---

## 1. 概述

Session 层已经通过 SSE 的 `delta.reasoning` 字段向 Channel 输出"思考流"——它聚合了 AI thinking、`[Tool Call: ...]` 意图和 `[Tool Result: ...]` 结果。但 `ChannelCore.post()` 当前**完全忽略 `reasoning`**，只读取 `delta.content`（`_core.py:99`），导致 reasoning 从未到达任何 channel，尽管 `channel/AGENTS.md:50` 已声明它应以 dim 样式渲染。

本次重构在 Channel 层引入 `ReasoningChunk`，使 `Chunk = FileChunk | TextChunk | ReasoningChunk`：
- `ChannelCore.post()` 将 `reasoning` 流切分为有序的 `ReasoningChunk`；
- 终端通道（CLI/REPL）以 dim 样式 inline 渲染 reasoning；
- Telegram/Feishu **不变**，继续只匹配 `TextChunk`/`FileChunk`，静默忽略 `ReasoningChunk`。

本变更是**纯 Channel 层**改动——Session 层已经发出 `delta.reasoning`，无需改动。

---

## 2. 范围

| 项 | 状态 |
|----|------|
| `_types.py` 新增 `ReasoningChunk` | 在范围内 |
| `_core.post()` 切分 reasoning 流 | 在范围内 |
| CLI/REPL 渲染 reasoning（dim, inline） | 在范围内 |
| Telegram/Feishu 渲染 reasoning | **范围外**（保持忽略） |
| Session/AI 层改动 | **范围外**（已发出 reasoning） |

---

## 3. Chunk 类型

文件 `psi_agent/channel/_types.py`：

```python
@dataclass
class FileChunk:
    path: str

@dataclass
class TextChunk:
    text: str

@dataclass
class ReasoningChunk:
    text: str

Chunk = FileChunk | TextChunk | ReasoningChunk
```

`ReasoningChunk` 镜像 `TextChunk`（仅 `text: str`）——reasoning 已经是纯文本（thinking + tool 意图 + tool 结果聚合）。

---

## 4. ChannelCore.post() — 类型感知的单活动缓冲

### 4.1 设计目标

当前 `post()` 用单一 content 缓冲 + 共享 interval timer 批量产出 `TextChunk`。现在存在两条文本流（content / reasoning）交错到达，必须：
1. 保持语义顺序（tool reasoning 在最终 content 之前）；
2. 把两类文本分别产出为 `TextChunk` / `ReasoningChunk`；
3. **向后兼容**：纯 content 流的行为与今天完全一致（现有 `_core` 测试全绿）。

### 4.2 机制：单活动缓冲 + 活动 kind

不再维护两个并行缓冲，而是一个"活动缓冲 + 活动 kind"：

```
buf: str            # 当前待 flush 的文本
kind: "text" | "reasoning" | None   # buf 当前的类型

每个 SSE delta：
    依次处理 reasoning 子串、content 子串（单 delta 同时含两者时罕见，但按此顺序处理）
    对每个非空子串 (incoming_kind, text)：
        if kind is not None and incoming_kind != kind and buf:
            flush(kind)         # 类型切换 → 先 flush 旧类型
            buf = ""; timer = None
        kind = incoming_kind
        buf += text
        if incoming_kind == "text":
            <在 full_content 上做 [SEND:...] 扫描，见 4.3>
        if timer is None:
            timer = now + interval
        if now >= timer:
            flush(kind); buf = ""; timer = None

stream end：
    if buf: flush(kind)

flush(kind)：
    kind == "reasoning" → yield ReasoningChunk(buf)
    否则               → yield TextChunk(buf)
```

**关键不变量**：在每个 delta 处理完后，`buf` 至多属于一种 kind（类型切换会先 flush 另一种）。因此 timer 到期/流结束时只需 flush 当前 `kind`。

### 4.3 `[SEND:...]` 检测保持 content-only

`[SEND:/path]` 文件标记只可能出现在 AI 的最终 content 输出中，绝不出现在 reasoning 里。因此：
- 保留独立的 `full_content: str`（仅累计 content）、`scan_ptr: int`、`emitted: set[str]`；
- 仅在处理 content 子串时把它追加到 `full_content` 并扫描新增部分；
- reasoning 文本**永不**参与 `[SEND:...]` 扫描。

这与现有 `[SEND]` 跨 chunk 检测、去重逻辑完全一致（`test_post_send_cross_chunk` / `test_post_send_dedup` 保持绿）。

### 4.4 内部状态（per-post）

| 变量 | 用途 |
|------|------|
| `buf: str` | 当前活动缓冲（content 或 reasoning） |
| `kind: str \| None` | `buf` 的类型 |
| `timer_target: float \| None` | 共享 interval timer |
| `full_content: str` | 仅 content 累计，供 `[SEND]` 扫描 |
| `scan_ptr: int` | `[SEND]` 扫描指针 |
| `emitted: set[str]` | `[SEND]` 去重 |

### 4.5 错误处理 / 其他

不变：非 200 抛异常、`finish_reason="error"` 抛异常、多 choice 抛异常、0 choice 心跳跳过、`[RECV:]` 输入拼接。

---

## 5. 终端渲染（CLI + REPL）

两者均 `interval=0`（每个 delta 立即各自 flush，顺序天然保留）。

| Chunk | 渲染 |
|-------|------|
| `TextChunk` | `console.print(text, end="")`（不变） |
| `ReasoningChunk` | `console.print(text, end="", style="dim")` |
| `FileChunk` | 终端通道忽略（不变，范围外） |

Reasoning 以 dim 样式 **inline** 渲染在正常字重答案之前，**无分隔符**（reasoning 天然先于 content）。符合 `channel/AGENTS.md:50`。

CLI（`cli/client.py`）和 REPL（`repl/client.py`）的 chunk 循环各加一个 `isinstance(chunk, ReasoningChunk)` 分支。

---

## 6. Telegram / Feishu — 不变

两者的 chunk 循环只匹配 `TextChunk`（累积/`stream.append`）和 `FileChunk`（文件发送）。未匹配的 `ReasoningChunk` 被静默忽略，行为与今天完全一致。**不修改**这两个文件。

---

## 7. 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 更新 | `psi_agent/channel/_types.py` | 新增 `ReasoningChunk`，扩展 `Chunk` union |
| 重写 | `psi_agent/channel/_core.py` | `post()` 改为类型感知单活动缓冲 |
| 更新 | `psi_agent/channel/cli/client.py` | 加 `ReasoningChunk` dim 分支 |
| 更新 | `psi_agent/channel/repl/client.py` | 加 `ReasoningChunk` dim 分支 |
| 不变 | `psi_agent/channel/telegram/client.py` | 静默忽略 `ReasoningChunk` |
| 不变 | `psi_agent/channel/feishu/client.py` | 静默忽略 `ReasoningChunk` |
| 更新 | `psi_agent/channel/AGENTS.md` | 架构块反映 `ReasoningChunk` |

---

## 8. 测试策略

### 8.1 `test__types.py`

- `ReasoningChunk` 构造（`text` 属性）；
- union `isinstance` 互斥（`ReasoningChunk` 不是 `TextChunk`/`FileChunk`）。

### 8.2 `test__core.py`（Mock aiohttp UnixSite，沿用现有模式）

| 测试 | 验证 |
|------|------|
| reasoning-only delta | 产出 `ReasoningChunk(text)` |
| reasoning → content 切换 | 有序产出 `[ReasoningChunk, TextChunk]` |
| 同类 reasoning 在 interval 内合并 | 合并为单个 `ReasoningChunk` |
| `[SEND:/p]` 出现在 reasoning 文本中 | **不**产出 `FileChunk` |

### 8.3 回归

现有 `test__core.py` 全部 content-only 用例（合并、interval 切分、`[SEND]` 检测/去重/跨 chunk、flush、错误、多 choice）保持绿——向后兼容验证。

---

## 9. 非目标 / 范围外

- Telegram/Feishu 的 reasoning 渲染（保持忽略）；
- 单 delta 同时携带 content+reasoning 的"交错"场景优化（按"先 reasoning 后 content"顺序处理即可，不追求更细粒度）；
- reasoning 与 content 之间的视觉分隔符（明确不加）；
- 终端 `FileChunk` 落盘（仍然忽略）。
