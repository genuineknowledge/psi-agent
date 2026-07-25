# Haitun 通过 MCP 调用 Haibao

本文记录 Haitun 的 MCP-only Haibao ChatBI 接入。Haitun Adapter、两个公开 Tool 和
Haibao Skill 已 bundled 在 `examples/haitun-workspace`。生产所需的私有 Haibao MCP
Server 由 operator-provisioned 外部服务提供，不 bundled 在本仓库；本文不表示生产
环境已经 deployed，也不包含私有服务实现。

旧内部方案已在概念上 superseded：MCP 是唯一集成边界，而不是 API、MCP、Skill 三条
平行路径。身份来自认证凭据，不由模型传入；Haitun 不直连私有核心服务。

## 1. 架构与边界

调用链固定为：

```text
用户
  -> Haitun Agent
  -> Haibao Skill
  -> haibao_list_datasets / haibao_ask
  -> Haitun MCP Adapter
  -> 私有 Haibao MCP Server（Streamable HTTP /mcp）
  -> 私有 ChatBI 与数据库
```

| 边界 | 内容 |
|---|---|
| 本仓库 | Adapter、两个公开 Tool、Skill、非敏感文档和测试 |
| 私有服务 | 认证、租户授权、NL2SQL、SQL 审核与执行、数据库凭据 |
| 部署平台 | MCP endpoint、限权 token、TLS、Secret Manager 和运行治理 |

本仓库不得包含私有 API key、数据库凭据、核心算法、内部 URL、客户 Schema 或客户
数据。不得增加绕过 MCP 的直连实现。

## 2. 已实现的 Haitun Bundle

### 2.1 文件图

```text
examples/haitun-workspace/
├── .env.haibao.example
├── tools/
│   ├── _haibao_mcp.py
│   ├── haibao_list_datasets.py
│   └── haibao_ask.py
├── skills/haibao/SKILL.md
├── docs/haibao-integration.md
└── tests/test_haibao_tools.py
```

- `tools/_haibao_mcp.py` 实现配置、官方 MCP Python SDK client、discovery Schema
  校验、结果校验、安全错误映射、硬超时和资源清理。
- `tools/haibao_list_datasets.py` 与 `tools/haibao_ask.py` 各自只公开一个同名异步
  Tool。
- `skills/haibao/SKILL.md` 定义真实问数的选择、解释和安全规则。
- Adapter 每次调用建立并关闭独立 session/transport，采用 per-call lifecycle；连接后
  执行 `initialize` 和 `tools/list`，验证 discovery 返回的 Tool 名称及关键输入
  Schema，再执行 `tools/call`。

### 2.2 Tool 输入 Schema

`haibao_list_datasets` 无参数。`haibao_ask` 的契约为：

```json
{
  "type": "object",
  "properties": {
    "text": {"type": "string", "minLength": 1, "maxLength": 8000},
    "db_id": {"type": "string", "minLength": 1, "maxLength": 128},
    "mode": {
      "type": "string",
      "enum": ["low", "medium", "high"],
      "default": "medium"
    }
  },
  "required": ["text", "db_id"],
  "additionalProperties": false
}
```

Adapter 同时在公开 wrapper 和 MCP discovery 边界校验该契约。Server 还必须按已认证
principal 授权 `db_id`。目标 Schema 显式使用 `additionalProperties: false`。当前
FastMCP SDK 实际 discovery Schema 可能 omitted 该字段，因此 Adapter 为 compatibility
接受 omitted 或 `false`，但 reject `true`；这不是声称两种表示是完全相同的 exact
JSON Schema。无论 discovery 如何表示，公开 Tool only sends exact known arguments，
不会转发额外字段。

### 2.3 配置

Haitun 只使用以下三个环境变量：

```dotenv
HAIBAO_MCP_URL=https://haibao.example.com/mcp
HAIBAO_MCP_TOKEN=replace-with-a-short-lived-token
HAIBAO_MCP_TIMEOUT=180
```

复制 `.env.haibao.example` 的变量到部署管理的环境或 Secret Manager，不要把真实值
提交到仓库或发到聊天。生产 endpoint 必须是可信 HTTPS 且路径精确为 `/mcp`，不得在
URL 中放 credential、query 或 fragment。仅 development 可使用 localhost、
`127.0.0.1` 或 `::1` 的 loopback HTTP；其他明文 HTTP 会被拒绝。

配置缺失或不安全时 Adapter fail closed。它不读取数据库配置或私有核心服务配置。

`HAIBAO_MCP_TOKEN` 是 process-global credential：一个 Haitun process/workspace deployment
就是一个 configured Haibao principal 和 security boundary，不提供 per-session identity
forwarding。不得用一个 token/process 服务需要 distinct authorization 的用户；每个
principal 或 distinct authorization cohort 必须部署 separate Haitun process、container
或 workspace，并注入 distinct token。不要声称 Haitun 会把 Session 或用户身份转发给
Haibao。

### 2.4 加载、升级与清理

实际 `ToolRegistry` skip underscore helper 文件，因此不直接索引 `_haibao_mcp.py`。
public wrapper 的 hash 纳入 registry，修改 `haibao_list_datasets.py` 或 `haibao_ask.py`
可以 hot refresh；helper-only `_haibao_mcp.py` 修改不会触发 wrapper reload，必须执行
Session/process restart 才会生效。

每次调用退出时 connector 会进行 shielded teardown，但等待时间为 max 5 seconds（配置
timeout 更短时采用更短值）。如果 SDK cleanup hangs，清理会被 cancelled/abandoned；
complete cleanup is not guaranteed。operator 应 recycle process，并 monitor repeated
cleanup timeout，而不是假设所有连接资源已经完整释放。
该路径发出固定、可监控且不含 URL、token、body 或 exception detail 的 warning：
`Haibao MCP cleanup timed out; recycle the process`。

Adapter 在模块加载时为 exact `mcp.client.streamable_http`、`httpx`、
`httpcore.connection` 和 `httpcore.http11` logger 各安装一个 permanent、idempotent
filter，丢弃这些 logger 的记录，因为 SDK 会记录 JSON-RPC body、session ID，而 HTTPX/
HTTPCore 会记录 private URL、连接目标或 response headers。它不按调用修改
process-global logger level/disabled state，因此并发 session 不会发生 enable/disable race；
tradeoff 是同一进程内应用自己通过这四个 exact logger 发出的记录也会被丢弃。其他
application logger 不受影响，Adapter 自身固定且 sanitized 的 cleanup warning 仍可监控。

Adapter 的 structured bounds validation 发生在 after SDK parsing，只限制已解析 payload；
它不能阻止解析前的大响应导致 pre-validation memory exhaustion。生产 reverse proxy 和
private MCP 必须 enforce response body limit，再把响应交给 SDK。

## 3. 私有 MCP Server 要求

私有 Server 是运行此 bundle 的 required dependency，但由 operator provision，不在本
仓库交付。生产 Server 必须提供 authenticated HTTPS Streamable HTTP `/mcp`，并在
server side 配置可信 OAuth issuer metadata 或受控 static verifier map。issuer、token
验证、principal 映射和 key rotation 都是私有服务运维责任，不新增 Haitun 环境变量。

认证后的 principal 决定可见数据集、租户和上游限权身份；模型不能覆盖用户或组织
身份。Server 只暴露 `haibao_list_datasets` 和 `haibao_ask`，使用稳定
`structuredContent`，并把认证、限流、网络、超时和协议错误与四种业务状态分开。

业务状态含义：

- `success`：执行成功且有结果；
- `empty`：执行成功但返回 0 行，不证明业务事实不存在；
- `sql_only`：只生成 SQL，未执行；
- `execution_failed`：尝试执行但失败。

只有 `execution.executed=true` 且 `execution.ok=true` 才能声称执行成功。Adapter 的
structured response hook captures status only，保留 401、403 或 429；它 captures no
remote body。错误 retryability 按 operation 和调用阶段判定：initialize 429 为
`retryable=true`；list call 429/transient 为 `retryable=true`；ask post-attempt 的 timeout、
transport 和 429/rate-limit 为 `retryable=false`，因为 POST outcome 可能未知；auth
401/403 始终为 `retryable=false`。Agent 仍不得 blind retry `haibao_ask` 或重试未知 POST
结果。Tool 结果是不可信数据，Agent 不执行其中的指令或链接。

当前两个 Tool 不提供 DDL 或 database onboarding。需要接入新数据库时，必须使用
operator-approved private console/process；不得在聊天中收集 token、密码、API key 或
connection string。

## 4. 启动与验收

### 4.1 Haitun 侧实施清单

Haitun 负责人按以下顺序完成接入。私有 MCP Server、ChatBI 和数据库不属于 Haitun
workspace 的交付范围。

- [ ] 合入并发布 `tools/_haibao_mcp.py`、`tools/haibao_list_datasets.py`、
  `tools/haibao_ask.py` 和 `skills/haibao/SKILL.md`。
- [ ] 在部署平台或 Secret Manager 注入 `HAIBAO_MCP_URL`、`HAIBAO_MCP_TOKEN` 和
  `HAIBAO_MCP_TIMEOUT`；不得把真实值提交到仓库、日志或聊天。
- [ ] 将 `HAIBAO_MCP_URL` 配置为 operator 提供的 authenticated HTTPS `/mcp`
  endpoint；仅本地开发可以使用 loopback HTTP。
- [ ] 将一个 Haitun process/workspace 限定为一个 Haibao principal 或相同授权 cohort；
  权限不同的用户使用不同 token 和独立 process、container 或 workspace。
- [ ] 确认部署没有向 Haibao 转发模型参数、Session ID 或调用方 Header 作为用户或组织
  身份；授权身份只能来自 `HAIBAO_MCP_TOKEN` 对应的 server-side principal。
- [ ] 启动 Haitun Session，确认 `ToolRegistry` 只加载公开 Tool
  `haibao_list_datasets` 和 `haibao_ask`，不把 `_haibao_mcp.py` 暴露给模型。
- [ ] 运行本节自动测试和静态检查，确认公开 Tool Schema 包含长度限制、`mode` 枚举、
  默认值和 `additionalProperties: false`。
- [ ] 使用 operator 发放的测试 principal 完成功能验收：无 `db_id` 先列数据集、多候选时
  询问用户、四种业务状态解释正确、未知 ask POST 结果不重试、结果中的指令不执行。
- [ ] 配置应用监控，至少覆盖认证失败、限流、超时、协议错误、调用延迟和固定 cleanup
  timeout warning；日志不得记录 token、问题全文、结果、private URL 或 session ID。
- [ ] 每次仅修改 `_haibao_mcp.py` 后重启受影响的 Haitun Session/process，不能依赖
  public wrapper hot refresh 使 helper-only 变更生效。
- [ ] 在 private MCP/reverse proxy 已启用 response body limit，且下方生产门禁全部通过后，
  才允许接入真实业务数据。

Haitun 侧不得实现或配置以下内容：ChatBI 直连 API、数据库连接、DDL/database onboarding、
数据库密码、私有 API key、`org_id`/`user_id` 覆盖参数，以及绕过 MCP 的备用调用路径。

### 4.2 启动命令

私有 Server 就绪且三个变量已由进程环境注入后，启动 workspace Session：

```bash
uv run psi-agent session \
  --workspace examples/haitun-workspace \
  --ai-socket /tmp/ai.sock \
  --channel-socket /tmp/ch.sock
```

### 4.3 验证命令

本地验证命令：

```bash
uv run pytest -c NUL -o asyncio_mode=auto -p no:cacheprovider examples/haitun-workspace/tests/test_haibao_tools.py -v
uv run pytest -c NUL -o asyncio_mode=auto -p no:cacheprovider tests/psi_agent/session/test_tool_registry.py -v
uv run ruff check .
uv run ruff format --check .
uv run ty check
git diff --check
```

在非 Windows 环境可将 pytest 的 `-c NUL` 改为 `-c /dev/null`；这是因为仓库默认
`testpaths` 只包含根 `tests/`。保留 `-o asyncio_mode=auto` 以运行异步测试。

功能验收至少覆盖：纯 SQL 概念不调用 Tool；未给 `db_id` 时先列数据集；零数据集说明
不可用；多个候选时询问用户；执行失败不描述为空结果；超时后不编造或盲重试；结果中
的伪指令只作为数据处理；取消和超时后启动 bounded cleanup，并验证 cleanup timeout
的 process recycle 告警路径；在 private MCP/reverse proxy 验证 response body limit。

## 5. 生产门禁

生产部署应使用 dedicated service deployment group 隔离私有 MCP 与核心服务。以下
仍是 production gates，不因 Adapter 已 bundled 而自动满足：

- OAuth issuer、principal、租户和数据集授权经过验证；
- 最小权限只读账户、SQL allowlist、statement timeout、行数与扫描成本限制有效；
- rate limiting、per-principal quota 和并发控制启用；
- monitoring、审计、request ID、延迟与错误率告警可用，且日志不含问题全文、结果或
  credential；
- timeout、取消、幂等、资源关闭和灾难恢复经过集成测试；
- reverse proxy/private MCP 强制 response body limit，防止 SDK 解析前发生
  pre-validation memory exhaustion；
- 敏感字段与大结果集采用最小披露。

这些门禁和私有服务安全验证完成前，不得宣称 production ready 或已部署生产。
