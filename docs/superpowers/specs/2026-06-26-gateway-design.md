# Gateway 设计文档

**日期**: 2026-06-26
**状态**: draft

## 1. 定位

Gateway 是 psi-agent 的新增组件，与现有 CLI（`psi-agent ai/session/channel...`）并存。Gateway 通过 OpenAPI REST 接口管理 AI 和 Session 的生命周期，并提供一个面向 Web UI 的 channel endpoint。

Gateway 自身是一个独立的 aiohttp 进程，AI/Session 作为进程内 anyio task 运行。

## 2. 架构

```
┌───────────────────────────────────────────────────┐
│                  Gateway 进程                       │
│                                                    │
│  ┌──────────────┐  ┌──────────────┐               │
│  │   AIManager  │  │SessionManager│               │
│  │ dict[id, Ai] │  │dict[id,Sess] │               │
│  └──────┬───────┘  └──────┬───────┘               │
│         │                  │                       │
│  ┌──────┴──────────────────┴────────────────────┐ │
│  │          aiohttp OpenAPI REST Server          │ │
│  │  POST   /ais              create AI           │ │
│  │  DELETE /ais/{id}         delete AI           │ │
│  │  GET    /ais              list AIs            │ │
│  │  POST   /sessions         create Session      │ │
│  │  DELETE /sessions/{id}    delete Session      │ │
│  │  GET    /sessions         list Sessions       │ │
│  │  POST   /sessions/{id}/chat  Web UI chat     │ │
│  │  GET    /openapi.json     OpenAPI schema      │ │
│  └──────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────┘
```

- AI 和 Session 之间仍通过 Unix socket（`_sockets.py` 抽象层）以 OpenAI Chat Completions HTTP/SSE 通信
- Gateway 对外仅暴露 TCP（`--listen`）
- Web UI chat endpoint 内部复用 `ChannelCore` 连接 Session

## 3. 组件结构

```
src/psi_agent/gateway/
├── __init__.py           # Gateway dataclass + run()
├── server.py             # aiohttp app + REST handlers
├── _manager.py           # 共享 socket helpers + _new_uuid
├── _ai_manager.py        # AIManager — AI 实例注册表 + 生命周期 + AiInfo
├── _session_manager.py   # SessionManager — Session 实例注册表 + 生命周期 + SessionInfo
├── _chat_manager.py      # SSE 流式对话管理 + 文件物化（复用 ChannelCore）
├── _history_manager.py   # JSONL 历史读取
├── _title_manager.py     # 会话标题 CRUD + AI 自动生成
├── _workspace_manager.py # 目录浏览 + cwd 查询
├── _tray.py              # 系统托盘图标（pystray）
└── _openapi.py           # OpenAPI schema 生成
```

### 3.1 Gateway dataclass

```python
@dataclass
class Gateway:
    listen: str = ""                          # 空 = 127.0.0.1 随机高端口
    socket_path: str = "psi"                 # socket 路径前缀
    verbose: bool = False
    browser: bool = True                    # 启动时打开浏览器
    tray: str | None = None                 # 系统托盘图标文件路径，None 表示不启用
```

### 3.2 `Gateway.run()` 启动流程

1. `setup_logging(verbose=self.verbose)` — 第一行
2. `listen = self.listen or f"http://127.0.0.1:{_random_port()}"` — 未指定则随机端口
3. 创建 `anyio.create_task_group()` 并手动 `__aenter__`
4. 创建 `AIManager` 和 `SessionManager`（内存 dict + `anyio.Lock`）
5. 构建 `aiohttp.web.Application`，注入 managers + `ChatManager`/`HistoryManager`/`TitleManager`/`WorkspaceManager`
6. 注册 REST 路由 + Web UI chat 路由 + `/openapi.json` + SPA 静态文件服务
7. `create_site(runner, addr)` — TCP
8. 若 `--browser`（默认），`webbrowser.open(addr)`
9. `await anyio.sleep_forever()`
10. `finally` shield cleanup: `runner.cleanup()` + `tg.__aexit__()`

## 4. AIManager

```python
@dataclass
class AIManager:
    _entries: dict[str, _AiEntry]
    _lock: anyio.Lock

@dataclass
class _AiEntry:
    scope: anyio.CancelScope
    socket: str             # /tmp/{socket_path}/ais/{ai_id}.sock
    provider: str
    model: str
```

### 4.1 `create(ai_id, provider, model, api_key, base_url) -> AiInfo`

1. 获取 lock，断言 `ai_id` 不重复
2. 自动生成 socket 路径：`_socket_path(prefix, "ais", ai_id)`（Linux 返回 `/tmp/{prefix}/ais/{id}.sock`，Windows 返回 `\\.\pipe\{prefix}\ais\{id}`）
3. `_ensure_socket_dir(socket)` 创建 socket 父目录（anyio 异步）
4. 构造 `Ai(session_socket=socket, provider=..., model=..., api_key=..., base_url=...)`
5. 创建 `anyio.CancelScope`，`task_group.start_soon(ai.run)`
6. 存入 `_entries[ai_id]`
7. `_wait_socket(socket)` 轮询等待 socket 文件出现 + 额外 0.3s sleep 确保 acceptor 就绪
8. 返回 `AiInfo(id, socket, provider, model)`

### 4.2 `delete(ai_id) -> None`
1. 获取 lock，断言存在
2. `del _entries[ai_id]` + `entry.scope.cancel()` + `await _remove_socket(entry.socket)`

### 4.3 `list_all() -> list[AiInfo]`

### 4.4 `get_socket(ai_id) -> str`

### 4.5 `has(ai_id) -> bool`

## 5. SessionManager

```python
@dataclass
class SessionManager:
    _entries: dict[str, _SessionEntry]
    _lock: anyio.Lock

@dataclass
class _SessionEntry:
    scope: anyio.CancelScope
    channel_socket: str
    ai_id: str
    workspace: str
```

### 5.1 `create(session_id, ai_id, workspace) -> SessionInfo`

1. 获取 lock，断言 `session_id` 不重复
2. `ai_socket = aimanager.get_socket(ai_id)` — 查 AI socket
3. 自动生成 channel socket：`_socket_path(prefix, "channels", session_id)`
4. workspace 为空时默认 `os.getcwd()`
5. 构造 `Session(workspace=..., channel_socket=..., ai_socket=..., session_id=session_id)`
6. 创建 `CancelScope`，`task_group.start_soon(session.run)`
7. 存入 `_entries[session_id]`
8. `_wait_socket(channel_socket)` 轮询等待 channel socket 就绪
9. 返回 `SessionInfo(id, ai_id, workspace, channel_socket)`

### 5.2 `delete(session_id) -> None`
1. 获取 lock，断言存在
2. `del _entries[session_id]` + `entry.scope.cancel()` + `await _remove_socket(entry.channel_socket)`

### 5.3 `list_all() -> list[SessionInfo]`

### 5.4 `get_socket(session_id) -> str`

### 5.5 `has(session_id) -> bool`

### 5.6 `get_workspace(session_id) -> str`

## 6. REST API

### 6.1 AI endpoints

**`POST /ais`** — 创建 AI
```json
// Request
{
  "id": "gpt-4o",
  "provider": "openai",
  "model": "gpt-4o",
  "api_key": "sk-xxx",
  "base_url": "https://api.openai.com/v1"
}
// Response 201
{
  "id": "gpt-4o",
  "socket": "/tmp/psi/ais/gpt-4o.sock",
  "provider": "openai",
  "model": "gpt-4o"
}
```

`id` 不传则自动生成 UUID。

**`DELETE /ais/{ai_id}`** — 删除 AI
```json
// Response 200
{"id": "gpt-4o", "status": "stopped"}
// Response 404
{"error": "ai 'x' not found"}
```

**`GET /ais`** — 列出所有 AI
```json
// Response 200
[{"id": "gpt-4o", "socket": "...", "provider": "openai", "model": "gpt-4o"}]
```

### 6.2 Session endpoints

**`POST /sessions`** — 创建 Session
```json
// Request
{
  "id": "my-session",
  "ai_id": "gpt-4o",
  "workspace": "./my-workspace"
}
// Response 201
{
  "id": "my-session",
  "ai_id": "gpt-4o",
  "workspace": "./my-workspace",
  "channel_socket": "/tmp/psi/channels/my-session.sock"
}
```

`id` 不传则自动生成 UUID。传已有 `session_id` 可 resume workspace 中的 history。

**`DELETE /sessions/{session_id}`** — 删除 Session
```json
// Response 200
{"id": "my-session", "status": "stopped"}
```

**`GET /sessions`** — 列出所有 Session

### 6.3 Web UI Chat endpoint

**`POST /sessions/{session_id}/chat`**

支持 `application/json` 和 `multipart/form-data` 两种 Content-Type。

```
Accept: text/event-stream
```

**Multipart Request**：
```
chunks: JSON array of text chunks (string)
file: binary file upload
```

上传的文件保存到 `~/Downloads/.psi/{date}/{filename}`。

```json
// Request (application/json)
{
  "chunks": [
    {"type": "text", "text": "Hello, what's in this image?"},
    {"type": "blob", "name": "file.png", "data": "base64..."}
  ]
}
```

```text
// Response: SSE stream
data: {"type": "text", "text": "Hello! "}

data: {"type": "text", "text": "I see a cat."}

data: {"type": "blob", "name": "generated.png", "data": "base64..."}

data: [DONE]
```

**内部实现：**
- 查 `SessionManager.get_socket(session_id)` 获取 channel socket
- 复用 `channel._core.ChannelCore` 构造连接
- `TextChunk(text)`、blob（base64 解码后由 `_save_upload()` 落 `~/Downloads/.psi/<date>/`，持久保留，转为 `FileChunk`）；multipart 文件上传通过 blob 通道走相同路径
- 输出：`TextChunk` → yield `{"type": "text"}`，`FileChunk` → 读取文件 base64 编码 → yield `{"type": "blob"}`

## 7. REST API 补充端点

### 7.1 Titles

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/titles` | 获取所有 session 标题 |
| POST | `/titles` | 设置 session 标题 `{id, title}` |
| POST | `/titles/generate` | AI 自动生成标题 `{id, user_text, assistant_text}` |

TitleManager 通过直接连接 AI socket（绕过 Session history）发送简短 prompt 生成标题。

### 7.2 Workspace

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/workspace/browse?path=...` | 浏览目录，返回 entries 列表 |
| GET | `/workspace/cwd` | 获取服务端当前工作目录 |

### 7.3 History

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/sessions/{session_id}/history` | 返回 `[{role, text}]` 的 user/assistant 消息 |

## 8. OpenAPI Schema

提供 `GET /openapi.json`，返回标准 OpenAPI 3.0 schema，描述所有端点。

## 9. CLI 集成

```python
# src/psi_agent/cli.py
def main() -> None:
    cmd = tyro.cli(Run | Ai | Session | ChannelGroup | Gateway)
    anyio.run(cmd.run)
```

```bash
psi-agent gateway --listen http://0.0.0.0:8080 --socket-path psi
```

Gateway 不在 `_run.py` 的批量启动中。

## 10. 错误约定

| 场景 | HTTP | Body |
|------|------|------|
| 资源不存在 | 404 | `{"error": "ai/session 'x' not found"}` |
| 参数校验失败 | 400 | `{"error": "..."}` |
| 内部异常 | 500 | `{"error": "internal error"}` |

Chat endpoint 流式错误复用现有 `finish_reason="error"` SSE chunk 透传。

## 11. 设计约束

遵循 psi-agent 全局约束：

- `setup_logging` 第一行
- 零 `sys.exit`，错误用 `raise`
- 全部 anyio，禁止 asyncio/pathlib IO/time.sleep
- 所有 IO 操作使用 anyio 异步接口，禁止 `os.makedirs`、`os.unlink` 等同步文件操作。Socket 父目录创建使用 `await anyio.Path(...).mkdir(parents=True, exist_ok=True)`（`_ensure_socket_dir`）
- 零 noqa / per-file-ignores（`server.py` 和 `__init__.py` 各 1-2 处必要的 `# ty: ignore`）
- `from __future__ import annotations`
- `X | None` 非 `Optional[X]`
- 参数透传原则（chat endpoint 额外字段穿透到 ChannelCore→Session）
- 可取消：`finally` 清理所有 task scope
- 跨平台 socket 路径：Linux 使用 `/tmp/{prefix}/...` (Unix socket)，Windows 使用 `\\.\pipe\{prefix}\...` (Named Pipe)，由 `_socket_path()` 函数统一处理

## 12. 测试策略

- **单元测试**：`AIManager` / `SessionManager` CRUD + 并发
- **集成测试**：Gateway process + Mock AI + 真实 Session + 最小 workspace，通过 REST API 驱动
- **端到端**：`POST /sessions/{id}/chat` → Session → Mock AI，验证 SSE 输出格式
- SSE 测试复用现有 `read_sse()` 工具

## 13. 未来扩展

- Web UI 前端直连 Gateway chat endpoint
- 更多 channel 类型通过 Gateway 统一管理
- Session 列表/状态查询的 WebSocket 推送
