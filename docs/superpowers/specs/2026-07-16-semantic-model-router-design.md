# 语义大模型路由设计

## 目标与边界

psi-agent 提供一个可选的、与普通 AI 服务协议兼容的语义路由服务。Session 仍然只向 `POST /chat/completions` 发送 OpenAI Chat Completions 请求；当 Session 的上游地址指向 Router 时，Router 先调用一个已经启动的普通 AI 服务完成候选选择，再把原始请求转发到被选中的候选 AI 服务。

Router 不负责创建或配置模型，不保存会话状态，也不直接持有 provider、模型名、base URL 或 API key。路由模型、候选模型和默认模型都必须先按普通 `psi-agent ai` 服务启动，Router 只保存它们的 socket 地址。路由模型的 socket 可以独立于候选列表，不要求同时作为候选模型。

该实现不依赖第三方 `llmrouter`，不执行多轮任务拆解、投票、embedding、关键词匹配或第二次本地分类。

## 命令行接口

Router 是顶层命令 `psi-agent router`：

```powershell
uv run psi-agent router `
  --session-socket "http://127.0.0.1:8100" `
  --router-socket "http://127.0.0.1:7001" `
  --upstream `
    '{\"socket\":\"http://127.0.0.1:7001\",\"description\":\"本地通用中文问答、摘要和简单任务\"}' `
    '{\"socket\":\"http://127.0.0.1:7002\",\"description\":\"复杂推理、代码分析、数学和多步骤任务\"}' `
  --default-socket "http://127.0.0.1:7001" `
  --router-timeout 10 `
  --router-context-chars 12000 `
  --verbose
```

参数契约如下：

- `session_socket`：Router 自身监听地址，供 Session 连接。
- `router_socket`：负责生成路由决策的已启动普通 AI 服务地址。
- `upstream`：可重复传入的严格 JSON 字符串，每项只允许 `socket` 和 `description` 两个非空字符串字段，顺序即候选索引顺序。
- `default_socket`：无法进行语义选择时使用的默认 AI 服务地址，不要求出现在候选列表中。
- `router_timeout`：可选的有限正数，限制一次路由模型请求的秒数；未设置时不增加总超时。
- `router_context_chars`：传给路由模型的序列化上下文字符上限，必须为正数，默认 `12000`。
- `verbose`：启用 DEBUG 日志。不存在 `log_router_details` 参数；路由理由和最终 socket 在默认日志级别下也会输出。

普通 AI 服务仍通过顶层 `psi-agent ai` 启动，参数含义和行为不因 Router 而改变。

## 代码结构

```text
src/psi_agent/router/
├── __init__.py   # Router CLI dataclass、配置校验、aiohttp 生命周期
├── models.py     # Upstream、RouteDecision 和严格 upstream JSON 解析
├── prompts.py    # 中文路由提示词及候选描述插值
├── selector.py   # 上下文压缩、路由模型 SSE 客户端、决策解析
├── server.py     # 请求处理、默认回退和业务上游 SSE 代理
└── AGENTS.md     # Router 层维护约束
```

Router 与普通 AI 服务暴露相同的 `POST /chat/completions`，因此 Session、Channel、workspace 和内部 SSE 协议无需识别 Router 的实现细节。

## 请求处理流程

### 1. 接收并保存原请求

`handle_router_chat_completions()` 解析 JSON，并要求顶层是 object。处理期间创建浅拷贝，但不删除、覆盖或重新构造其中的字段；`model`、`messages`、`tools`、采样参数以及未知扩展字段都会保持原值。

### 2. 构造受限上下文

`serialize_context()` 从 `messages` 中提取路由所需信息：

- 保留首个 system 文本以及 user、assistant 文本；
- assistant tool call 只保留工具名，不包含参数；
- tool result 只留下“结果存在”的标记，不包含结果正文；
- 图片、音频和文件内容替换为短标记；
- 必须至少存在一个可用 user 文本，否则直接使用 `default_socket`；
- 超长时优先删除较旧的非 system 区块，必要时用 `[TRUNCATED]` 截断，最终不超过 `router_context_chars`。

### 3. 构造中文路由提示词

所有提示词集中在 `prompts.py`。路由模型只看到从 0 开始的候选索引和对应 `description`，不会看到候选 socket、默认 socket 或 API key。

提示词要求先判断最新用户任务与上文是否相关：相关时结合上文判断整体任务类型；不相关时忽略上文，只按最新任务选择。返回格式为：

```json
{"candidate": 1, "reason": "任务需要复杂代码分析"}
```

### 4. 调用路由模型

`selector.py` 通过共享的 `resolve_connector_and_endpoint()` 连接 `router_socket`，发送固定的流式请求：

```json
{
  "model": "router",
  "messages": ["由 prompts.py 构造的 system/user 消息"],
  "stream": true
}
```

客户端遵循显式单 choice 约定：0 choice 作为心跳跳过，超过 1 个 choice 或结构不合法视为路由失败；若收到 `finish_reason="error"` 也视为失败。它聚合所有文本 `delta.content`，然后从纯 JSON、Markdown fenced JSON 或夹杂说明文字的响应中寻找第一个有效 object。`candidate` 必须是范围内的整数且不能是布尔值；缺失的有效 `reason` 会使用固定诊断文本代替。

### 5. 本地映射并转发

有效候选索引只在本地映射到 `Upstream.socket`，路由模型不能生成任意地址。无论语义选择成功还是回退到默认 socket，Router 都把原始请求 body 原样转发，尤其不会改写 `model`。

地址遵循共享 socket 约定：支持 HTTP/HTTPS、Unix socket 和 Windows Named Pipe。HTTP/HTTPS 配置值是服务 base address，`resolve_connector_and_endpoint()` 会无条件追加 `/chat/completions`，因此配置中不要包含完整 endpoint。

### 6. 代理 SSE

业务上游返回 HTTP 200 后，Router 使用 `iter_any()` 逐块代理原始 bytes，不解析或重建正常 SSE。这会保留 content、reasoning、tool calls、finish reason、provider 扩展和 `[DONE]`。

## 默认回退与错误

以下情况使用 `default_socket`：

- 没有可用 user 上下文；
- 路由模型超时、连接失败或返回非 200；
- 路由 SSE/JSON 结构不兼容；
- 路由模型返回 `finish_reason="error"`；
- 无法解析有效候选索引。

回退只影响当前请求，不会关闭后续语义路由。默认上游失败后不会递归回退。

- 请求 JSON 解析失败或顶层不是 object：返回 OpenAI 风格 HTTP 400。
- 业务上游在下游 response prepare 前返回非 200：返回 OpenAI 风格 HTTP 502。
- prepare 后代理异常：尽力发送 `finish_reason="error"` 的内部 Router error chunk 并结束响应。
- 下游断开：停止代理，让异步上下文关闭上游连接。

## 日志、取消与清理

每个可路由请求默认输出两条 INFO 摘要：`Router reason: ...` 和 `Router result: socket='...'`。恢复性语义选择失败记录 WARNING，代理失败记录 ERROR，每个代理 SSE bytes chunk 记录 DEBUG；`--verbose` 用于开启这些额外 DEBUG 信息。INFO 摘要不得泄露候选描述、候选索引、原始上下文或密钥。

`Router.run()` 的第一行可执行语句必须是 `setup_logging(verbose=self.verbose)`。启动失败与正常退出都用 shielded `runner.cleanup()`；网络 session 和 response 使用异步上下文管理器，确保取消时释放连接。

## 测试覆盖与验证

当前测试覆盖严格 upstream 解析、提示词信息边界、上下文截断、决策解析、路由 SSE 客户端、超时、语义选择、默认回退、原请求透传、byte-preserving SSE、HTTP/流式错误、日志摘要、生命周期清理、CLI 形状和本地多服务集成。

修改 Router 后至少运行：

```text
uv run pytest tests/psi_agent/router tests/integration/test_semantic_router.py tests/psi_agent/test_cli.py -v
uv run ruff check src/psi_agent/router tests/psi_agent/router tests/integration/test_semantic_router.py
uv run ruff format --check src/psi_agent/router tests/psi_agent/router tests/integration/test_semantic_router.py
uv run ty check
uv run psi-agent router --help
uv run psi-agent ai --help
```
