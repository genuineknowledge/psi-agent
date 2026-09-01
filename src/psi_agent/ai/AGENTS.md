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

全部参数可选，CLI 优先于环境变量。`model` 在请求处理中被启动配置覆盖（AI 层隐藏上游 model 细节）。

## 请求透传

Session 发送的 body 中，除 `model` 被启动配置覆盖、`messages` 被显式提取、`stream` 被剥离（AI 层始终强制 `stream=True`）、`provider`/`api_key`/`api_base`/`routing` 防御性剥离（避免与启动配置冲突）外，其余字段（`tools`, `temperature`, `max_tokens` 等）全部通过 `**body` 透传给 any-llm-sdk。

`reasoning_effort` 是唯一被**补默认值**的字段（`setdefault("reasoning_effort", "medium")`，调用方给了就用它的，含 `"none"`）。

### 为什么必须显式传 `reasoning_effort`

any-llm 的 DeepSeek provider 把缺省值 `"auto"` 读成「调用方没要思维」，转而下发 `extra_body.thinking={"type": "disabled"}`（见 1.26.0 的 `providers/deepseek/deepseek.py`）。DeepSeek V4 官方默认是**开**思维，any-llm 为对齐旧版 `deepseek-chat` 行为主动反转成关。

后果不是「字段丢了」而是「思维根本没生成」：模型被关掉思维通道后仍要推理，就把自我对话写进 `content` —— 即线上的 thinking 泄漏（复述提问 + 自问自答）。实测同一 prompt：不传 = 0/9 个 chunk 带思维链，传 `"medium"` = 24/33。

该默认值在 1.21.0 之后引入（1.21.0 的同一文件里没有 `thinking` 分支），而依赖声明是 `any-llm-sdk>=1.21.0` —— 一次静默的上游行为变更改掉了线上语义。

**ToC 装机版不受影响，但那是巧合**：它与 ToB 共用同一份代码与同一个 any-llm，只因 `spa-v2/src/services/bootstrapAi.ts` 的 `DEFAULT_REMOTE_AI` 配 `provider: 'openai'`（打的是云端 OpenAI 兼容网关）而绕开了这个默认值。改成 `deepseek` 泄漏立刻出现。

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
