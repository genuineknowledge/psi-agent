# AI 层设计文档

## 定位

AI 层是无状态的多 provider LLM 适配服务。它对 Session、Router 等调用方统一暴露 `POST /chat/completions`，再通过 `any-llm-sdk` 调用启动时指定的 provider/model，并以 OpenAI-compatible SSE 返回结果。

```text
Session 或 Router ── POST /chat/completions ──▶ AI
                                             │
                                             ▼
                                  any_llm.acompletion()
                                             │
                                             ▼
                              OpenAI / Anthropic / Gemini / ...
```

普通模型与“负责做路由判断的小模型”使用完全相同的 `Ai` 服务创建和启动方式。AI 层不知道自己是否被 Router 用作路由模型，也不管理候选列表。语义路由由独立的顶层 `psi-agent router` 服务实现，详见 `src/psi_agent/router/AGENTS.md`。

## 文件职责

| 文件 | 职责 |
|------|------|
| `__init__.py` | `Ai` dataclass、配置解析、`serve_ai()` 和 aiohttp 生命周期 |
| `server.py` | `handle_chat_completions()`、any-llm 调用、SSE 输出和错误处理 |

## 启动配置

入口为：

```text
psi-agent ai --session-socket <addr> --provider <name> --model <name> --api-key <key> --base-url <url>
```

| 字段 | CLI | 环境变量 | 说明 |
|------|-----|----------|------|
| `session_socket` | `--session-socket` | 无 | AI 服务监听地址，支持 HTTP/HTTPS、Unix socket 和 Windows Named Pipe |
| `provider` | `--provider` | `PSI_AI_PROVIDER` | any-llm-sdk provider key |
| `model` | `--model` | `PSI_AI_MODEL` | 实际调用的上游模型名 |
| `api_key` | `--api-key` | `PSI_AI_API_KEY` | 上游 API key |
| `base_url` | `--base-url` | `PSI_AI_BASE_URL` | 上游 API base URL |
| `verbose` | `--verbose` | 无 | 开启 DEBUG 日志 |

除 `session_socket` 外的字符串字段允许从环境变量回退，显式 CLI 值优先。`Ai.run()` 的第一行可执行语句必须是 `setup_logging(verbose=self.verbose)`。

## 请求与模型覆盖

`handle_chat_completions()` 从请求中提取 `messages`，移除调用方传入的 `stream`、`provider`、`model`、`api_key` 和 `api_base`，然后调用：

```python
await acompletion(
    provider=启动配置,
    model=启动配置,
    messages=messages,
    stream=True,
    api_key=启动配置,
    api_base=启动配置,
    **其余请求字段,
)
```

因此 AI 服务的实际 provider/model/API 配置始终以进程启动配置为准。包括 Router 在内的调用方可以保留自己的 `model` 字段，但它不会覆盖 AI 服务的实际模型。除上述保留字段外，`tools`、`tool_choice`、采样参数、token 限制及未知扩展字段全部透传给 any-llm-sdk。

AI 始终强制 `stream=True`，逐个把 `ChatCompletionChunk.model_dump_json()` 写成 SSE `data:` 事件。正常响应由 any-llm-sdk 负责把不同 provider 的流式格式统一为 OpenAI-compatible chunk，包括 reasoning、tool calls 和 finish reason。

## 与 Router 的关系

- Router 的 `router_socket` 指向一个已经启动的普通 AI 服务；该 AI 的 provider/model/base URL/API key 仍由 `psi-agent ai` 配置。
- Router 给该 AI 发送固定请求模型值 `"router"`，但 AI 层会丢弃它并使用自身启动模型。
- Router 的候选 socket 和 `default_socket` 同样指向普通 AI 服务。
- Router 将业务请求 body 原样转发；候选 AI 随后按本节规则用自己的启动模型覆盖请求中的 `model`。
- AI 层不读取候选 description，不执行语义选择，也不需要为 Router 增加专用 provider adapter。

## 错误、取消与资源清理

- 请求 JSON 解析失败：在 prepare 前返回 OpenAI 风格 HTTP 400。
- 上游调用或流消费失败：在已经建立的 SSE 中发送 `finish_reason="error"` 的内部 error chunk。
- 下游断开：记录取消状态，不继续向下游写入。
- 上游 stream 无论正常、异常或取消都会在 `finally` 中检查 `aclose()`，并用 `anyio.CancelScope(shield=True)` 保护关闭过程。
- `serve_ai()` 在 setup/start 失败及正常 shutdown 时都 shield `runner.cleanup()`，然后继续传播原异常或取消。

日志约定与项目一致：请求生命周期为 INFO，参数解析、透传字段和每个 SSE chunk 为 DEBUG，可恢复关闭异常为 WARNING，上游失败为 ERROR。日志不得输出 API key 原文。

## 维护检查

修改 AI 层时重点验证：

```text
uv run pytest tests/psi_agent/ai -v
uv run ruff check src/psi_agent/ai tests/psi_agent/ai
uv run ruff format --check src/psi_agent/ai tests/psi_agent/ai
uv run ty check
uv run psi-agent ai --help
```

若修改会改变 Router 调用普通 AI 的协议，还必须同步 `src/psi_agent/router/AGENTS.md` 和 Router 集成测试。
