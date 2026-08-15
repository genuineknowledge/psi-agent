# 协议层统一设计

> **目标**：把 psi-agent 跨组件通信协议的格式定义、常量与行为规则收敛到唯一归属，让"改一处全局生效"成为可能。
>
> **来源**：`docs/onboarding/psi-agent 协议层现状分析与统一方案.md` 与 `docs/onboarding/psi-agent 工程现状评估与迭代建议.md` 的诊断结论，经逐行代码核对后修正。
>
> **范围**：协议统一 + P0/P1 重复实现收敛 + 正式接口补齐。FusionFlow 相关不在本次范围。

## 设计目标

五个组件（AI / Session / Channel / Router / Gateway）通过 OpenAI Chat Completions 格式的 SSE 流通信。它们**本来就该紧密耦合**——通信是它们唯一的工作。问题不是耦合太多，而是已有的耦合没有被显式管理：

- 线格式类型已完整定义，但锁在 `session/protocol.py` 并标注 "Channel-side only"，其余组件各自手写 dict
- `finish_reason` 的四个值以字符串字面量散落全仓
- "compaction 是辅助帧、不得覆盖终止帧"这条行为规则在 Router 里被独立实现了 5 次
- SSE `data:` 行解析 5 处实现，4 处偏离规范
- 飞书群聊判定 3 处实现 + 群聊类型常量 2 处定义，根 AGENTS.md 把不一致定性为隐私事故
- `[SEND:]` 标记正则两处写法不同

本设计**不重新设计协议**，只把已有的类型、常量和行为规则放到正确的位置。除一处 bug 修复外，行为零变更。

## 开工前的代码核对：六处与文档诊断不符

两份 onboarding 文档写于数周前，开工前逐行核对发现六处偏差。这些偏差直接影响实现与测试断言，故记录在案。

| # | 文档结论 | 代码实际 | 对本设计的影响 |
|---|---|---|---|
| 1 | compaction 行为规则 4 处独立实现 | **5 处**——`router/fallback/strategy.py:144` 也有一处（serial fallback 是 5761b73f 之后新增的，文档写时尚不存在） | 替换清单需含第 5 处 |
| 2 | `line[6:]` 失败模式是"吃掉首字符导致 JSON 损坏" | **整帧静默丢弃**——4 处的前置 guard 都是 `startswith("data: ")`（带空格），无空格帧在 guard 处就被 `continue` 跳过，走不到切片 | 测试断言改为"无空格帧能被解析出来"，而非"JSON 不损坏" |
| 3 | 飞书判定 2 处重复 | **3 处判定 + 2 处常量**——`client.py:832` 还有一处与 `_is_group()` 逐字相同的内联判定；`_GROUP_CHAT_TYPES` 在 `client.py:89` 与 `_feishu_manager.py:32` 各定义一遍 | 收敛范围扩大 |
| 4 | 方案文档 §改动前后对比给出 `is_terminal_finish()` 示例：`return value == FINISH_REASON_COMPACTION_NEEDED` | **写反了**——这会把唯一的辅助帧判为终止帧 | 按语义实现（`not in`），不照抄示例 |
| 5 | "下游对空路径有过滤，所以正则差异暂未表现为可见故障" | **Channel 侧无任何空路径过滤**——`SendMarkerScanner.feed()` 会产出 `FileChunk("")`，Feishu `_send_file` 与 Telegram 消费端都直接透传；只有 Gateway 投影侧的 `extract_send_paths()` 有 `if path` guard | 单纯统一正则会新增缺陷，方案改为"宽松正则 + 共享空路径过滤" |
| 6 | 根 AGENTS.md 有"3 个重复编号" | 实际是 `1..19, 19, 20, 21, 19`——**三个 19** | 编号修复不在本 PR 范围 |

## 架构

三个新增/变更的归属点：

```
src/psi_agent/
├── protocol.py          ← 新建：跨组件 SSE 协议的唯一归属
├── _feishu_routing.py   ← 新建：飞书路由判定
├── _appdata.py          ← 已有先例（Session + Gateway 共享工具模块）
├── _sockets.py
├── channel/
│   └── _markers.py      ← SEND 正则与空路径规则的唯一定义
└── session/
    ├── protocol.py      ← 改为重导出共享定义 + 保留 Session 专属类型
    └── __init__.py      ← 补齐 Gateway 已依赖符号的正式导出
```

三个判断依据：

- **`protocol.py` 与五组件平级**，不属于任何一层——它描述的是层与层之间的约定。单文件不建包：当前内容是 3 类型 + 9 常量 + 5 函数。
- **`_feishu_routing.py` 用下划线前缀**，与 `_appdata.py` / `_sockets.py` 对齐；仓里已有"跨组件共享工具模块"先例。飞书特定知识不进通用 `protocol.py`。
- **`session/__init__.py` 的导出补齐**是给一条已经存在的依赖补正式通道。不新增依赖、不改结构。（onboarding 文档称此依赖"双方文档均记录并标刻意为之"，实际核对：`session/AGENTS.md:356` 提到 "Gateway `HistoryManager` 同时投影剥掉 `[SEND:]`/`[RECV:]` 标记"，`gateway/AGENTS.md:419` 提到用了 `is_displayable_chat_message`——两处都只描述了**行为**，没有一处说明"Gateway 绕过 Session 公开门面导入内部模块"是刻意选择。所以这条依赖比文档声称的更缺乏交待，补导出的同时需在两侧文档写明。）

## `psi_agent/protocol.py` 内容

### 格式层

| 内容 | 来源 | 说明 |
|---|---|---|
| `DeltaMessage` | 从 `session/protocol.py` 移入 | 去掉 "Channel-side only" 标注 |
| `StreamChoice` | 同上 | 同上 |
| `ChatCompletionChunk` | 同上 | 含 `to_dict()` / `to_sse()` |
| `make_error_chunk(message)` | 新增 | 替代三处独立构造 |
| `make_compaction_signal(prompt_tokens, threshold)` | 新增 | 替代 `ai/server.py` 手写 dict |
| `parse_sse_data(line)` | 新增 | 统一 `data:` 行解析，修复规范偏离 |

`make_error_chunk()` 接收**已成形的完整 message**，前缀由调用方拼好。三处调用点的前缀各不相同（`[Upstream Error]: ` / `[Router Error]: ` / 裸 message），且 `tests/psi_agent/channel/test__core.py:232`、`test__stream.py:109`、`tests/psi_agent/router/test_server.py:156` 都在断言这些前缀。函数不参与前缀拼接。

`parse_sse_data()` 只管切片，语义留给调用点：

```python
def parse_sse_data(line: str) -> str | None:
    """Extract the payload of an SSE ``data:`` line.

    Returns ``None`` for blank or non-``data:`` lines.  The single space after
    the colon is optional per the SSE spec, so both ``data: X`` and ``data:X``
    yield ``X``.  ``[DONE]`` is returned verbatim -- callers differ on how to
    react to it (``return`` / ``continue`` / ``break``).
    """
    if not line.startswith("data:"):
        return None
    return line[5:].lstrip()
```

不做成"连迭代循环一起收敛"的 `iter_sse_payloads()`：5 处调用点对 `[DONE]` 的反应各不相同（`channel/_stream.py` 是 `return`、`session/ai_client.py` 是 `continue`、gateway 两处是 `break`），且 `router/client.py` 是唯一做多行 `data` 累积的调用点、gateway 两处用手工 `buf` 分割。强行统一循环骨架会把改动面从"一行切片"扩大到"重写五个流消费循环"。

### 常量层

```python
REASONING_KIND_THINKING = "thinking"  # 从 session/protocol.py 移入
REASONING_KIND_TOOL_CALL = "tool_call"  # 同上
REASONING_KIND_TOOL_RESULT = "tool_result"  # 同上

FINISH_REASON_STOP = "stop"  # OpenAI 标准
FINISH_REASON_TOOL_CALLS = "tool_calls"  # OpenAI 标准
FINISH_REASON_ERROR = "error"  # psi-agent 扩展
FINISH_REASON_COMPACTION_NEEDED = "compaction_needed"  # psi-agent 扩展

SSE_DONE = "[DONE]"
```

### 语义层

```python
# 辅助帧：不终止流，附加在终止帧之后，不得覆盖已有的终止帧。
AUXILIARY_FINISH_REASONS = frozenset({FINISH_REASON_COMPACTION_NEEDED})


def is_auxiliary_finish(value: str | None) -> bool:
    return value in AUXILIARY_FINISH_REASONS


def is_terminal_finish(value: str | None) -> bool:
    """未知值视为终止。``None`` 不是终止（流尚未报告结束）。"""
    if value is None:
        return False
    return value not in AUXILIARY_FINISH_REASONS
```

用 frozenset + 两个函数，不用枚举 + 注册表：当前只有一个辅助帧值，注册表的每项都要重复声明 `TERMINAL`，而未知值仍需额外兜底。新增辅助帧时只需往 frozenset 加一个值——这正是本设计要达到的"改一处全局生效"。

### `session/protocol.py` 的变化

线格式类型与 `REASONING_KIND_*` 物理移到 `psi_agent/protocol.py`，`session/protocol.py` 重导出：

```python
"""Session-layer types.  Wire-format definitions live in ``psi_agent.protocol``
and are re-exported here for compatibility."""

from psi_agent.protocol import (
    ChatCompletionChunk,
    DeltaMessage,
    REASONING_KIND_THINKING,
    REASONING_KIND_TOOL_CALL,
    REASONING_KIND_TOOL_RESULT,
    StreamChoice,
)

__all__ = [...]

# 以下为 Session 专属类型，不共享：
# AgentError / AgentRunStatus / AgentStopCause / AgentRunResult / AgentChunk / AiDelta
```

选重导出而非"不重导出、逐一改导入路径"：现有 session 内部与 `tests/psi_agent/session/test_protocol.py` 的 import 路径全部不变，该测试文件继续通过即是迁移正确性的免费验证。代价是一个符号有两条合法导入路径，由 `session/protocol.py` 的 docstring 说明主次。

## `psi_agent/_feishu_routing.py` 内容

```python
GROUP_CHAT_TYPES = frozenset({"group", "topic"})


def is_group_chat(chat_id: str, chat_type: str) -> bool:
    """群聊判定：类型是 group/topic **且** ``chat_id`` 非空。

    ``chat_id`` 缺失时不能按群路由（否则会建出 ``feishu-chat-`` 这种无主
    session），故退回按发送者 open_id——宁可不隔离，也不建垃圾 session。
    """
    return chat_type in GROUP_CHAT_TYPES and bool(chat_id)


def route_key(open_id: str, chat_id: str, chat_type: str) -> str:
    """路由/缓存键：群聊 ``chat:<chat_id>``，私聊裸 ``open_id``。

    ``chat:`` 前缀隔离两个命名空间，免得 chat_id 与 open_id 相撞。判定不一致
    会让两个陌生人共享同一份上下文（隐私事故），故收敛到此处唯一定义。
    """
    if is_group_chat(chat_id, chat_type):
        return f"chat:{chat_id}"
    return open_id
```

`_sanitize_open_id` 的 `-` → `_` 转义**不上提**：它只服务于 Gateway 侧的 `session_id` / workspace 目录派生，Channel 侧不派生这些，上提会把一个单方职责伪装成共享契约。

## SEND 标记正则收敛

单纯统一正则会引入缺陷（见核对表 #5），故改为**宽松匹配 + 共享空路径过滤**：

```python
# channel/_markers.py —— 唯一定义
SEND_RE = re.compile(r"\[\s*SEND\s*:\s*([^\]]*?)\s*\]", re.IGNORECASE)


def iter_send_paths(text: str) -> Iterator[tuple[str, int]]:
    """Yield ``(path, match_end)`` for each ``[SEND:…]`` with a non-empty path.

    Empty / whitespace-only paths are skipped: a bare ``[SEND:]`` is a model
    slip, not a transfer request, and forwarding it would make the Channel
    attempt an upload with an empty source path.
    """
    for match in SEND_RE.finditer(text):
        path = match.group(1).strip()
        if path:
            yield path, match.end()
```

三方共用一套语义：

- `SendMarkerScanner.feed()` 改用 `iter_send_paths()`（需要 `match_end` 维护 `_scan_ptr`）。行为不变——宽松正则新增匹配的都是空路径，恰好被新过滤器拦掉；但 `[ SEND: ]` 这类空标记不再有机会漏进 `FileChunk`。
- `session/history_display.py` 删除本地 `_SEND_PATH_RE` 与重复的 `if path`，`extract_send_paths()` 改为基于 `iter_send_paths()`。行为完全不变。
- `_TRANSFER_MARKER_RE`（strip 用，含 RECV）留在 `history_display.py`：它服务 Gateway 投影的文本清理，与传输协议解码是两件事。

净效果：正则一处定义，空路径规则一处定义，且关掉了一个此前无人注意的空路径上传缺口。这是本设计**唯一的行为变更**，且是修复缺陷。

## 组件改动清单

**Session**（6 文件）

| 文件:行 | 改动 |
|---|---|
| `protocol.py` | 线格式类型与 `REASONING_KIND_*` 上提，改为重导出 |
| `ai_client.py:63` | → `parse_sse_data()` |
| `ai_client.py:55,80` | → `FINISH_REASON_ERROR` |
| `agent.py:514,518,612,709` | → `FINISH_REASON_*` |
| `agent.py:638` | 四元组 `("error","stop","tool_calls","compaction_needed")` → 常量 |
| `channel_adapter.py:112` | → `FINISH_REASON_ERROR` |
| `history_display.py:74` | 删 `_SEND_PATH_RE`，改用 `channel._markers.iter_send_paths` |
| `__init__.py` | 补 `__all__`，正式导出 Gateway 依赖的 7 个符号（`history_display` 6 个 + `ACTIVATE_ALL`） |

**AI**（1 文件）

| 文件:行 | 改动 |
|---|---|
| `server.py:103-113` | → `make_compaction_signal()` |
| `server.py:124-129` | → `make_error_chunk(f"[Upstream Error]: {e}")` |

**Router**（6 文件）

| 文件:行 | 改动 |
|---|---|
| `server.py:146-155` | → `make_error_chunk(f"[Router Error]: {error}")` |
| `client.py:66,184` | → `is_auxiliary_finish()` / `is_terminal_finish()` |
| `client.py:127` | → `parse_sse_data()` |
| `client.py:224` | → `FINISH_REASON_TOOL_CALLS` |
| `routing/strategy.py:85,95` | → 判断函数 + 常量 |
| `aggregation/strategy.py:97,129` | → 判断函数 + 常量 |
| `fallback/strategy.py:99,144` | → 判断函数 + 常量（核对表 #1 新发现的第 5 处） |
| `routing/selector.py:45` | → `FINISH_REASON_STOP` |

**Channel**（3 文件）

| 文件:行 | 改动 |
|---|---|
| `_stream.py:37` | → `parse_sse_data()` |
| `_stream.py:68` | → `FINISH_REASON_ERROR` |
| `_markers.py:18` | `SEND_RE` 成为唯一定义，新增 `iter_send_paths()` |
| `feishu/client.py:89,118,832` | → `_feishu_routing.is_group_chat()` / `route_key()`，删本地常量 |

**Gateway**（3 文件）

| 文件:行 | 改动 |
|---|---|
| `_title_manager.py:62` | → `parse_sse_data()` |
| `_summary_manager.py:77` | → `parse_sse_data()` |
| `_feishu_manager.py:32,75` | → `_feishu_routing`，删本地 `_GROUP_CHAT_TYPES` 与 `_is_group()` |

## 测试

**新增 `tests/psi_agent/test_protocol.py`**

- `parse_sse_data()`：`data: X` 与 `data:X` 都得到 `X`（修 4 处规范偏离的回归锚点）、`data:` 空 payload、非 `data:` 行返回 `None`、空行返回 `None`、`data: [DONE]` 原样返回
- `make_error_chunk()`：断言输出形状与三处调用点当前产物逐字节相同，三种前缀（`[Upstream Error]: ` / `[Router Error]: ` / 裸 message）由调用方传入
- `make_compaction_signal()`：断言含 `psi_compaction.needed/prompt_tokens/threshold` 与 `finish_reason="compaction_needed"`
- `is_terminal_finish()` / `is_auxiliary_finish()`：`stop`/`tool_calls`/`error` → terminal，`compaction_needed` → auxiliary，`None` → 都不是，未知值 `"length"` → terminal

**新增 `tests/psi_agent/test_feishu_routing.py`**

- `is_group_chat()` 参数化：`group`/`topic` × 有无 `chat_id`，`p2p` × 有无
- `route_key()`：群聊得 `chat:<chat_id>`，私聊得裸 `open_id`

抽出共享函数后，两侧一致性由函数唯一性保证，不需要跨组件 parity 测试。

**现有测试一行不改**，全部通过即是"行为零变更"的证据：

- `tests/psi_agent/session/test_protocol.py` — 重导出方案下 import 路径不变
- `tests/psi_agent/channel/test__stream.py:109`、`tests/psi_agent/router/test_server.py:156`、`tests/psi_agent/channel/test__core.py:232` — 断言 error chunk 具体形状

唯一新增断言：`tests/psi_agent/channel/test__markers.py` 补一条 `[SEND:]` 与 `[ SEND: ]` 不产生 `FileChunk`，守护上文关掉的缺口。

**不做的**：契约测试套件（方案文档 §5.3 明确排除，触发条件未到）、Router 未知扩展字段透传测试（与本次改动无因果关系，属独立测试补强）。

## 文档同步

原则：信息只归属一层。根文档说协议归属在哪，子文档只引用不重复定义。

**根 `AGENTS.md`**

- §核心通信协议（`:147-183`）→ 新增「协议归属」小节：线格式、常量、行为规则统一定义在 `psi_agent/protocol.py`；新增 `finish_reason` 值只改这一个文件；`data:` 后空格按 SSE 规范可选，一律用 `parse_sse_data()`
- §代码结构（`:93` 附近）→ 加 `protocol.py` 与 `_feishu_routing.py`
- §关键注意事项第 19 条（`:240`，三个重复 19 中的第一个）→ 末句"channel 侧 `_GatewayRouteProvider._cache_key` 复制了同款群聊判定……**两处判定改动时必须同步**"改为"群聊判定与路由键已收敛到 `psi_agent/_feishu_routing.py`，改那一处即全局生效"。**隐私事故的理由与 `-` 转义两处同步的要求全部保留**——转义不在本次收敛范围（见上文 `_feishu_routing.py` 内容说明），后人仍需知道这个不变式为何重要
- §改动后自检清单「文档同步」（`:340`）→ 收窄为：凡改协议格式/常量/行为规则，必须同步 `protocol.py` docstring 与 §核心通信协议

**四层子模块**

| 文件 | 改动 |
|---|---|
| `ai/AGENTS.md:69,77` | error chunk 与 compaction 信号改为引 `make_error_chunk()` / `make_compaction_signal()`，删重复 JSON 示例 |
| `session/AGENTS.md` | 标注 `protocol.py` 为「重导出共享定义 + Session 专属类型」；在 §History 展示白名单（`:348-356`）补一句：`history_display` 的 6 个符号经 `session/__init__.py` 正式导出给 Gateway |
| `router/AGENTS.md:61-62` | 辅助帧规则改为引 `is_terminal_finish()` |
| `channel/AGENTS.md` | SSE 解析与 SEND 标记改为引 `parse_sse_data()` / `iter_send_paths()` |
| `gateway/AGENTS.md` | 飞书判定（`:355-356` 两条刻意为之）改为引 `_feishu_routing`，隐私事故理由保留；`:419` 的 `/history` 行补明 `is_displayable_chat_message` 等符号来自 `psi_agent.session` 的公开导出 |

**不改的**：两份 onboarding 文档留在原地。它们是诊断记录（含发现问题时的现场），不是需要与代码同步的规格。根 AGENTS.md 三个重复的第 19 条编号修复留给独立 PR——本 PR 会改其中一条的内容但不动编号，避免混入无关的行号位移。

## 实施顺序

按依赖推进，中间态始终可运行：

1. 新建 `protocol.py` + `_feishu_routing.py` + 两个测试文件（不动任何现有代码，可独立验证）
2. `session/protocol.py` 改为重导出（跑 `test_protocol.py` 验证迁移）
3. 逐组件替换：session → ai → router → channel → gateway，每组件跑一次测试
4. SEND 正则收敛 + 补 `test__markers.py` 断言
5. `session/__init__.py` 导出补齐
6. 文档同步

## 验证

- `uv run pytest` 全绿
- `uv run ruff check` 无告警（项目零抑制，不堆 `noqa`）
- `uv run ty check` 通过——本次动了 `session/protocol.py` 的导出面，需确认类型检查仍过

## 不做的事

- 不新建包——一个 `.py` 文件足够
- 不动五个组件的边界
- 不定义 OpenAI 标准部分（`id` / `choices` / `delta` 的字段结构由 OpenAI 规范管）
- 不引入协议版本号——当前规模不需要
- 不拆 Session 的 7 类关注点——与协议问题正交，独立决策
- 不做协议规格书与契约测试（即插即用）——触发时机是首个外部 AI 服务适配 / 外部 Channel 开发 / FusionFlow 成为一等执行器
- 不统一 `any_llm.api.ChatCompletionChunk`——它是接收上游 provider 响应的 Pydantic 模型，与本仓构造下游 SSE 的 dataclass 用途不同，强行统一反增复杂度；靠模块路径 `psi_agent.protocol.ChatCompletionChunk` 区分
- FusionFlow 定位、`ty` 的 `extra-paths` 依赖排查、Gateway 多 Session 故障隔离测试、README 进程拓扑措辞修正——均不在本次范围
