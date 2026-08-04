# 实验性单目标 Routing Router

`psi_agent.router` 是下一代 Router 的实验实现。目前只实现了**单目标分流（routing）**：
它把多个 OpenAI Chat Completions 兼容的 AI 服务暴露成一个统一服务，每个普通用户轮次先由
Selector AI 选择一个候选编号，再由 Router 将原请求流式转发给该候选对应的私有 Socket。

该目录同时为后续的 aggregation 和 fallback 模式保留共享 HTTP/SSE、错误类型和上游客户端；
**当前代码不包含 aggregation，也不包含 fallback**。

> 注意：`router` 尚未注册到顶层 `psi-agent router` CLI。仓库中的
> `psi-agent router` 仍指向原版 `psi_agent.router.Router`。实验版可以通过
> `RoutingRouter.run()` 嵌入其他协程，或运行本目录提供的 `run_qwen_routing.py`。

## 1. 目标与非目标

当前实现负责：

- 对外提供 `POST /chat/completions`；
- 用 Selector AI 对一个普通用户轮次进行分类；
- 只向 Selector 暴露候选编号和描述，不暴露私有 Socket；
- 将 Selector 返回的候选编号映射为本地 `RoutingTarget`；
- 将原始请求流式转发给一个目标服务；
- 校验上游单 choice SSE；
- 透传 `messages`、`tools`、`tool_choice` 及其他公开请求参数；
- 在一次工具调用链路中使用轻量 sticky，保证多个独立 POST 继续访问同一目标；
- 在 HTTP 200 已提交后，将运行时错误编码为 `finish_reason="error"` 的 SSE 帧。

当前实现不负责：

- 保存普通多轮会话历史；
- 执行工具；
- 聚合多个模型的回答；
- 隐式 fallback、重试、熔断或健康检查；
- 负载均衡；
- 鉴权和租户隔离；
- 跨进程或持久化 sticky 状态。

多轮历史和工具执行属于 Session；目标 AI 服务是无状态的。

## 2. 目录结构

```text
router/
├── client.py                  # Socket-aware Chat Completions HTTP/SSE 客户端
├── server.py                  # Router 公共 HTTP/SSE 服务边界
├── models.py                  # 共享 CompletionResult
├── errors.py                  # 共享 RouterError 类型
├── routing/
│   ├── entry.py               # RoutingRouter 组合入口
│   ├── models.py              # RoutingTarget / RoutingConfig / SelectionResult
│   ├── prompts.py             # 纯函数 build_selector_messages
│   ├── selector.py            # LLM Selector + candidate_id 安全映射
│   ├── strategy.py            # 单目标转发 + tool sticky
│   └── errors.py              # RouteSelectionError
├── run_qwen_routing.py        # 本机 Qwen TCP 演示拓扑
├── tool_demo/
│   ├── tools/routing_probe.py # REPL 工具调用测试工具
│   └── systems/system.py      # 强制真实工具调用的测试 prompt
└── tests/                     # transport、selector、strategy 等测试
```

共享代码放在包根目录，模式特有逻辑放在 `routing/`。后续 aggregation/fallback 应新增同级策略目录，
复用 `client.py`、`server.py`、`models.py` 和 `errors.py`，而不是把模式分支继续塞进
`RoutingStrategy`。

## 3. 组件与 Socket 拓扑

以 `run_qwen_routing.py` 的本机 TCP 配置为例：

| 地址 | 组件 | 角色 |
|---|---|---|
| `http://127.0.0.1:18100` | Router | 对 Session 暴露统一 AI API |
| `http://127.0.0.1:18101` | Selector AI | 返回一个候选编号 |
| `http://127.0.0.1:18102` | General AI | 普通问答目标 |
| `http://127.0.0.1:18103` | Code AI | 编程任务目标 |
| `http://127.0.0.1:18110` | Session | 对 Channel 暴露会话 API（示例端口） |

```text
User
  ↓
Channel / REPL
  ↓  Channel --session-socket == Session --channel-socket
Session
  ↓  Session --ai-socket == Router session_socket
Router :18100
  ├─→ Selector :18101
  └─→ selected target :18102 or :18103
```

这里的 `socket` 是 psi-agent 组件之间的服务端点，不是 Qwen/OpenAI 请求体中的厂商字段。
地址由 `psi_agent._sockets` 按前缀解析：

- `http://` / `https://`：TCP；
- `\\.\pipe\...`：Windows Named Pipe；
- 无上述前缀的路径：POSIX Unix socket。

一个连接两端会使用同一个服务端地址，但参数名从各组件视角命名。例如：

```text
Session --channel-socket http://127.0.0.1:18110
Channel --session-socket http://127.0.0.1:18110
```

Session 在该地址监听，Channel 连接该地址；它们的职责并不相同。

## 4. 核心数据类型与配置约束

### 4.1 `RoutingTarget`

```python
RoutingTarget(
    candidate_id="strong-code",
    socket="http://127.0.0.1:18103",
    description="Programming, debugging, testing, architecture, and code review.",
)
```

- `candidate_id` 是向 Selector 暴露的不透明编号；
- `socket` 只保存在 Router 本地；
- `description` 是 Selector 判断的主要配置依据；
- 编号必须匹配 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`；
- 三个字段都会去除首尾空白，且不能为空。

### 4.2 `RoutingConfig`

```python
RoutingConfig(
    session_socket="http://127.0.0.1:18100",
    selector_socket="http://127.0.0.1:18101",
    targets=[...],
    selector_timeout=60.0,
    target_timeout=180.0,
    max_selection_chars=12_000,
)
```

构造时执行以下校验：

- Router 公开地址和 Selector 地址不能为空且不能相等；
- 至少配置一个 `RoutingTarget`；
- `candidate_id` 必须唯一；
- target socket 必须唯一；
- target socket 不能等于 Router 的公开 `session_socket`，防止直接自递归；
- timeout 必须是有限正数或 `None`；
- `max_selection_chars` 必须是正整数。

`SelectionResult` 将已验证的 `candidate_id` 和私有 `RoutingTarget` 绑定起来，后续策略不再信任
Selector 的任意输出。

## 5. 对外请求与响应协议

Router 对外提供：

```text
POST /chat/completions
Content-Type: application/json
```

典型请求：

```json
{
  "messages": [
    {"role": "user", "content": "请实现一个 LRU Cache"}
  ],
  "tools": [],
  "stream": true,
  "temperature": 0.2,
  "routing": {
    "session_id": "session-001"
  }
}
```

边界校验规则：

- 请求体必须是 JSON object；
- `messages` 必须存在且为 object 列表；
- `tools` 可省略，存在时必须为 object 列表；
- `stream` 省略时按 `true` 处理，显式传入非 `true` 会拒绝；
- `routing` 可省略，存在时必须为 object；
- `routing.session_id` 可省略，存在时必须是非空字符串。

正常响应是单 choice SSE：

```text
data: {"choices":[{"index":0,"delta":{"content":"..."},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

`routing` 是 Router 内部元数据，不会转发给目标；请求中的 `model` 也会删除，因为具体模型由目标
AI 服务进程的启动配置决定。除这两个字段外，其他参数均深拷贝并透传，`stream` 被强制设为
`true`。

## 6. Selector 的输入、输出与安全边界

### 6.1 Selector 实际收到什么

`RouteSelector.build_request()` 不会把原请求直接交给 Selector，而是生成新的分类请求：

```json
{
  "messages": [
    {
      "role": "system",
      "content": "You are the selector for a multi-backend AI routing service..."
    },
    {
      "role": "user",
      "content": "{\"candidates\":[...],\"conversation\":[...],\"available_tools\":[...]}"
    }
  ],
  "stream": true,
  "temperature": 0
}
```

Selector 可见：

- 每个候选的 `candidate_id`；
- 每个候选的 `description`；
- 压缩后的对话内容；
- 工具的 `name` 和最多 256 字符的 `description`。

Selector 不可见：

- Router 私有 target socket；
- Router 公开 socket；
- 工具完整 JSON Schema；
- 客户端传入的 `model`；
- 本地 `RoutingTarget` 映射表。

### 6.2 对话压缩规则

`_compact_messages()` 的当前行为：

1. 只保留 role 为字符串的消息；
2. 字符串 content 原样保留；
3. 多模态 block 列表替换为 `[multimodal content with N block(s)]`；
4. 其他 content 类型跳过；
5. 从最新消息向前选取，直到达到 `max_selection_chars`；
6. 如果最新一条消息本身超预算，则只保留其尾部；
7. 最后恢复时间顺序。

默认字符预算是 `12_000`。这里按字符而不是 token 计算。

### 6.3 Selector 输出约束

Selector 必须只返回：

```json
{"candidate_id":"strong-code"}
```

以下输出全部视为错误：

```text
Markdown code fence
解释文字
未知 candidate_id
额外 socket 字段
数组而不是 object
tool_calls
finish_reason 非 stop
```

Router 使用 `json.loads` 严格解析，要求 object 只能包含 `candidate_id`，然后通过本地字典映射为
私有 Socket。Selector 无法通过返回一个任意 Socket 绕过配置。

## 7. 普通轮次的分流算法

普通用户请求的消息末尾不是 `role="tool"`。当前策略对每个普通用户轮次都重新调用 Selector：

```text
validate request
  ↓
discard old sticky for this session_id
  ↓
call Selector
  ↓
validate candidate_id
  ↓
temporarily remember selection by session_id
  ↓
strip model/routing and forward to target
  ↓
stream target SSE to caller
  ↓
target finishes with stop/error/etc. → discard sticky
```

因此 sticky 不会把同一个 Session 的所有普通问题永久固定到一个模型。

## 8. 多轮会话的职责边界

多轮会话由 Session 实现，不由 Router 实现：

- Session 在内存和 AppData JSONL 中保存 conversation history；
- 每一轮都把完整 `messages` 发给 Router；
- Router 将完整 `messages` 透传给目标；
- 目标 AI 服务本身无状态，只依据本次收到的消息回答。

`routing.session_id` 在当前 Router 中主要用于关联工具迭代。只传相同 `session_id`、但不传之前的
`messages`，Router 不会自动恢复历史。

新开一个 REPL 也不等于新建 Session：只要新 REPL 仍连接同一个 Session `channel_socket`，就会继续
使用同一份会话历史。要得到干净历史，必须创建新的 Session 或使用新的 Session `session_id`。

## 9. 工具调用与 sticky

### 9.1 职责分工

```text
Target model  决定调用哪个 tool，并返回 OpenAI-compatible tool_calls
Session       累积 tool_calls、执行本地 async function、写入 role=tool
Router        透传 tools/tool_calls，并保证工具迭代继续使用同一 target
```

`tools`、`tool_calls`、`finish_reason="tool_calls"`、`role="tool"` 和 `tool_call_id` 来自上游
OpenAI 兼容 Function Calling 协议，不是本 Router 自定义字段。

### 9.2 为什么需要 sticky

Session 的一次工具运行由多个独立 POST 组成：

```text
POST 1: user request
  → Selector chooses strong-code
  → strong-code returns tool_calls

Session executes tool

POST 2: messages end with role=tool
  → Router must skip Selector
  → reuse strong-code
  → strong-code returns final answer or another tool_calls
```

没有 sticky 时，工具结果可能被 Selector 当成普通文本重新分类到另一个目标，从而在一次 Agent run
中途更换模型，并额外增加一次 Selector 成本和延迟。

### 9.3 sticky 的实现

内存状态：

```python
_sticky_targets: dict[str, SelectionResult]
```

简化逻辑：

```python
is_tool_iteration = messages[-1].get("role") == "tool"

if session_id and is_tool_iteration and session_id in sticky_targets:
    selection = sticky_targets[session_id]
else:
    if session_id and not is_tool_iteration:
        discard(session_id)
    selection = await selector.select(...)
    if session_id:
        sticky_targets[session_id] = selection

stream selected target

if request did not complete or finish_reason != "tool_calls":
    discard(session_id)
```

唯一正常保留 sticky 的结束原因是：

```text
finish_reason="tool_calls"
```

以下情况会清理 sticky：

- `finish_reason="stop"`；
- `finish_reason="error"`；
- `length`、`content_filter` 等任何非 `tool_calls` 结束原因；
- 上游流未正常完成；
- 客户端断开或 Router 流异常；
- 同一 Session 的新普通用户轮次；
- 显式 `discard(session_id)`；
- Router 启动失败、关闭或重启时的 `clear()`。

如果第一次请求没有携带 `routing.session_id`，Router 无法把后续 `role=tool` POST 与原目标关联，
因此会重新调用 Selector。psi-agent Session 会自动携带 conversation 的 `session_id`。

### 9.4 多工具和多轮工具调用

Router 不执行工具，也不关心工具函数实现。目标返回的 SSE 被透传给 Session；Session 按 tool call
index 累积流式参数，一次返回多个调用时并行执行，再按原顺序写入多个 `role=tool` 消息。只要最后
一条消息仍为 `role=tool`，Router 就继续复用 sticky。目标再次返回 `tool_calls` 时 sticky 继续保留，
直到最终 `stop`。

## 10. `RouterHttpClient` 的 SSE 处理

`RouterHttpClient.stream()`：

- 按 Socket 类型创建 aiohttp connector；
- 对 `<socket>/chat/completions` 发起 POST；
- 要求 HTTP status 为 200；
- 支持一个 SSE event 中的多行 `data:`；
- 遇到 `data: [DONE]` 结束；
- 忽略没有 `data:` 的行；
- 忽略 `choices=[]` 的 usage-only/心跳帧；
- 要求每个有效事件恰好一个 choice；
- 将 `delta=null` 归一化为 `{}`；
- 校验 `delta` 必须为 object、`finish_reason` 必须为字符串或 null；
- 要求流中至少出现一个非 `compaction_needed` 的完成原因；
- 在 `finally` 中关闭 response 和 client session。

`RouterHttpClient.complete()` 用于 Selector：

- 拼接所有 `delta.content`；
- 拼接所有 `delta.reasoning`；
- 按 tool call index 累积 `id`、`type`、函数名和分片 arguments；
- `compaction_needed` 只作为辅助帧，不覆盖真正的 `stop/tool_calls`；
- 上游 `finish_reason="error"` 转成 `RouterUpstreamError`；
- Selector 正常结束后返回一个 `CompletionResult`。

SSE 中相同的 `id`、`created` 和 `model` 表示多个 chunk 属于同一次 completion；真正变化的是
`choices[0].delta`。例如多个 chunk 的 content 分别为 `"candidate"`、`"_id"` 和
`"\":\"strong-code\"}"`，拼接后才得到完整 Selector JSON。

## 11. Router Server 与错误边界

`create_router_app()` 使用 100 MiB `client_max_size`，注册一个 `/chat/completions` POST handler。

在 `response.prepare()` 之前发现的错误使用 HTTP JSON：

```json
{
  "error": {
    "message": "messages must be a list of objects",
    "type": "invalid_request_error",
    "param": null,
    "code": 400
  }
}
```

HTTP 200 已提交后发生的 Selector、目标服务或 SSE 错误使用内部错误帧：

```json
{
  "id": "error",
  "choices": [
    {
      "index": 0,
      "delta": {"content": "[Router Error]: ..."},
      "finish_reason": "error"
    }
  ]
}
```

Session 会把 `finish_reason="error"` 当成失败，不将该轮错误结果提交到 conversation history。

Server 用 `aclosing()` 消费策略 async generator；连接重置或流错误时会按请求中的 `session_id`
调用 `discard()`。启动失败和正常关闭都会在 shielded cleanup 中释放 aiohttp runner，并清空全部
sticky。

## 12. 日志与可观测性

关键 INFO 日志：

```text
Received experimental Router request
Router upstream response status: socket='http://127.0.0.1:18101', status=200
Routing selector chose candidate 'strong-code'
Routing request to candidate 'strong-code'
Router upstream response status: socket='http://127.0.0.1:18103', status=200
Reusing sticky routing candidate 'strong-code' for tool iteration in session '...'
```

`verbose=True` 时还会记录每个上游 SSE 行和每个下游 SSE chunk。Selector 的原始 JSON 在
`18101` AI 终端中通常表现为多个 `delta.content` 分片；Router 当前只在 INFO 打印最终解析出的
`candidate_id`，没有单独打印拼接后的原始 Selector 文本。

验证一次普通分流，应观察：

```text
Selector socket :18101
→ Routing selector chose ...
→ exactly one target socket :18102 or :18103
```

验证工具 sticky，应观察第二个 POST：

```text
Received experimental Router request
→ Reusing sticky routing candidate ...
→ same target socket
```

这段日志之间不应出现新的 Selector `:18101` 请求。

## 13. Qwen 本机 TCP 示例

### 13.1 前置环境

每个 AI 服务终端都要单独设置环境变量，因为 PowerShell 环境变量不会自动传播到已经打开或已经
运行的其他终端：

```powershell
$env:PSI_AI_API_KEY = "sk-替换为百炼API-Key"
$env:PSI_AI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
```

启动日志必须显示 `api_key=set`。`--provider openai` 表示使用 OpenAI 兼容协议，不表示使用
OpenAI 模型。

### 13.2 启动三个 AI 服务

Selector：

```powershell
uv run --no-dev --no-sync psi-agent ai --session-socket http://127.0.0.1:18101 --provider openai --model qwen-plus --max-context-tokens 0 --verbose
```

General：

```powershell
uv run --no-dev --no-sync psi-agent ai --session-socket http://127.0.0.1:18102 --provider openai --model qwen-plus --max-context-tokens 0 --verbose
```

Code：

```powershell
uv run --no-dev --no-sync psi-agent ai --session-socket http://127.0.0.1:18103 --provider openai --model qwen3-coder-plus --max-context-tokens 0 --verbose
```

如果账号没有 `qwen3-coder-plus` 权限，可以临时把 Code 也设为 `qwen-plus`；两个目标仍然是不同
Socket，可以通过 Router 日志验证选择结果。

### 13.3 启动实验 Router

```powershell
uv run --no-dev --no-sync python -m psi_agent.router.run_qwen_routing
```

成功日志：

```text
Experimental Router listening on http://127.0.0.1:18100
```

### 13.4 启动普通 Session 与 REPL

```powershell
$sessionId = "qwen-routing-" + [guid]::NewGuid().ToString("N")
uv run --no-dev --no-sync psi-agent session --ai-socket http://127.0.0.1:18100 --channel-socket http://127.0.0.1:18110 --workspace E:\PycharmCode\psi-agent --session-id $sessionId --verbose
```

另一个终端：

```powershell
uv run --no-dev --no-sync psi-agent channel repl --session-socket http://127.0.0.1:18110
```

普通问题预期选择 `general`；代码问题预期选择 `strong-code`。最终应以 Router 的
`Routing selector chose candidate ...` 和目标 Socket 日志为准，不能只根据回答风格判断。

## 14. 真实 REPL 工具调用测试

`tool_demo` 提供一个无副作用的确定性工具：

```python
async def routing_probe(value: str) -> str:
    return f"ROUTING_TOOL_OK::{value.upper()}::7391"
```

专用 system prompt 要求模型必须真实调用该工具，不能自行猜测结果。

### 14.1 启动工具测试 Session

选择一个未占用的 Channel 端口：

```powershell
$toolSessionId = "qwen-tool-routing-" + [guid]::NewGuid().ToString("N")
uv run --no-dev --no-sync psi-agent session --ai-socket http://127.0.0.1:18100 --channel-socket http://127.0.0.1:18112 --workspace E:\PycharmCode\psi-agent\src\psi_agent\router\tool_demo --session-id $toolSessionId --verbose
```

Session 应记录：

```text
Loaded tool: 'routing_probe'
Loaded 1 tool(s) from 1 file(s)
```

### 14.2 连接 REPL

```powershell
uv run --no-dev --no-sync psi-agent channel repl --session-socket http://127.0.0.1:18112
```

发送：

```text
这是一个代码工具调用链路测试。必须调用 routing_probe，参数 value 为 alpha。不要自行生成结果，收到工具结果后原样回复。
```

REPL 预期：

```text
[Tool Call: routing_probe({"value": "alpha"})]
[Tool Result: ROUTING_TOOL_OK::ALPHA::7391]
ROUTING_TOOL_OK::ALPHA::7391
```

Session 预期：

```text
AI requested tool calls, processing...
Executing tool: 'routing_probe'({'value': 'alpha'})
Tool result ('routing_probe'): 'ROUTING_TOOL_OK::ALPHA::7391'
```

Router 首次 POST 应选择 `strong-code`；工具结果 POST 应出现：

```text
Reusing sticky routing candidate 'strong-code' for tool iteration in session '...'
Routing request to candidate 'strong-code'
Router upstream response status: socket='http://127.0.0.1:18103', status=200
```

工具最终 `stop` 后再发送一条普通用户消息，应重新看到 Selector 请求，而不是 `Reusing sticky`。
由于当前上下文分类限制，新的普通问题仍可能被重新判为 `strong-code`；验证清理时应判断“是否重新
调用 Selector”，而不是强制要求候选一定为 `general`。

## 15. 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `No openai API key provided` | AI 服务进程没有读取百炼 Key | 在每个 AI 终端设置 `PSI_AI_API_KEY` 后重启 |
| HTTP 401 | Key 无效或 Key 与 Base URL 地域不匹配 | 使用同地域 Key 与 endpoint |
| `WinError 10048` | TCP 端口已被另一个进程监听 | 停止旧服务或换未占用端口 |
| `Tools directory not found` | Session workspace/agent 指错目录 | 指向包含 `tools/` 的 `tool_demo` |
| `Selector output is not valid JSON` | Selector 返回解释、Markdown 或截断内容 | 查看 18101 日志并改进 selector prompt/model |
| 新开 REPL 仍有旧历史 | 新 REPL 连接了同一个 Session | 使用新的 Session `session_id` 或新 Session 服务 |
| 普通新话题仍选 strong-code | Selector 被旧代码历史主导 | 见“当前限制” |
| tool 结果后再次调用 Selector | 缺少/改变 `routing.session_id`，sticky 丢失或 Router 重启 | 保持相同 session_id 并检查 sticky 日志 |

## 16. 当前限制与后续优化

### 16.1 新话题识别不足

当前 Selector prompt 要求为整个 `conversation` 选择候选，而 `_compact_messages()` 会把最多
12,000 字符的近期历史一起发送。历史中大量代码可能压过一个新的普通问题，导致 Selector 在已经
重新调用、sticky 已清除的情况下仍独立选择 `strong-code`。

判断依据：

- 出现 `Routing selector chose candidate ...`：Selector 确实重新分类，不是 sticky；
- 出现 `Reusing sticky routing candidate ...`：才是工具迭代复用。

合理的后续方案不是简单关键词匹配，也不是只发送最后一条消息，而是拆分：

```json
{
  "current_request": "新增一些函数注释",
  "recent_context": ["上一轮生成的代码摘要"],
  "candidates": []
}
```

Selector 需要先语义判断当前请求是 `continuation` 还是 `new_topic`：省略了对象的“新增一些函数
注释”仍应承接代码上下文；独立的“我朋友叫小红”应按新普通话题处理。

### 16.2 sticky 仅在单进程内存中

- 没有 TTL；
- 没有容量上限；
- Router 重启后全部丢失；
- 多 Router 实例之间不共享；
- 同一 session 的并发请求没有专门串行化；
- 如果目标返回 `tool_calls` 后 Session 永远不再发送结果，条目会保留到新普通轮次、显式
  `discard()` 或 Router 关闭。

生产化前应考虑 TTL、容量控制、并发语义和多实例状态策略。

### 16.3 仅支持单目标分流

当前每次只调用一个目标，没有：

- fallback 序列；
- 并行 aggregation；
- aggregator AI；
- cost/latency/load-aware policy；
- 目标健康状态；
- 重试和断路器。

### 16.4 Selector 输出仍依赖 prompt 约束

Selector 请求使用 `temperature=0` 和严格提示词，但当前没有向上游传递 JSON Schema
`response_format`。模型仍可能返回 Markdown、额外解释或未知编号，Router 会安全拒绝，但不会自动
修复或重试。

### 16.5 Server 当前不预取首帧

`handle_chat_completions()` 当前先 `response.prepare()`，再开始迭代策略生成器。因此合法请求的
Selector/目标错误通常发生在 HTTP 200 之后，并通过 SSE error 帧报告。当前也没有在 prepare 前
预取首个上游事件。

### 16.6 CLI 尚未闭环

`RoutingRouter` 可以通过 Python 调用，Qwen 演示有独立模块，但尚未作为新模式注册到顶层 tyro
CLI，也未接入 Gateway 的生产 Router 生命周期。

## 17. 测试

现有测试覆盖：

- `RouterHttpClient` 的 SSE 拼接、compaction 和 finish reason 校验；
- Server 的 SSE 正常转发、HTTP 400、流式错误；
- `RoutingTarget`/`RoutingConfig` 约束；
- Selector 请求隐去 Socket、候选映射和错误输出；
- prompt builder 输出；
- 目标单选和公开参数透传；
- tool iteration sticky；
- 新用户轮次重新分类；
- `discard()`/`clear()`；
- 非法请求拒绝。

安装开发依赖后可直接运行：

```powershell
uv run python -m pytest src/psi_agent/router/tests -q
```

真实 Qwen、Session、工具执行和 REPL 的端到端验证使用第 13、14 节流程。自动测试应使用 mock
服务，避免依赖外部 API、余额、模型随机性和网络状态。
