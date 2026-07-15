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
3. serve_ai → aiohttp UnixSite + 注册 handler
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

Session 发送的 body 中，除 `model` 被启动配置覆盖、`messages` 被显式提取、`routing` 被视为内部元数据而剥离、`stream` 被剥离（AI 层始终强制 `stream=True`）、`provider`/`api_key`/`api_base` 防御性剥离（避免与启动配置冲突）外，其余字段（`tools`, `temperature`, `max_tokens` 等）全部通过 `**body` 透传给 any-llm-sdk。

## Provider 支持

any-llm-sdk 原生支持的 50+ provider 全部可用，无需额外代码。包括：OpenAI, Anthropic, Gemini, DeepSeek, Mistral, Groq, Ollama, Cerebras, Cohere, Perplexity, Fireworks, Together, xAI, Bedrock, Azure, VertexAI 等。

Anthropic→OpenAI 格式转换由 any-llm-sdk 自动完成，包括 `thinking_delta`→`reasoning`、`input_json_delta`→`tool_calls`、`content_block_stop`→`finish_reason="tool_calls"`。

## 错误处理

- **HTTP 层**（`response.prepare()` 之前）：返回 OpenAI 格式 `{"error": {...}}` JSON + HTTP 4xx/5xx
- **SSE 层**（`response.prepare()` 之后）：ChatCompletionChunk error chunk → `finish_reason="error"`（psi-agent 内部扩展，非 OpenAI 标准）
- **取消/断开安全**：上游 stream 在 `finally` 中用 `anyio.CancelScope(shield=True)` 调 `stream.aclose()` 关闭（`getattr` 守卫兼容无 `aclose` 的流），确保客户端断开 / 进程关闭被 cancel 时不泄露上游连接

## 依赖

- `any-llm-sdk`：多 provider 客户端
- `aiohttp`：HTTP/SSE server + client
- `anyio`：异步 runtime

## Router Demo

除单上游 `Ai` 外，AI 层还提供基于 `llmrouter-lib==0.3.1` 的 `AiRouter`。Router 使用一个远程小模型分析有界对话上下文，只选择一个候选 upstream，再原样代理 OpenAI Chat Completions 请求和 SSE 响应。

PowerShell TCP 启动示例：

```powershell
uv run psi-agent ai router `
  --session-socket "http://127.0.0.1:8100" `
  --router-model "qwen-turbo" `
  --router-base-url "https://router.example/v1" `
  --router-api-key "sk-router" `
  --upstream `
    '{\"addr\":\"http://127.0.0.1:8101\",\"model\":\"qwen-plus\",\"description\":\"通用中文问答和总结\"}' `
    '{\"addr\":\"http://127.0.0.1:8102\",\"model\":\"deepseek-reasoner\",\"description\":\"复杂推理和代码分析\"}' `
  --default-model "qwen-plus"
```

- `--upstream` 是一个 `list[str]` 参数：只写一次，后面每行提供一个独立 JSON object；不再接受带外层 `[...]` 的完整 JSON 数组。
- 每个 JSON object 必须且只能包含非空的 `addr`、`model`、`description`；候选模型名不能重复。
- Windows PowerShell 调用原生 `uv.exe` 时可能剥离 JSON 双引号，因此示例使用 `\"` 保留内部引号。
- 候选顺序有意义：未指定 `--default-model` 时，路由失败或超时会降级到第一个候选，即 `upstream[0]`。
- 请求体显式指定已知候选 `model` 时跳过 LLMRouter；未知或缺失模型时才根据上下文路由。
- Router 序列化 system、user、assistant 和工具名称，但省略工具参数与工具结果正文；字符预算由 `--router-context-chars` 控制。
- `--router-timeout` 省略时无限等待；传入有限正数时，超时只降级到默认候选，不重试其他模型。
- Router 剥离内部 `routing` 元数据，设置选中模型的 `model`，其余请求字段及 `content`、`reasoning`、`tool_calls`、`[DONE]` SSE 数据保持透传。
- 默认只在 DEBUG 日志记录投票；`--log-router-details` 额外记录 LLMRouter 返回的子问题和路由结果。

### LLMRouter prompt 资源

`llmrouter-lib==0.3.1` 的 prompt loader 会从模块级私有变量 `_PROJECT_ROOT` 和 `_CUSTOM_TASKS_DIR` 推导自定义模板目录。作为第三方依赖安装时，它的默认目录不指向 psi-agent，因此 `LLMRouterAdapter` 在同步 worker 中将这两个全局变量设置为包内 `psi_agent.ai/custom_tasks`，再构造 `LLMMultiRoundRouter`。设置路径、构造 Router 与后续路由调用共用进程级锁，避免并发访问第三方全局状态。

以下三个 YAML 是运行时必需的包资源，启动时缺失任意一个都会直接失败：

- `custom_tasks/agent_decomp_route.yaml`
- `custom_tasks/agent_decomp_cot.yaml`
- `custom_tasks/agent_prompt.yaml`

该接入依赖 LLMRouter 0.3.1 的私有 prompt 全局变量。升级 `llmrouter-lib` 时必须重新验证变量名称、模板名称、延迟加载行为以及 wheel 中的 YAML 包含情况；禁止通过修改 `.venv/site-packages` 或启动时复制模板来掩盖兼容性问题。

### 运行时模型数据

Adapter 根据命令行生成两份运行时数据：`llm_data.json` 只包含 `--upstream` 提供的候选模型，key 和 `model` 来自候选 `model`，`feature` 来自 `description`；`runtime.yaml` 的 `base_model` 和 `api_endpoint` 分别来自 `--router-model` 与 `--router-base-url`。API key 只在进程锁内临时写入 `API_KEYS`，不得进入 YAML 或 JSON。

LLMRouter 0.3.1 在 `llm_data` 非空但不包含 `base_model` 时不会回退到 YAML 的 `api_endpoint`。为兼容该缺陷，Adapter 先用候选专用 JSON 构造 Router，让候选 prompt 不包含独立路由模型；构造完成后再向实例内存的 `llm_data[router_model]` 注入 `model` 和 `api_endpoint`。若路由模型与某个 upstream 同名，则保留该候选的 `feature`，只补充 endpoint。升级 LLMRouter 时必须重新检查该两阶段注入是否仍有必要。
