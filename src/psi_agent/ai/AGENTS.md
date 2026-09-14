# AI 层设计文档

## 概述

AI 层是一个统一的多 provider LLM 客户端，对外提供 OpenAI-compatible HTTP/SSE 服务。

核心能力：
- 接收 OpenAI Chat Completions 格式的 HTTP 请求
- 使用 [any-llm-sdk](https://github.com/mozilla-ai/any-llm) 转发到任意 LLM provider
- 透传 SSE 流式响应（含 Anthropic→OpenAI 格式转换）
- 错误统一处理（HTTP 非流式 + SSE 流式）

## 架构

```
Session ── POST /chat/completions ──► AI
                                            │
                                            │ any_llm.acompletion()
                                            ▼
                              OpenAI / Anthropic / Gemini / ...
```

单一入口：`psi-agent ai --provider <name> --model <name> --api-key <key> --base-url <url>`。

## 模块

| 文件 | 职责 |
|------|------|
| `__init__.py` | `Ai` dataclass + `run()` + `serve_ai()` + `_build_http_client()`（按 provider 决定是否自带 TLS 上下文的 httpx client，见下） |

| `server.py` | `handle_chat_completions()` — 请求处理 |

## 数据流

```
1. CLI → Ai.run()
2. run() → serve_ai(provider, model, api_key, base_url, handler)
3. serve_ai → `create_site(runner, socket_path)`（按地址前缀选 UnixSite / TCPSite / NamedPipeSite，见 `psi_agent._sockets`）+ 注册 handler
4. 请求到达 → handle_chat_completions()
5. 解析 body → await any_llm.acompletion(provider=..., stream=True, ...)
6. async for chunk → chunk.model_dump_json() → SSE write
```

## 配置

| 参数 | CLI | 环境变量 | 说明 |
|------|-----|----------|------|
| `provider` | `--provider` | `PSI_AI_PROVIDER` | any-llm-sdk provider key |
| `model` | `--model` | `PSI_AI_MODEL` | 模型名 |
| `api_key` | `--api-key` | `PSI_AI_API_KEY` | 上游 API key |
| `base_url` | `--base-url` | `PSI_AI_BASE_URL` | 上游 base URL |
| `max_context_tokens` | `--max-context-tokens` | `PSI_MAX_CONTEXT_TOKENS` | Token 阈值，超过时触发 compaction（默认 100K，0 = 禁用） |
| `reasoning_effort` 兜底档位 | — | `PSI_AI_REASONING_EFFORT` | 调用方没给 `reasoning_effort` 时下发的档位（默认 `medium`）；只对 `deepseek` provider 生效，进程启动读一次 |

全部参数可选，CLI 优先于环境变量。`model` 在请求处理中被启动配置覆盖（AI 层隐藏上游 model 细节）。

`PSI_AI_REASONING_EFFORT` 是唯一**不走 CLI** 的一项（它不是启动参数，而是那个兜底常量的值）：
空串/纯空白视为未设置，回落到 `medium`，即不设这个变量时行为与引入它之前逐字节相同。
取值不做白名单校验，写错由上游拒绝（见下）。

## 请求透传

Session 发送的 body 中，除 `model` 被启动配置覆盖、`messages` 被显式提取、`stream` 被剥离（AI 层始终强制 `stream=True`）、`provider`/`api_key`/`api_base`/`routing` 防御性剥离（避免与启动配置冲突）外，其余字段（`tools`, `temperature`, `max_tokens` 等）全部通过 `**body` 透传给 any-llm-sdk。

`reasoning_effort` 是唯一被**补默认值**的字段（`setdefault("reasoning_effort", <默认档位>)`，调用方给了就用它的，含 `"none"`；默认档位由 `PSI_AI_REASONING_EFFORT` 决定，缺省 `medium`）。**兜底只对 `deepseek` provider 生效**（白名单在 `_REASONING_EFFORT_DEFAULT_PROVIDERS`）——只有它会缺省值误读；其余 provider 保持不传，交给上游默认行为。

### 为什么必须显式传 `reasoning_effort`

any-llm 的 DeepSeek provider 把缺省值 `"auto"` 读成「调用方没要思维」，转而下发 `extra_body.thinking={"type": "disabled"}`（见 1.26.0 的 `providers/deepseek/deepseek.py`）。DeepSeek V4 官方默认是**开**思维，any-llm 为对齐旧版 `deepseek-chat` 行为主动反转成关。

后果不是「字段丢了」而是「思维根本没生成」：模型被关掉思维通道后仍要推理，就把自我对话写进 `content` —— 即线上的 thinking 泄漏（复述提问 + 自问自答）。实测同一 prompt：不传 = 0/9 个 chunk 带思维链，传 `"medium"` = 24/33。

该默认值在 1.21.0 之后引入（1.21.0 的同一文件里没有 `thinking` 分支），而依赖声明是 `any-llm-sdk>=1.21.0` —— 一次静默的上游行为变更改掉了线上语义。

**兜底必须 provider 感知，不能无条件对全部 provider 生效（2026-09 修正）**：最初这段默认对**所有** provider 全局生效。ToC 装机版不经过 psi-agent 的 `ai/server.py`（SPA 的 `DEFAULT_REMOTE_AI` 直接打云端 OpenAI 兼容网关），所以恰好没吃到这个默认；但 ToB 自部署若用 `provider: 'openai'` 直连 DeepSeek 兼容端点（如 `api.deepseek.com/v1`）就会吃到 —— 而 `openai` provider 并没有 auto→disabled 逻辑：不传 `reasoning_effort` 时 thinking 本来就开着，思考照常进 `reasoning_content`。强制传 `"medium"` 反而把思考档位压到中档，模型于是把**过程叙述**写进 `content`（每轮 tool call 前一段自述），用户在飞书看到整段自我对话 —— 与本页描述的泄漏形态一致。故兜底范围收敛到 `_REASONING_EFFORT_DEFAULT_PROVIDERS`（`{"deepseek"}`）。实测同一 tool-call prompt：不传 `reasoning_effort` → content=0 / reasoning=306；传 `"medium"` → content=34 / reasoning=345。

### 什么时候该调 `PSI_AI_REASONING_EFFORT`，代价是什么

档位是**延迟与可读性的直接权衡**，各部署的取舍不同 —— 所以值做成可配，不必改安装包里的常量。真链路实测（同一批 6 题）：

| 档位 | 6 题实测墙钟 | 代价 |
|------|------|------|
| `medium`（默认，不设变量） | 23.5s | 无 —— 与引入环境变量之前逐字节相同 |
| `minimal` | 13.1s | 未观察到 thinking 泄漏 |
| `none` | 5.5s | 约一半答复的**开头变成英文自我对话**（思考泄漏进正文） |

- **`none` 不是免费的**：`none` 正是 any-llm DeepSeek provider 关掉思考的判据之一（`reasoning_effort in (None, "none", "auto")` → `thinking={"type": "disabled"}`），也就是本页开头那条泄漏的成因 —— 模型被关掉思维通道后仍要推理，就把自我对话写进 `content`。拿它换延迟要自己权衡：只有面向内部批处理、对延迟敏感且能容忍正文噪声的部署才值得。`minimal` 是实测里唯一「省掉近一半时间、又没观察到泄漏」的档位。
- **只对 `deepseek` provider 生效**：其余 provider 根本不读这个值（`_REASONING_EFFORT_DEFAULT_PROVIDERS`），设了不会有任何变化 —— 它们不传 `reasoning_effort`，思考本来就开着。
- **进程启动读一次**：改环境变量要重启 AI 进程/容器，没有热更新。
- **调用方优先**：Session 显式传了 `reasoning_effort` 时环境变量不生效（`setdefault` 语义），含显式传 `"none"`。
- **不校验取值**：合法档位由 any-llm 的 `ReasoningEffort` 定义（`none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max`/`auto`）。写错不在这里拦——在本层抄一份名单只会在上游加档位时变成假的拒绝，而非法值上游自己会拒（`CompletionParams` 的该字段是字面量类型）。

## Provider 支持

any-llm-sdk 原生支持的 50+ provider 全部可用，无需额外代码。包括：OpenAI, Anthropic, Gemini, DeepSeek, Mistral, Groq, Ollama, Cerebras, Cohere, Perplexity, Fireworks, Together, xAI, Bedrock, Azure, VertexAI 等。

Anthropic→OpenAI 格式转换由 any-llm-sdk 自动完成，包括 `thinking_delta`→`reasoning`、`input_json_delta`→`tool_calls`、`content_block_stop`→`finish_reason="tool_calls"`。

## 自带 TLS 上下文的 httpx client

`serve_ai()` 的 `_build_http_client(provider)` 建一个 `httpx.AsyncClient(verify=psi_agent._tls.client_ssl_context())`，经 `acompletion(client_args={"http_client": ...})` 灌进 provider 的 client 构造。

| 问题 | 说明 |
|------|------|
| 为什么要 | 默认组列表下部分网络会丢 TLS 握手包，表现为**对话请求全部超时**（实测 19s 超时 vs 换上下文后 0.6s 拿到上游响应）。成因见 `psi_agent/_tls.py` |
| 为什么走 `client_args` | 它进的是 provider 的 client 构造，不是请求体。any-llm 内部自己 new httpx client，从 `**body` 传 `http_client` 只会变成发给上游的 JSON 字段 |
| 为什么按 provider 挑 | OpenAI 系（`BaseOpenAIProvider`）与 Anthropic 系（`BaseAnthropicProvider`）的 SDK 收 `http_client`；Gemini（google-genai）与 Mistral **不收**，无条件传过去会当场 `TypeError`，等于为修一条路把另外几条弄断。不适用的 provider 拿 `None`，走 any-llm 默认 |
| 为什么建一次 | 每请求新建等于扔掉连接池，每次对话白付一轮 TCP + TLS 握手；而这个进程全程只对一个上游说话。进程退出时 `aclose()` |
| 为什么不设 timeout | 超时由上层（Channel / Gateway）决定，这里设了会把长回答截断 |

`handle_chat_completions` 用 `request.app.get("http_client")` 而非 `[...]`：该 handler 也被不经 `serve_ai` 装配的 app 用（测试、将来的嵌入式用法），少一个键不该变成 500。

## 错误处理

- **HTTP 层**（`response.prepare()` 之前）：返回 OpenAI 格式 `{"error": {...}}` JSON + HTTP 4xx/5xx
- **SSE 层**（`response.prepare()` 之后）：`make_error_chunk()` 构造 error chunk → `finish_reason="error"`（psi-agent 内部扩展，非 OpenAI 标准；构造函数在 `psi_agent/protocol.py`，前缀 `[Upstream Error]: ` 由本层拼好后传入）
- **取消/断开安全**：上游 stream 在 `finally` 中用 `anyio.CancelScope(shield=True)` 调 `stream.aclose()` 关闭（`getattr` 守卫兼容无 `aclose` 的流），确保客户端断开 / 进程关闭被 cancel 时不泄露上游连接

## 回合标记（模型耗时的权威判据）

`handle_chat_completions` 的两端是**模型墙上时间的唯一权威来源**，都是 INFO（生产钉死 INFO，放 DEBUG 等于没做）：

| 标记 | 时机 |
|---|---|
| `ai-turn open` | 请求体解析成功、即将转发上游 |
| `ai-turn close elapsed_ms=<N> outcome=<结局>` | 唯一出口。`outcome` ∈ `ok` / `upstream_error` / `client_disconnect` / `prepare_failed` |
| `ai-turn rejected` | 请求体没解析出来。**没有配对的 open**，不进配平计数 |

- **两端计数必须相等**，用例钉住了包括 `response.prepare` 失败在内的每条 return 路径。三条终态日志收成一条出口，于是「配平」只需数两个词，将来多一种结局也不会让脚本漏计一个 close。
- **不要去补 `agent.py` 的标记。** 实测 2,331 个回合里 241 个（10%）只有 AI 侧、没有 agent 侧标记，据此算出模型耗时占比 39.2%，正确值 63.4%——差 24 个百分点且系统性偏低（掉的那批恰好是走特殊分支的慢回合）。选这一侧作权威是因为**配平在这里是结构性的**：所有上游调用必经这个 handler，open/close 各一次可由一个函数的控制流锁死；放在 `agent.py` 要靠人自觉，下次新加分支又会静默失衡。另外这两端量的正是想要的东西（上游墙上时间），`agent.py` 那一对还含 Session 自己的历史读写。
- `"Sending request to AI via AiClient"` 保留用于观测**发起**，不得用来配对算耗时。
- 每行还带**会话 id**（第三列，见根 `AGENTS.md`「日志约定」）。值取自请求体 `routing.session_id`——AI 是 socket 后面另一个进程，Session 侧 ContextVar 过不来。
- 改这几个标记文本要同步 `scripts/latency-probe/parse.py`（它按 logger 名 + 消息文本匹配，刻意不含行号）。

## Context Compaction

AI 层强制 `stream_options={"include_usage": True}` 获取上游 token 用量。当 `chunk.usage.prompt_tokens > max_context_tokens`（0 禁用），在上游 stream 结束后发送 **额外 SSE 事件** 通知 Session 触发 compaction。

信号由 `psi_agent.protocol.make_compaction_signal(prompt_tokens=…, threshold=…)` 构造，形状见根 `AGENTS.md`「核心通信协议」。`prompt_tokens` / `threshold` 不是日志字段——Session 用它们做压缩冷却判断（见 `session/AGENTS.md`），省略会让冷却退化成 fail-open。

`psi_compaction` 是 psi-agent 内部扩展字段，非 OpenAI 标准。仅 OpenAI / Anthropic / Gemini 及兼容 provider 支持 `usage` 返回；Groq / Mistral / Ollama 等 strip `stream_options`，compaction 不触发。

`max_context_tokens` 除 CLI / 环境变量外，也可经 Gateway `POST /ais` 的同名 body 字段
按 AI 后端配置（见 `gateway/AGENTS.md`）。**阈值应显著小于模型真实上下文窗口**：压缩
改不了 system prompt 体积，压缩本身也要发一次请求，阈值贴太近会从「压得太频繁」变成
「上游直接拒绝」。

## 依赖

- `any-llm-sdk`：多 provider 客户端
- `aiohttp`：HTTP/SSE server + client
- `anyio`：异步 runtime
