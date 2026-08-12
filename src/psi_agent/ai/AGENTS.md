# AI 层设计文档

## 概述

AI 层是一个统一的多 provider LLM 客户端，对外提供 OpenAI-compatible HTTP/SSE 服务。

内部组件调用携带 `X-Psi-Trace-Id`；AI 服务规范化并在 SSE 响应头回显，用于与 Session/Router 日志关联。
`routing` 仍须在调用外部 Provider 前整体删除，因此 trace_id 不会作为模型参数泄漏到供应商。

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
| `__init__.py` | `Ai` dataclass + `run()` + `serve_ai()` |

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

## Provider 支持

any-llm-sdk 原生支持的 50+ provider 全部可用，无需额外代码。包括：OpenAI, Anthropic, Gemini, DeepSeek, Mistral, Groq, Ollama, Cerebras, Cohere, Perplexity, Fireworks, Together, xAI, Bedrock, Azure, VertexAI 等。

Anthropic→OpenAI 格式转换由 any-llm-sdk 自动完成，包括 `thinking_delta`→`reasoning`、`input_json_delta`→`tool_calls`、`content_block_stop`→`finish_reason="tool_calls"`。

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
