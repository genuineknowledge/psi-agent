# Channel 层公共部件提取设计规格

**日期**: 2026-06-25
**状态**: 待审批

---

## 1. 概述

Channel 层当前 CLI（74行）和 REPL（96行）共享大量重复代码：SSE 解析、HTTP 连接管理、错误处理。本次重构将公共逻辑提取到 `ChannelCore`，同时引入文件传输协议标记（`[RECV:path]`/`[SEND:path]`）和 SSE 缓冲合并机制。重构后 CLI ~18行，REPL ~41行。

ChannelCore 管理 aiohttp 连接和 SSH 管道，对外暴露 `post(list[Chunk]) -> AsyncIterator[Chunk]`。CLI 和 REPL 只需处理 Chunk 类型——不再接触 HTTP、SSE 或错误处理。

---

## 2. Chunk 类型

文件 `psi_agent/channel/_types.py`：

```python
@dataclass
class FileChunk:
    path: str

@dataclass
class TextChunk:
    text: str

Chunk = FileChunk | TextChunk
```

### 2.1 协议标记（仅 ChannelCore 内部，不暴露为类型）

| 标记 | 方向 | 含义 |
|------|------|------|
| `[RECV:/abs/path]` | input | 告诉 Session 端读取此文件（Session 用自己的 tool 处理） |
| `[SEND:/abs/path]` | output | Session 在输出中嵌入此标记，ChannelCore 检测到后 yield `FileChunk(path)` |

---

## 3. ChannelCore

文件 `psi_agent/channel/_core.py`：

```python
@dataclass
class ChannelCore:
    session_socket: str
    interval: float = 1.0    # SSE 缓冲合并窗口，秒

    async def __aenter__(self) -> ChannelCore:
        ...

    async def __aexit__(self, ...) -> None:
        ...

    async def post(self, chunks: list[Chunk]) -> AsyncIterator[Chunk]:
        ...
```

### 3.1 连接管理

- `__aenter__` 调用 `resolve_connector_and_endpoint(session_socket)` 创建 `aiohttp.ClientSession`
- `post()` 复用同一连接
- `__aexit__` 关闭 ClientSession

### 3.2 post() 内部流程

**Input（Chunks → String）：**

```
[FileChunk(/a.txt), TextChunk("hello")]
    → "[RECV:/a.txt]\nhello"
    → POST /chat/completions
      {"messages": [{"role": "user", "content": "..."}], "stream": true}
```

**Output（SSE → Chunks）：**

```
for each SSE delta.content:
    _full_buf   += delta
    _chunk_buf  += delta

    # [SEND:...] 检测：只扫描新增部分
    orig = len(_full_buf) - len(delta)
    new = _full_buf[_scan_ptr:]
    for match in re.finditer(r"\[SEND:(.+?)\]", new):
        if path not in _emitted:
            yield FileChunk(path)
            _emitted.add(path)
        _scan_ptr = orig + match.end()
    # 无匹配时 _scan_ptr 不推进——下个 chunk 重扫

on interval timer (from first chunk of window):
    yield TextChunk(_chunk_buf)
    _chunk_buf = ""

on stream end:
    yield TextChunk(_chunk_buf)   # flush 残余
```

### 3.3 内部状态（per-post，非 ChannelCore 对象级别）

| 变量 | 生命周期 | 用途 |
|------|----------|------|
| `_full_buf: str` | post 内累计 | 跨 interval 检测 `[SEND:...]`，永不重置 |
| `_chunk_buf: str` | per-interval | 当前窗口内容，到期 flush 后清空 |
| `_scan_ptr: int` | post 内递增 | 避免重复扫描已处理文本 |
| `_emitted: set[str]` | post 内累计 | 去重：同一路径只 yield 一次 FileChunk |

每次 `post()` 调用初始化所有状态。

### 3.4 错误处理

- HTTP 非 200：抛异常（调用方自行处理）
- SSE 流中 `finish_reason="error"`：抛出包含错误信息的异常
- SSE 流中多个 choice（len != 1）：抛异常
- `ClientConnectorError`：透传

---

## 4. CLI 瘦身

### 4.1 修改前（74 行）

- `resolve_connector_and_endpoint` + `aiohttp.ClientSession` 连接管理
- `req_data` 构造
- HTTP 状态码错误处理
- SSE 逐行解析 + reasoning_content/content 显示
- `finish_reason="error"` 检测
- 顶级 try/except

### 4.2 修改后（~18 行）

```python
async def run_cli(*, session_socket: str, message: str) -> None:
    try:
        async with ChannelCore(session_socket, interval=0.0) as core:
            async for chunk in core.post([TextChunk(message)]):
                if isinstance(chunk, TextChunk):
                    console.print(chunk.text, end="")
    except Exception as e:
        logger.error(f"CLI error: {e}")
        console.print(f"[red]Error: {e}[/red]")
        raise
    console.print()
```

---

## 5. REPL 瘦身

### 5.1 修改前（96 行）

与 CLI 相同 + prompt_toolkit REPL 循环。

### 5.2 修改后（~41 行）

```python
async def run_repl(session_socket: str) -> None:
    try:
        async with ChannelCore(session_socket, interval=0.0) as core:
            while True:
                try:
                    user_input = await prompt_session.prompt_async("> ", ...)
                except (EOFError, KeyboardInterrupt):
                    console.print("\nGoodbye!")
                    break

                if not user_input.strip():
                    continue

                console.print()
                try:
                    async for chunk in core.post([TextChunk(user_input)]):
                        if isinstance(chunk, TextChunk):
                            console.print(chunk.text, end="")
                except Exception as e:
                    logger.error(f"REPL error: {e}")
                    console.print(f"\n[red]Error: {e}[/red]")
                console.print("\n")
    except ClientConnectorError as e:
        console.print(f"[red]Connection error: {e}[/red]")
        raise
```

---

## 6. 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `psi_agent/channel/_types.py` | `FileChunk`, `TextChunk`, `Chunk` |
| 新建 | `psi_agent/channel/_core.py` | `ChannelCore` 类 |
| 重写 | `psi_agent/channel/cli/client.py` | 74→~18行 |
| 重写 | `psi_agent/channel/repl/client.py` | 96→~41行 |
| 不变 | `psi_agent/channel/cli/__init__.py` | dataclass 参数不变 |
| 不变 | `psi_agent/channel/repl/__init__.py` | dataclass 参数不变 |
| 更新 | `psi_agent/channel/AGENTS.md` | 反映新架构 |

---

## 7. 测试策略

### 7.1 单元测试

| 文件 | 测试内容 |
|------|----------|
| `test__types.py` | FileChunk/TextChunk 构造、Chunk 类型 union |
| `test__core.py` | Mock aiohttp session → 验证 SSE→Chunk 缓冲合并、[SEND] 检测、[RECV] 拼接、interval 行为、flush、去重 |

### 7.2 集成测试

- 更新 `tests/integration/test_channel_error.py` 和 `test_channel_repl_cli.py`：验证通过 ChannelCore 的端到端流程未破坏

---

## 8. 非目标/范围外

- FileChunk 的落盘逻辑：终端通道（CLI/REPL）直接忽略 FileChunk，落盘由未来 Web channel 实现
- `[RECV:path]` 的实际文件读取：由 Session 端 tool 处理，Channel 层不碰 I/O
- 不支持单次 post 内跨消息的状态共享：每次 `post()` 独立
