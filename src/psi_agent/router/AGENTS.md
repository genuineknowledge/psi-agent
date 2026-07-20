# Router 层设计文档

## 定位与拓扑

Router 是可选的、无状态的 AI-compatible 服务，位于 Session 与多个普通 AI 服务之间。每个存在可用用户内容的请求最多进行一次语义选择，然后把原始 Chat Completions 请求代理到一个候选 socket；无法完成选择时代理到 `default_socket`。

```text
Session ── POST /chat/completions ──▶ Router
                                      ├── 路由决策 ──▶ router_socket（普通 AI）
                                      └── 原请求代理 ─▶ 候选 socket / default_socket（普通 AI）
```

路由模型和业务候选模型都按普通 `psi-agent ai` 服务启动。Router 不创建模型，不持有 provider、模型名、base URL 或 API key。`router_socket` 可以独立于候选列表，不要求同时出现在 `upstream` 中。

## 文件职责

| 文件 | 职责 |
|------|------|
| `__init__.py` | `Router` CLI dataclass、配置校验、`serve_router()` 和生命周期 |
| `models.py` | `Upstream`、`RouteDecision` 与严格 upstream JSON 解析 |
| `prompts.py` | 中文路由提示词和 description-only 候选列表 |
| `selector.py` | 上下文序列化、路由 AI SSE 调用和候选决策解析 |
| `server.py` | HTTP handler、逐请求回退、原请求转发和 SSE bytes 代理 |

## CLI 与配置不变量

命令固定为顶层 `psi-agent router`。`Router` 字段和含义如下：

- `session_socket`：Router 自身监听地址。
- `router_socket`：用于做语义判断的已启动普通 AI 服务地址。
- `upstream`：重复 JSON 字符串，每项必须恰好包含非空的 `socket` 和 `description`。
- `default_socket`：路由失败时的直接服务地址，不要求属于候选列表。
- `router_timeout`：可选的有限正数秒数。
- `router_context_chars`：正整数，默认 `12000`。
- `verbose`：开启 DEBUG 日志。

不得重新加入 `model_name`、provider、base URL、API key 或 `log_router_details`。路由理由与最终 socket 是默认 INFO 输出，不需要额外开关。

所有地址使用 `psi_agent._sockets` 的统一规则。HTTP/HTTPS 输入必须是 base address，helper 会无条件追加 `/chat/completions`；不要为“输入已含完整 endpoint”增加 Router 私有兼容分支。

## 安全边界

路由模型只能看到：

- 经过裁剪和脱敏的对话上下文；
- 从 0 开始的候选索引；
- 每个候选的 `description`。

任何 prompt 修订都禁止包含候选 socket、`default_socket`、API key 或其他服务凭据。路由响应只能选择整数索引，索引到 socket 的映射必须留在经过验证的本地配置中；绝不接受路由模型生成的地址。

## 上下文与 Prompt

`selector.serialize_context()`：

- 保留首个 system、user 和 assistant 文本；
- assistant tool call 只保留工具名，省略参数；
- tool result 只保留存在标记，省略正文；
- 图片、音频、文件用短标记替代；
- 优先删除旧的非 system 区块，并保证结果不超过 `router_context_chars`；
- 没有可用 user 内容时返回空字符串，使 server 直接走默认 socket。

所有路由提示词必须集中在 `prompts.py` 的 `ROUTING_SYSTEM_PROMPT`。提示词要先判断最新用户任务与上文是否相关：相关时按整体任务类型路由，不相关时忽略上文，仅按最新任务路由。候选插值只能使用索引和 description。

## 路由模型协议

`selector` 向 `router_socket` 发送 `model="router"`、`stream=true` 的标准 Chat Completions 请求。目标普通 AI 会用自身启动配置覆盖该模型值。

路由 SSE 必须遵循显式单 choice 约定：0 choice 静默跳过，恰好 1 个 choice 才处理，多 choice 或不可信结构失败；`finish_reason="error"` 直接转换为 `RouterSelectionError`。仅聚合文本 `delta.content`。

解析器可以从纯 JSON、fenced JSON 或说明文字中寻找第一个有效 object。`candidate` 必须是候选范围内的整数且不能是 bool；`reason` 仅用于诊断，不得影响 socket 选择。

## 请求与回退不变量

- 语义选择成功时，复制并原样转发完整请求 body 到候选 socket。
- 路由失败、超时、输出损坏或缺少用户上下文时，将同一原始 body 转发到 `default_socket`。
- 两条路径都必须保留调用方原始 `model`，不得在 Router 内改写模型名。
- 未知 Chat Completions 字段必须保留。
- 回退仅作用于当前请求；默认上游失败是终态，禁止递归回退。
- 正常业务 SSE 必须按 bytes 代理，禁止重建 content、reasoning、tool-call、provider extension 或 `[DONE]` chunk。
- prepare 前的业务上游非 200 转换为 HTTP 502；prepare 后的代理失败使用内部 `finish_reason="error"` chunk。

普通 AI 服务最终会按自己的启动配置覆盖原请求 `model`。该职责属于 AI 层，Router 不应复制这套逻辑。

## 生命周期与日志

所有 I/O 使用 aiohttp 和 anyio。ClientSession、上游 response 必须位于异步上下文管理器中，使取消和客户端断开能够释放连接。aiohttp runner 在启动失败和 shutdown 时均使用 shielded cleanup。`Router.run()` 的第一行可执行语句必须是 `setup_logging(verbose=self.verbose)`。

日志级别约定：

- INFO：启动/关闭、请求进入、客户端断开，以及每次请求的 `Router reason` 和 `Router result`。
- WARNING：可恢复的语义选择失败、业务上游非 200。
- ERROR：无法代理的业务上游异常。
- DEBUG：解析后的配置和每个代理 SSE bytes chunk，由 `--verbose` 开启。

默认选择摘要只能包含路由理由和最终 socket；不得在 INFO 中加入 description、候选索引、上下文长度、原始上下文或密钥。

## 测试要求

任何行为修改都应同步镜像目录下的单元测试；跨路由模型、候选 AI、Session 或 SSE 的修改还要补 `tests/integration/`。重点保持以下回归覆盖：

- upstream 严格 schema 和候选顺序；
- prompt 不泄露 socket/密钥；
- 上下文关联性规则、截断和工具/多模态脱敏；
- 单 choice SSE、损坏响应、错误 finish reason 和超时；
- 选择成功与回退均保留完整 body/model；
- SSE bytes 保真、HTTP 502、流式 error chunk 和连接清理；
- 默认日志只返回理由与最终结果；
- `Router.run()` logging-first 与启动失败 cleanup；
- 顶层 `psi-agent router` CLI 形状。

建议验证：

```text
uv run pytest tests/psi_agent/router tests/integration/test_semantic_router.py tests/psi_agent/test_cli.py -v
uv run ruff check src/psi_agent/router tests/psi_agent/router
uv run ruff format --check src/psi_agent/router tests/psi_agent/router
uv run ty check
uv run psi-agent router --help
```
