# AI 层设计文档

## 概述

AI 层是一个统一的多 provider LLM 客户端，通过 Unix socket 对外提供 OpenAI-compatible HTTP/SSE 服务。

核心能力：
- 接收 OpenAI Chat Completions 格式的 HTTP 请求
- 使用 [any-llm-sdk](https://github.com/mozilla-ai/any-llm) 转发到任意 LLM provider
- 透传 SSE 流式响应（含 Anthropic→OpenAI 格式转换）
- 错误统一处理（HTTP 非流式 + SSE 流式）

## 架构

```
Session ── POST /v1/chat/completions ──► AI (Unix socket)
                                            │
                                            │ any_llm.acompletion()
                                            ▼
                              OpenAI / Anthropic / Gemini / ...
```

单一入口：`psi-agent ai --provider <name> --model <name> --api-key <key> --base-url <url>`。

## 模块

| 文件 | 职责 |
|------|------|
| `__init__.py` | `AiBackend` dataclass + `run()` + `serve_ai_backend()` |

| `server.py` | `ErrorResponse` + `handle_chat_completions()` |

## 数据流

```
1. CLI → AiBackend.run()
2. run() → serve_ai_backend(provider, model, api_key, base_url, handler)
3. serve_ai_backend → aiohttp UnixSite + 注册 handler
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

全部参数可选，CLI 优先于环境变量。`model` 在请求处理中被启动配置覆盖（AI 层隐藏上游 model 细节）。

## 请求透传

Session 发送的 body 中，除 `model` 被启动配置覆盖、`messages` 和 `tools` 被显式提取外，其余字段（`temperature`, `max_tokens` 等）全部通过 `**body` 透传给 any-llm-sdk。

## Provider 支持

any-llm-sdk 原生支持的 50+ provider 全部可用，无需额外代码。包括：OpenAI, Anthropic, Gemini, DeepSeek, Mistral, Groq, Ollama, Cerebras, Cohere, Perplexity, Fireworks, Together, xAI, Bedrock, Azure, VertexAI 等。

Anthropic→OpenAI 格式转换由 any-llm-sdk 自动完成，包括 `thinking_delta`→`reasoning_content`、`input_json_delta`→`tool_calls`、`content_block_stop`→`finish_reason="tool_calls"`。

## 错误处理

- **HTTP 层**（`response.prepare()` 之前）：`ErrorResponse` → `web.json_response(status=4xx/5xx)`
- **SSE 层**（`response.prepare()` 之后）：ChatCompletionChunk error chunk → `finish_reason="error"`

## 依赖

- `any-llm-sdk >= 1.17`：多 provider 客户端
- `aiohttp`：Unix socket HTTP server
- `anyio`：异步 runtime

## 测试

- `tests/psi_agent/ai/test_common.py`：ErrorResponse 测试
- `tests/psi_agent/ai/test_ai_backend.py`：AiBackend dataclass 测试
- 集成测试通过 `tests/integration/` 覆盖全链路
