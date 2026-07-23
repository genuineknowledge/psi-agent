# 语义大模型路由实施记录

> 本文件由最初的实施计划同步为当前代码的落地记录。实际行为以 `src/psi_agent/router/` 和对应测试为准。

## 落地目标

新增顶层 `psi-agent router` 服务：利用一个已启动的普通 AI 服务，根据候选模型的描述选择目标 socket，并透明代理原始 Chat Completions/SSE 请求；路由失败时使用独立的默认 socket。

## 最终文件映射

- `src/psi_agent/router/models.py`：不可变的 `Upstream`、`RouteDecision` 类型，以及严格的重复 `--upstream` JSON 解析。
- `src/psi_agent/router/prompts.py`：独立维护中文提示词，只插入候选索引和 description，并处理当前任务与历史上下文的关联性。
- `src/psi_agent/router/selector.py`：序列化受限上下文，通过路由模型的 Chat Completions SSE 聚合决策，并校验候选索引。
- `src/psi_agent/router/server.py`：选择目标、执行逐请求默认回退、原样转发请求 body、按 bytes 代理 SSE。
- `src/psi_agent/router/__init__.py`：`Router` dataclass、启动参数校验、服务创建与可取消生命周期。
- `src/psi_agent/router/AGENTS.md`：Router 的安全、协议、日志和维护约束。
- `src/psi_agent/cli.py`：将 `Router` 直接加入顶层 tyro union。
- `tests/psi_agent/router/`：模型、prompt、selector、server 和生命周期单元测试。
- `tests/psi_agent/test_cli.py`：顶层 `ai`/`router` CLI 形状测试。
- `tests/integration/test_semantic_router.py`：全部使用本地 aiohttp 服务的端到端选择、回退和 SSE 保真测试。

## 已实现任务

### 1. 候选配置与 CLI

- [x] `Router` 命令名固定为顶层 `psi-agent router`，普通 AI 仍为 `psi-agent ai`。
- [x] `Router` 字段为 `session_socket`、`router_socket`、`upstream`、`default_socket`、`router_timeout`、`router_context_chars` 和 `verbose`。
- [x] 删除独立的 `log_router_details`；默认输出路由理由和结果，`verbose` 只负责开启额外 DEBUG 日志。
- [x] 每个 `upstream` 是仅包含 `socket`、`description` 的严格 JSON object；不再包含模型名、provider 或 API 配置。
- [x] `default_socket` 是独立地址，不要求出现在候选列表。
- [x] `router_socket` 指向已启动的普通 AI 服务，并且不要求出现在候选列表。

### 2. Prompt 与上下文

- [x] 提示词独立放在 `prompts.py`，使用中文维护。
- [x] Prompt 仅暴露候选序号和 description，不暴露 socket、默认地址或密钥。
- [x] Prompt 先判断最新任务与上文的关联性：相关时判断整体任务类型，不相关时只判断最新任务。
- [x] 上下文保留首个 system、user/assistant 文本和 assistant 工具名，省略工具参数与工具结果正文，并为多模态内容生成标记。
- [x] 上下文按 `router_context_chars` 限制，优先保留 system 与最近区块；没有可用 user 内容时不请求路由模型。

### 3. 路由模型调用与决策

- [x] Router 通过 `router_socket` 调用普通 AI 的 `/chat/completions`，请求使用 `model="router"` 和 `stream=true`。
- [x] 聚合单 choice SSE 的 `delta.content`；0 choice 跳过，多 choice、错误 finish reason 或不可信结构转为 `RouterSelectionError`。
- [x] 接受纯 JSON、fenced JSON 和说明文字中的有效 JSON object。
- [x] 只接受范围内、非 bool 的整数候选索引；socket 映射始终在本地完成。
- [x] 可用 `router_timeout` 为单次选择设置有限正数超时。

### 4. 请求转发、回退与 SSE

- [x] 语义选择成功后，将原始请求 body 原样转发到候选 socket，不改写 `model`。
- [x] 缺少用户上下文或路由失败时，将同一个原始 body 原样转发到 `default_socket`。
- [x] 默认上游失败是终态，不递归回退。
- [x] 普通上游 SSE 使用 `iter_any()` 逐块转发原始 bytes，不重建 chunk。
- [x] 上游非 200 在 prepare 前转换为 HTTP 502；prepare 后异常转换为内部 `finish_reason="error"` chunk。
- [x] HTTP/HTTPS、Unix socket 和 Named Pipe 均复用 `_sockets.py`；TCP 地址作为 base address，并统一追加 `/chat/completions`。

### 5. 生命周期与日志

- [x] `Router.run()` 首行调用 `setup_logging(verbose=self.verbose)`。
- [x] aiohttp runner 在启动失败和 shutdown 时均 shield cleanup。
- [x] 默认 INFO 只输出 `Router reason` 和最终 `Router result`；恢复性路由失败为 WARNING、代理失败为 ERROR、SSE chunk 为 DEBUG。
- [x] 请求和 response 保持在异步上下文管理器中，客户端断开或服务取消时释放连接。

### 6. 测试与验证

- [x] `test_models.py`：严格 schema、非法 JSON、顺序与索引映射。
- [x] `test_prompts.py` / `test_selector.py`：信息边界、上下文、解析、SSE、HTTP 错误和超时。
- [x] `test_server.py`：完整 body/model 透传、默认回退、SSE bytes、HTTP/流式错误和日志摘要。
- [x] `test_router.py` / `test_cli.py`：参数校验、logging-first、cleanup 和顶层命令形状。
- [x] `test_semantic_router.py`：本地多服务语义选择、默认回退和 tool-call SSE 保真。

## 与初始计划相比的最终调整

1. Router 最终是顶层命令，而不是 `psi-agent ai router` 子命令，也没有 `parse_command()` 特判。
2. `upstream` 最终只保存 `socket` 与 `description`，候选模型名不会进入 Router 配置。
3. 语义选择成功和默认回退都保留原请求 `model`；候选服务自身负责用启动时配置的模型覆盖它。
4. `router_socket` 可完全独立于候选模型，不需要落入 `upstream`。
5. 共享 `_sockets.py` 不处理“已含完整 endpoint 时避免重复”的分支；调用者必须提供 base address。
6. 路由理由与最终 socket 始终输出，额外细节仅由 `verbose` 控制。

## 当前验证命令

```text
uv run pytest tests/psi_agent/router tests/integration/test_semantic_router.py tests/psi_agent/test_cli.py -v
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run psi-agent router --help
uv run psi-agent ai --help
```
