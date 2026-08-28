# C 端免费模型转发器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 C 端默认免费模型的上游 key 从个人云服务器迁到公司服务器，由 litellm 独立容器持有，psi-cloud 加无状态 `modules/llm` 做登录态鉴权与错误收口。

**Architecture:** 客户端 → Caddy → psi-cloud `modules/llm`（鉴权 + 错误收口）→ litellm 容器（持 key + provider 路由）→ api.deepseek.com。litellm 只监听容器网络，不对外。`modules/llm` 无状态：不建表、无后台任务。

**Tech Stack:** FastAPI + httpx + anyio（psi-cloud）；litellm proxy 容器；pytest + respx（测试）；TypeScript/Vue（客户端两版 SPA）。

**Spec:** `docs/superpowers/specs/2026-08-14-llm-gateway-design.md`

## Global Constraints

以下是 psi-cloud 仓库（服务器 `/srv/psi-cloud`）的既有硬规则，逐字来自其 `AGENTS.md`，每个任务都隐含适用：

- **`core/` 里不出现业务名字。** 出现了就是隔离破了。
- **`service.py` 不 import fastapi。** 状态码只在 `core/errors.py`。
- **SQL 只在 `repository.py`。** 别处出现 `execute(` 就是越层。（本模块无库，故不应出现任何 SQL）
- **`anyio`，不用 `asyncio` 原生 API；不用 `pathlib`。**
- **不留抑制。** 没有 `noqa`、没有 `type: ignore` 兜底。
- **失败响应不回报剩余次数，也不回报标识归谁。**
- **日志不打完整手机号、邮箱、验证码。** 本模块追加：**日志不打上游 key 任何片段、不打完整对话内容。**
- **自家限频跑在调供应商之前。** 顺序反了要花冤枉钱。
- Python >= 3.14；ruff line-length 88，lint select = `["E","F","I","UP","B","SIM"]`。
- psi-cloud 依赖只增不换：**不引入 litellm 作为 Python 依赖**（它是独立容器）。

psi-agent 主仓约束（客户端任务适用）：

- 异步用 `anyio`，禁止 `asyncio` 原生 API 与 `pathlib`。
- 零抑制：不堆 `noqa`，不设 `per-file-ignores`。

## 环境事实（实施时依赖，已实地核实）

- 服务器 `root@account.genuineknowledge.cn`，已配置免密，新加坡节点，Ubuntu 24.04。
- 仓库在 `/srv/psi-cloud`，**无 git remote，服务器上是唯一副本** —— 改动前先在服务器本地打 tag 备份。
- 现有容器 `psi-cloud`，`127.0.0.1:8081->8000`，compose 网络 `psi-cloud_default`。
- Caddy v2.11.4 宿主机原生，`/etc/caddy/Caddyfile` 是唯一权威副本，**本次不改它**。
- 生产编排 `docker-compose.yml`，开发编排 `docker-compose.dev.yml`（需显式 `-f`）。
- `.env` 整体注入容器（不逐条列举），`PSI_DATA_DIR` 与 `AUTH_CODE_HASH_SALT` 由编排固定。
- DeepSeek 在册模型实测：`['deepseek-v4-flash', 'deepseek-v4-pro']`。
- psi-cloud 当前**零测试**，无 `tests/` 目录，dev 依赖已声明 `pytest` / `pytest-asyncio` / `respx` / `ruff`。

## File Structure

服务器 `/srv/psi-cloud`（新增/修改）：

```
src/psi_cloud/core/module.py        改：Module.requires + ModuleRuntime.deps
src/psi_cloud/core/registry.py      改：字典序 → 拓扑序 + 环/缺失检测
src/psi_cloud/core/app.py           改：按拓扑序构造 ctx 并注入 deps
src/psi_cloud/modules/llm/
  __init__.py
  config.py                         LlmSettings（LLM_* 环境变量）
  deps.py                           LlmContext / get_llm_ctx
  service.py                        转发逻辑，不 import fastapi
  router.py                         3 个路由 + StreamingResponse
  manifest.py                       MODULE = Module(requires=("auth",), schema=())
tests/
  conftest.py
  test_registry.py                  拓扑序 / 环 / 缺失依赖
  modules/llm/test_auth.py
  modules/llm/test_forward.py
  modules/llm/test_errors.py
  modules/llm/test_stream.py
docker-compose.yml                  改：+ litellm service
litellm/config.yaml                 新建
.env                                改：+ LLM_* / DEEPSEEK_API_KEY / LITELLM_MASTER_KEY
```

本仓 `psi-agent`（新增/修改）：

```
src/psi_agent/gateway/spa-v2/src/services/bootstrapAi.ts       改：base_url + token
src/psi_agent/gateway/spa-v2/src/services/bootstrapAi.test.ts  改：同步断言
src/psi_agent/gateway/spa/src/bootstrapAi.js                   改：base_url + token
```

---

## Task 0：服务器改动前备份

**这一步不可跳过。** 服务器上的 git 无 remote，是唯一副本（spec「风险」节）。

- [ ] SSH 上服务器，确认工作区干净：`cd /srv/psi-cloud && git status --short`
- [ ] 打备份 tag：`git tag backup/before-llm-gateway-2026-08-14`
- [ ] 备份现有 `.env` 到 `/root/psi-cloud-env-backup-2026-08-14`（600 权限），确认可读回
- [ ] 记录当前容器状态与镜像 digest：`docker compose ps` + `docker compose images`，贴进交付文档

---

## Task 1：扩 `Module.requires` + 拓扑序 + 环检测

对应 spec 任务 1。这是唯一动 `core/` 的任务，先做因为 Task 3 依赖它。

**为什么必须改排序：** `create_app()` 顺着 `discover()` 返回的顺序调
`build_context`，而 `discover()` 返回 `sorted(key=name)`。`llm` 排在 `auth` 之后
纯属字典序巧合 —— 依赖注入不能靠巧合。

### Interfaces

```python
# core/module.py —— ModuleRuntime 加一个字段
@dataclass(frozen=True, slots=True)
class ModuleRuntime:
    name: str
    db: Database
    data_dir: str
    deps: Mapping[str, Any] = field(default_factory=dict)
    """本模块 requires 声明的模块 ctx，键是模块名。框架填，模块只读。"""

    # core/module.py —— Module 加一个字段
    requires: Sequence[str] = ()
    """依赖的其它模块名。框架保证它们的 ctx 先构造好并从
    ModuleRuntime.deps 传入 —— 模块之间不直接 import。"""


# core/registry.py
def discover(disabled: frozenset[str] = frozenset()) -> list[Module]:
    """返回拓扑序模块列表：被依赖者在前，同层按 name 稳定排序。"""


def _topo_sort(modules: list[Module]) -> list[Module]:
    """按 requires 拓扑排序。缺失依赖 → ValueError；成环 → ValueError。
    同层用 name 排序，保证启动日志与路由顺序可复现。"""
```

### Steps

- [ ] 写 `tests/` 骨架：`tests/__init__.py`、`tests/conftest.py`（暂空）；`pyproject.toml` 的 `[tool.pytest.ini_options]` 加 `asyncio_mode = "auto"`、`testpaths = ["tests"]`
- [ ] 先写测试 `tests/test_registry.py`：直接测 `_topo_sort`，构造裸 `Module` 实例，不碰真实 modules 包 —— 四个用例：(a) `B.requires=("A",)` 且 name 逆序时 A 仍在前；(b) 无依赖时按 name 排序；(c) `requires` 指向不存在的模块 → `ValueError`，消息含缺失名与声明方；(d) A↔B 互相 requires → `ValueError`，消息含「环」与环上模块名
- [ ] 跑测试，确认失败（`_topo_sort` 不存在）
- [ ] 改 `core/module.py`：`ModuleRuntime` 加 `deps`，`Module` 加 `requires`；`Mapping` 从 `collections.abc` 导入
- [ ] 改 `core/registry.py`：加 `_topo_sort`，把 `return sorted(...)` 换成 `return _topo_sort(found)`；更新 `discover` docstring 与模块头注释（现在写的是「按 name 排序」）。禁用某模块导致依赖缺失时，报错要指明是 `disabled` 造成 —— 否则排查方向会跑到拼写错误上
- [ ] 跑测试，全绿
- [ ] 改 `core/app.py`：构造 ctx 的循环里，按 `module.requires` 从已构造的 `contexts` 取出依赖，作为 `deps=` 传给 `ModuleRuntime`；因为已是拓扑序，缺失即框架 bug，用 `raise RuntimeError` 而非 `.get()` 兜底
- [ ] `ruff check` + `ruff format --check`，跑全部测试
- [ ] 手工验证契约未破：`docker compose up -d --build` 后 `curl -s localhost:8081/healthz`，auth 与 analytics 仍 ok

---

## Task 2：litellm 独立容器进编排

对应 spec 任务 2。**必须钉具体 tag，不用 `latest`**（spec「风险」节）。

**注意这违反了「新增业务模块不改 `docker-compose.yml`」的契约字面** —— 但那条契约
说的是业务模块，本任务加的是独立容器，属另一类变更。提交说明须写明这点。

### Interfaces

```yaml
# docker-compose.yml 新增 service
  litellm:
    image: ghcr.io/berriai/litellm:v1.77.3-stable   # 实施时确认该 tag 存在，不用 latest
    restart: unless-stopped
    env_file: [.env]
    volumes:
      - ./litellm/config.yaml:/app/config.yaml:ro
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    # ** 不写 ports ** —— 只在 compose 网络内可达，宿主机与外网都碰不到
```

```yaml
# litellm/config.yaml
model_list:
  - model_name: deepseek-v4-flash
    litellm_params:
      model: deepseek/deepseek-v4-flash
      api_key: os.environ/DEEPSEEK_API_KEY
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

### Steps

- [ ] 确认要用的镜像 tag 真实存在（`docker pull` 试拉），记下 digest
- [ ] `.env` 追加 `DEEPSEEK_API_KEY`（**新生成的 key，不用截图里那把泄露的**）与 `LITELLM_MASTER_KEY`（`openssl rand -hex 32` 生成）。文件权限确认 600
- [ ] 新建 `litellm/config.yaml`（内容同上）
- [ ] `docker-compose.yml` 加 litellm service；psi-cloud service 加 `depends_on: [litellm]`
- [ ] `docker compose up -d`，`docker compose logs litellm` 确认启动无 DB 报错
- [ ] **验证不对外**：宿主机 `curl -sS -m 3 localhost:4000/health` 必须失败（连接被拒）
- [ ] **验证容器内通**：`docker compose exec psi-cloud python -c` 用 httpx 调 `http://litellm:4000/v1/models`，带 `Authorization: Bearer $LITELLM_MASTER_KEY`，应返回含 `deepseek-v4-flash` 的列表
- [ ] **验证上游真的通**：容器内对 litellm 发一次 `max_tokens: 1` 的 `/v1/chat/completions`，确认 200。失败则先解决，不要带着坏 key 往下做
- [ ] 确认 `.env` 与 `litellm/config.yaml` 不含真实 key 之外的敏感值；`git status` 检查 `.env` 已被 ignore

---

## Task 3：`modules/llm` 骨架 + 鉴权 + 非流式转发

对应 spec 任务 3。验收 A5、A6（非流式部分）。

### Interfaces

```python
# modules/llm/config.py
@dataclass(frozen=True, slots=True)
class LlmSettings:
    upstream_base_url: str  # LLM_UPSTREAM_BASE_URL
    upstream_master_key: str  # LLM_UPSTREAM_MASTER_KEY
    default_model: str  # LLM_DEFAULT_MODEL
    allowed_models: tuple[str, ...]  # LLM_ALLOWED_MODELS，逗号分隔
    request_timeout: float  # LLM_REQUEST_TIMEOUT，默认 300


def llm_settings() -> LlmSettings: ...


# modules/llm/deps.py
class LlmContext:
    def __init__(self, *, settings: LlmSettings, client: httpx.AsyncClient, auth: Any) -> None: ...

    # auth 是 auth 模块的 ctx，由 manifest 从 runtime.deps["auth"] 传入


def get_llm_ctx(request: Request) -> LlmContext: ...


Ctx = Annotated[LlmContext, Depends(get_llm_ctx)]


# modules/llm/service.py —— 不 import fastapi
async def authenticate(ctx: LlmContext, token: str) -> str:
    """token → user_id。委托 auth 模块的 resolve_session。"""


def resolve_model(ctx: LlmContext, payload: dict[str, Any]) -> dict[str, Any]:
    """填默认 model；清单外 raise InvalidInput（不转发）。返回新 payload。"""


async def complete(ctx: LlmContext, payload: dict[str, Any]) -> dict[str, Any]:
    """非流式转发。上游异常映射为 core.errors 里的既有异常。"""


def map_upstream_error(status: int, body: str, retry_after: str | None) -> DomainError:
    """上游状态码 → 统一信封。body 只进日志，不进返回值的 message。"""
```

### Steps

- [ ] 新建 `modules/llm/__init__.py`、`config.py`，`LlmSettings` 照 `modules/auth/config.py` 的读环境变量风格写（不引新配置库）
- [ ] 写测试 `tests/modules/llm/test_errors.py`：`map_upstream_error` 的四条映射（401→502 `provider_failure`、429→429 带 `retry_after`、超时→502、5xx→502），并断言返回的 `message` 不含传入 body 的任何子串
- [ ] 实现 `service.py` 的 `map_upstream_error`，跑测试转绿
- [ ] 写测试 `tests/modules/llm/test_forward.py`：用 `respx` mock `http://litellm:4000/v1/chat/completions`，断言 (a) 未知参数原样出现在上游请求体；(b) 响应里 `reasoning_content` 不丢；(c) 请求带 `stream_options` 时不被改写；(d) 无 `model` 时填 `LLM_DEFAULT_MODEL`；(e) 清单外 model → `InvalidInput`（422）且 **respx 未收到任何请求**
- [ ] 实现 `resolve_model` 与 `complete`，跑测试转绿
- [ ] 写测试 `tests/modules/llm/test_auth.py`：`authenticate` 在 token 无效时（mock 的 auth ctx 抛 `Unauthorized`）向外仍是 `Unauthorized`；有效时返回 user_id
- [ ] 实现 `authenticate`，跑测试转绿
- [ ] 写 `router.py`：`POST /chat/completions`，用 `BearerToken` 依赖（从 `...auth.deps` 复用不行 —— 那是业务 import 业务；把 `require_bearer` 的等价实现放在 `modules/llm/deps.py`，8 行，重复优于跨业务耦合）
- [ ] 写 `manifest.py`：`MODULE = Module(name="llm", prefix="/llm/v1", router=router, schema=(), requires=("auth",), build_context=build_context, cors_origins=())`；`build_context` 从 `runtime.deps["auth"]` 取 auth ctx，建 `httpx.AsyncClient(timeout=...)`
- [ ] 日志检查：确认 service 与 router 里没有任何一行会打出 token、master key、或完整对话内容
- [ ] `ruff check`，全部测试通过
- [ ] 部署验证：`docker compose up -d --build`，无 Bearer 调 `/llm/v1/chat/completions` → 401；用真实登录 token 调（非流式 `"stream": false`）→ 200 有内容

---

## Task 4：流式转发 + 取消传导

对应 spec 任务 4。验收 A7、A8，以及 A6 的流式分支。

**状态码边界是本任务的核心难点：** SSE 首个 chunk 发出后 HTTP 状态码已定。所以
上游连接必须在**产出第一个 chunk 之前**就建立并读到响应头 —— 那时还能返回 502。

### Interfaces

```python
# modules/llm/service.py
async def stream_completion(ctx: LlmContext, payload: dict[str, Any]) -> AsyncIterator[bytes]:
    """流式转发。第一个 chunk 之前的失败以异常抛出（router 转 HTTP 错误）；
    之后的失败以 SSE 错误帧发出并终止。

    实现要点：httpx 的 stream 上下文必须在生成器体内打开并持有 ——
    客户端断连时生成器被 GC/aclose，上下文退出即关闭上游连接（A8）。
    不得在中间攒完整包：攒了首字延迟等于整轮时长。
    """


def error_frame(exc: DomainError) -> bytes:
    """流中失败时发的 SSE 帧：data: {"error": code, "message": msg}\\n\\n
    随后跟 data: [DONE]\\n\\n —— 客户端的 SSE 解析器不会因缺 DONE 而挂住。"""
```

### Steps

- [ ] 写测试 `tests/modules/llm/test_stream.py`：respx mock 流式响应，断言 (a) 上游 chunk 原样透传、顺序不变；(b) 上游返回 401 响应头（未出 chunk）时 `stream_completion` 抛 `ProviderFailure` —— 即错误发生在首个 `yield` 之前；(c) 上游发两个 chunk 后断开时，产出的最后一帧是错误帧且不含上游原文；(d) 首字不被攒：mock 慢速上游，第一个 chunk 到达即可从生成器取到，不等流结束
- [ ] 实现 `stream_completion` 与 `error_frame`，跑测试转绿
- [ ] `router.py`：`stream` 为真时返回 `StreamingResponse(media_type="text/event-stream")`；先 `await anext()` 拿到首个 chunk 再构造响应，这样首 chunk 前的异常还能走 `_domain_error_handler`（**这是 (b) 能兑现的前提，实现时别省**）
- [ ] `ruff check`，全部测试通过
- [ ] 部署验证 A7：用真实 token 发一次带 `stream: true` + `stream_options.include_usage` 的请求，确认末帧含 usage
- [ ] 部署验证 A8：`curl` 流式请求，中途 Ctrl-C；`docker compose logs litellm` 应显示该请求被取消/断开，而非继续跑完。把日志片段贴进交付文档 T 段
- [ ] 手工测首字延迟：对比容器内直调 litellm 与经 psi-cloud，首字延迟差应在几十毫秒量级

---

## Task 5：`/models` + `/health/upstream`

对应 spec 任务 5。验收 A4。

**`/health/upstream` 是本次故障的直接对策** —— 换 key 后能一条 curl 自证，不必靠
群里来回确认。**它不进 `/healthz`**：compose healthcheck 30 秒一次，探针不该花钱。

### Interfaces

```python
# modules/llm/service.py
async def list_models(ctx: LlmContext) -> dict[str, Any]:
    """转发 litellm 的 /v1/models，过滤到 allowed_models。
    ** 不证明上游 key 有效 ** —— 这正是本次踩的坑，故另有 check_upstream。"""


async def check_upstream(ctx: LlmContext) -> dict[str, Any]:
    """对上游发一次 max_tokens=1 的真实请求。返回
    {"ok": bool, "model": str, "detail": str} —— detail 是给运维看的原因
    摘要（如 "upstream rejected credentials"），不含 key 任何片段、不含上游原文。"""
```

### Steps

- [ ] 写测试：`list_models` 过滤掉清单外模型；`check_upstream` 在上游 401 时返回 `ok=False` 且 `detail` 不含上游 body 与 key 片段（**注意它返回结果而不抛异常** —— 诊断接口要能报告失败，不是自己 500）
- [ ] 实现两个函数，跑测试转绿
- [ ] `router.py` 加 `GET /models` 与 `GET /health/upstream`，都要 Bearer
- [ ] 确认 `core/app.py` 的 `/healthz` 未被改动（不含任何供应商探测）
- [ ] 部署验证 A4：`curl -H "Authorization: Bearer <token>" https://account.genuineknowledge.cn/llm/v1/health/upstream` → `{"ok": true, ...}`
- [ ] 反向验证：临时把 litellm 的 `DEEPSEEK_API_KEY` 改成无效值重启，同一条 curl 应返回 `ok: false`；恢复正确 key 并确认恢复 `ok: true`

---

## Task 6：测试门禁与文档同步

对应 spec 任务 6。用例本身已在 Task 3–5 里随功能写完（TDD），本任务只做收口。

- [ ] 核对 spec「测试」节四组用例全部存在且通过：鉴权(A5)、转发(A7)、错误映射(A6)、流式(A6)
- [ ] 服务器上跑全量：`python -m pytest tests/ -q` 与 `ruff check .`，两者都必须干净
- [ ] 更新 `/srv/psi-cloud/AGENTS.md`：补「测试」一节（怎么跑、mock 上游用 respx、不做真实上游自动化测试的原因）；`modules/llm` 加进模块清单；`Module.requires` 写进契约说明
- [ ] 确认 `AGENTS.md` 里没有把 spec 内容复制进来（三向同步：信息只归属一层）

---

## Task 7：客户端接入

对应 spec 任务 7。验收 A1。

### ⚠️ spec 修正（实施时发现，须同步回 spec）

spec「客户端改动」写「`api_key` 字段改为传登录态 token」。**这一条不能照做**，两条
既有硬约束挡着：

1. `spa-v2/src/services/api.ts:271` —— token 全程由 Gateway 持有并加密落盘，
   **前端拿不到也不该存**；`authFlow.ts:290` 更进一步要求登录组件源码不出现 token
   字面量，理由是 XSS。SPA 里根本取不到 token 可填。
2. `gateway/_auth_store.py:10` 与 `gateway/__init__.py:222` —— `api_key` 是**明文写进
   快照**的，注释明确写着「登录凭证不再踩这个坑」。把 token 当 api_key 存就是重新踩。

**改为：** SPA 继续填哨兵值，由 **Gateway 在拉起 AI 子进程时把哨兵替换成真实 token**。
好处是 token 不进快照、不进前端、AI 层与 Session 层零改动（符合 spec「不做」第 5 条）。
**本条原先写「`_config_key` 把 api_key 计入去重键，所以重新登录后 token 变化会自然
拉起新 socket —— 轮换不需要额外机制」，实施时证伪。** 进去重键的是 `AiInfo.api_key`，
而那里存的是**哨兵**（token 只活在 `Ai` 实例里，见上）—— 去重键从头到尾看不见 token
变化，socket 不会自然重建。不补机制的后果：换账号登录后仍拿旧 token（已被云端吊销）
去请求，一路 401；登出后仍能继续用，更糟。

**改为：** `AIManager.refresh_where(predicate)` 在登录/登出时**原地重建**匹配的 socket
（`AiInfo` 一个字段都不变，所以模型列表、Session 的 `backend_id`、快照全都不动），
重建时重新走一次 key 解析。接线在 `server.py` 的 `_auth_verify` / `_auth_complete`
（200 时）与 `_auth_logout`（无条件 —— `logout()` 即使云端不可达也会 `logout_local()`）。

### Interfaces

```ts
// spa-v2/src/services/bootstrapAi.ts
export const DEFAULT_REMOTE_AI = {
  provider: 'openai',
  model: 'deepseek-v4-flash',                              // 不变，实测在册
  base_url: 'https://account.genuineknowledge.cn/llm/v1',  // 改
  api_key: 'haitun-default',                               // 不变：哨兵，Gateway 替换
}
```

```python
# gateway/_free_model.py（新增）
PLACEHOLDER_API_KEY = "haitun-default"  # 与两份 SPA 是同一个契约


def is_cloud_free_model(api_key: str, base_url: str, auth_endpoint: str) -> bool: ...
def make_key_resolver(token_of, auth_endpoint) -> Callable[[str, str], str]: ...


# gateway/_ai_manager.py
_resolve_key: Callable[[str, str], str] = _key_as_is  # 注入点，只作用于交给 Ai 的那一份


async def refresh_where(self, predicate) -> list[str]: ...


# gateway/_auth_manager.py
def bearer_token(self) -> str: ...  # 唯一的进程内取值口，不接任何下行响应
```

替换条件是**两条同时成立**：`api_key` 是哨兵，且 `base_url` 与认证服务**同源** ——
token 只能发给签发它的那台主机，否则改一份快照就能把凭证送去任意域名。

**「未登录则不拉起该 socket」这一条改掉了。** 免费模型是默认配置，不拉起的表现是用户
看到模型列表少一项，更难懂。改为：仍然拉起，key 为空。

**⚠️ 这里我判断错了一次，负责人实测推翻。** 原先写的是「请求时拿云端的 401，那是能看懂
的错误」—— 实际上空 key **走不到云端**：any-llm 的 openai provider 在发 HTTP 之前就本地
抛 `No openai API key provided. Please provide it in the config or set the
OPENAI_API_KEY environment variable`，一句与本产品毫无关系的话（截图见负责人反馈）。
未登录的真正兜底改在前端：SPA v2 启动即**硬门禁**，未登录进不来（见下方 Task 10）。
`_free_model.py`、`test_free_model.py`、`gateway/AGENTS.md` 三处措辞已同步改正。

AuthManager 的构造必须**移到恢复 AI 之前**（`__init__.py`）：免费模型的 socket 在构造时
就要拿到 token，建晚了恢复出来的 socket 会带着哨兵起来，第一次对话必然 401。

### Steps

- [x] 读 `gateway/__init__.py:150-270` 与 `_ai_manager.py:50-115`，确认替换点与未登录时的现有行为
- [x] 写 Python 测试（`tests/psi_agent/gateway/test_free_model.py`，16 例）：同源判定 6 例、解析函数 4 例、接进 `AIManager` 6 例
- [x] 实现替换逻辑，跑测试转绿。**替换后的 token 不得写回快照** —— `test_token_reaches_ai_but_not_aiinfo` 断言整个 `asdict(AiInfo)` 里不出现 token
- [x] 改 `spa-v2/src/services/bootstrapAi.ts:12` 的 `base_url`
- [x] 同步 `spa-v2/src/services/bootstrapAi.test.ts` 断言（两处 dedupe URL + 新增 `DEFAULT_REMOTE_AI` 三条）
- [x] 改 `spa/src/bootstrapAi.js:8` 的 `base_url`（v1 别漏，两份都在用）
- [x] 复核 `isPlaceholderAi()`：api_key 仍是哨兵，语义不变，`pickPreferredAi` 无需改动（前端 163 例全绿佐证）
- [x] 前端测试：`spa-v2` vitest 163 例全绿，`vite build` 通过。**spa-v2 没有 lint script**；`tsc --noEmit` 是**先前就坏的**（TypeScript 7.0.2 移除了 `baseUrl`，其 tsconfig 仍设着），与本次改动无关
- [ ] 端到端验证 A1：已登录状态下 SPA v2 新建会话直接对话成功；SPA v1 同样验一次 —— **需要真实登录，交由负责人在客户端上点一次**；服务端侧的等价证据已由 `verify-llm-e2e.py` 取得（真实登录拿真 token 打真实上游，10 项通过）

---

## Task 8：换 key 演练

对应 spec 任务 8。验收 A3 —— 这是整个方案要解决的原始问题，必须实测一遍。

- [ ] `docker compose exec` 进 psi-cloud 容器，检索环境变量与文件系统，确认**拿不到上游 key**（A2）
- [ ] 检索客户端构建产物，确认不含上游 key（A2）
- [ ] 演练：把 litellm 的 `DEEPSEEK_API_KEY` 换成另一把有效 key（或同一把改一位再改回），`docker compose up -d litellm`
- [ ] 客户端不做任何改动，对话仍然成功 → A3 成立
- [ ] 用 `/llm/v1/health/upstream` 自证换后可用 → A4 与 A3 联动成立
- [ ] 把演练的命令序列写成 5 行以内的 runbook，放进交付文档 A 段（下次换 key 的人照抄即可）

---

## Task 9：停用个人服务器 LLM 转发

对应 spec 任务 9。**这一步涉及他人机器，且有未确认依赖，须先确认再动手。**

- [ ] 确认 `scripts/dev-feishu.ps1:19` 用 `misakamikoto.genuineknowledge.cn` 做 BaseUrl 时指向的是什么服务 —— 若与 LLM 转发无关则本任务不受其阻塞；若相关，先给它换地址
- [ ] 与个人服务器持有者确认该机器上是否还有其它在用服务，只停 LLM 转发，不动其它
- [ ] 停用后验证旧地址不再转发；确认线上客户端已全部指向新地址（旧版安装包本就已坏，不构成回退）
- [ ] 若确认阻塞或需他人操作，**不要强行推进** —— 在交付文档 A 段记下卡点与责任人，其余任务照常收尾

---

## Task 10：C 端强制登录（团队 2026-08-15 决定，spec 之外的追加）

**起因**：负责人实测未登录对话，拿到 `[Upstream Error]: [openai] No openai API key
provided. Please provide it in the config or set the OPENAI_API_KEY environment
variable.` —— 我在 Task 7 里判断的「拿云端 401」不成立（详见上方 Task 7 的 ⚠️ 块）。
团队随后决定：**C 端必须登录才能使用**，进应用即检查登录态，未登录弹登录窗且**不可跳过**，
点击组件外部不关闭；同时**删除登录时的隐私条款勾选**。

原先是**软门禁**（可跳过）：理由是「设计文档写明离线不可登录、数据全在本机，登录不是
使用前置条件」。这条理由现在不成立了 —— C 端默认模型的 key 由云端按登录态下发，未登录
时它**根本不可用**。放人进来只是把拦截点从登录窗推迟到第一次对话，还换成一句看不懂的话。

- [x] `HubLoginPanel`：`showSkip` → `mandatory`。为真时不给「暂不登录，继续使用」、
      `blocking` 传给 `HubDialog`（藏 ✕ + 遮罩退化为 `aria-hidden` 装饰层）
- [x] `HubLoginPanel`：删掉 `agreed` / `shakeAgree` 两个 state、`onSend` 的勾选前置检查、
      `onLogout` / `startBind` 里的 `setAgreed` —— 勾选框改为一行被动告知「登录即表示同意
      《用户服务协议》与《隐私政策》」。**协议链接保留**：去掉勾选不等于不告知
- [x] `UserHub`：加 `loginRequired`。为真时 (a) `show={loginRequired || panel === 'login'}`
      强制显示，否则用户点侧栏别的入口就把登录窗顶掉、门只拦得住第一下；(b) Esc 直接 return
- [x] `HaiTunAgentWorkspace`：`authGate` 由软改硬，探测逻辑提成 `recheckAuthGate`；
      `onLoginGateDone` 改为**重新探一次** `/auth/status` 而不是直接 `setAuthGate("passed")`
      —— 硬门禁只该被真的登录成功打开
- [x] 放行的两种情形（**刻意保留**）：`available === false`（部署方显式关掉登录，没有门可守，
      拦下去只会得到一个点不动的表单）；探测抛错（连「是否需要登录」都不知道，且 Gateway
      不通本身会由别处报错，不该在这里变成一堵解释不清的墙）
- [x] CSS：`.hub-agree`（含 `prefers-reduced-motion` 里的 `.box.shake`）→ `.hub-legal-note`
- [x] 测试：`describe('硬门禁：不可跳过')` 4 例 —— 无跳过按钮 / 无 ✕ 且点遮罩不关窗 /
      断网屏也不放行且说清原因 / 非 mandatory 时 ✕ 与可点遮罩仍在。原「未勾协议就点」
      改为「号码合法即可直接发码」，A1 那条补断言协议链接仍在且 `同意协议` 控件已消失
- [x] SPA v1（`spa/`）**无任何认证 UI**（`grep` 无 `auth/status` / `sendAuthCode`），不加门
- [x] 同步改正五处「云端会回 401」的错误措辞：`_free_model.py`（docstring + resolve 注释 +
      日志文案）、`test_free_model.py` docstring、`gateway/AGENTS.md` 表格「未登录」行、
      本 plan 的 Task 7、交付文档 A 段
- [ ] 负责人手工验：全新客户端未登录启动 → 只能看到关不掉的登录窗；登录成功 → 直接进
      工作台且能对话（这一条同时兑现 A1）

## Task 11：TLS 握手包被丢导致云端全不可达（Task 10 验收时暴露）

**起因**：负责人按 Task 10 最后一条去验，门禁显示正确，但**人被彻底关在外面** ——
截图是 D3 断网屏，日志里 `认证服务请求失败 GET /me: TimeoutError()` 与
`POST /sms/send: TimeoutError()` 反复出现，30s 打满。而同一时刻 `curl` 同一域名
0.7s 就回。硬门禁把「登录坏了」放大成了「整个产品用不了」。

**定位过程**（逐层排除，每步都是背靠背实测）：

| 排除项 | 证据 |
|---|---|
| DNS / TCP | 域名解析到单个 IPv4 `8.222.255.23`；裸 TCP 连接 0.20s 通 |
| aiohttp connector 配置 | 裸 asyncio TLS 同样 0/12，与 connector 无关 |
| 事件循环 | Proactor 0/5、`_WindowsSelectorEventLoop` 0/5 |
| asyncio 本身 | 同步 `http.client` 也失败（2/5、1/4） |
| Python 整体 / 网络整体 | 同一 Python 打 baidu 4/4 通 |
| TLS 版本协商 | 强制 1.2 与 1.3 各 0/6 |
| curl 为什么行 | curl on Windows 走 **Schannel**，不发后量子密钥份额 |

**结论**：`ctx.set_ecdh_curve("prime256v1")` **8/8 通，默认组列表 0/8**。OpenSSL 3.5
默认组列表带后量子混合密钥交换（X25519MLKEM768），ClientHello 撑过 ~1400 字节被分片，
路径上有设备把分片的握手包丢了。**与本产品代码无关**，但必须由本产品绕开。

- [x] 新增 `psi_agent/_tls.py`：`client_ssl_context()` 从 `create_default_context()` 起手，
      **证书校验与主机名核对一个都不动**，只收窄密钥交换曲线。放顶层是因为出站 HTTPS 有
      两条独立的路、分属两个进程，同一成因不该有两份注释
- [x] `AuthManager._ensure_session()`：`TCPConnector(ssl=client_ssl_context())`。
      实测经真实 `AuthManager`、每次新连接 **4/4 拿到云端 401**（此前 0/N 全超时）
- [x] AI 层同样中招（免费模型经 any-llm/httpx 打 `/llm/v1`，也是 OpenSSL）—— **不修的话
      登录成功后第一次对话照样超时**。实测默认 19s 超时 vs 修后 0.64s 拿到上游 401
- [x] `serve_ai()` 加 `_build_http_client(provider)`，经 `acompletion(client_args=...)` 注入。
      **按 provider 挑**：Gemini（google-genai）与 Mistral 不收 `http_client`，无条件传会
      当场 `TypeError` —— 为修一条路把另外几条弄断
- [x] `handle_chat_completions` 用 `app.get("http_client")` 而非 `[...]`：该 handler 也被
      不经 `serve_ai` 装配的 app 用（测试），少一个键不该变成 500
- [x] 测试 5 例：provider 分档 3 例（收 / 不收 / 名字不认识不拦启动）+ handler 注入 2 例
      （经 `client_args` 传进去 / 没配时不传空壳）
- [x] 排查过其它出站 client：只有飞书 `open.feishu.cn` 是另一个外部 host，实测
      default 4/4、p256 4/4 —— **不受影响，不动**。其余 `ClientSession` 全是本机组件间 socket
- [x] 修正 D3 断网屏那句「登录需要联网，**本机功能不受影响**」—— 硬门禁下这话是假的
      （人是真进不去），`mandatory` 时改为「请检查网络后重试」，并补断言守着

**顺带查清的一件事（结论：不是 bug）**：`POST /auth/sms/send` 回 502
`provider_failure`。服务器日志显示阿里云 HTTP 200 但体里 `Code=UNKNOWN`。这是我拿
**测试号码 `13800000000`** 打出来的 —— 该号段是保留号，被上游拒属正常。负责人那次登录
**根本没有请求到达服务器**（`docker logs` 在对应时间窗内 `sms/send` 与 `/auth/me` 各 0 条），
证实 TLS 是当时唯一的拦路虎。真实号码能否发出**只能由负责人用自己的号码验**。

> ~~移交给云端仓（`/srv/psi-cloud`，非本仓）的一条改进：`aliyun.py:145` 的 non-OK 日志
> 只打了 `Code`，没打 `Message`。~~ **已在 Task 12 做掉。**

---

## Task 12: 负责人验收后的四项（登录已通之后）

> **下面前两项（安装器侧）不是本 plan 做的。** 负责人在**另一个会话**里做完了它们，那个会话
> 与本会话共用同一个工作树和同一个 git index，所以 `git add` 时被本 plan 的提交一并带上了
> （`fe944dce` 混了 11 个安装器文件 + 5 个本 plan 的 UI 文件）。负责人的决定是「将错就错，
> 合并为一个 PR」，故提交结构不动，但**署名要说清**：设计与实现在
> `docs/superpowers/specs/2026-08-15-installer-tos-consent-design.md`，作者是那个会话。
> 本会话在其上只补了测试（见下）。
> **教训**：并行会话要各开 git worktree，否则谁都可能提交对方的半成品。

- [x] **删掉登录屏那句协议文字。**（另一会话）直接删等于没人同意过协议，所以**同意动作前移到
      安装期** —— 安装向导第一页必勾，不勾则「下一步」禁用。一个勾选框覆盖两份协议，这是许可
      协议导言自己规定的形态（「勾选同意本协议即视为同时同意隐私保护政策」），不是 UI 选择；
      也因此不能用 Inno 内置 `LicenseFile`（单选钮、一次只挂一份文件）
- [x] 协议 HTML 由 `scripts/gen_legal_html.py` 从 `docs/` 下两份 md 生成，安装器以 `dontcopy`
      引 `spa-v2/public/` 同一路径 —— **安装期与产品内共用一份产物**，各存一份必有一份过时。
      CI 加 `--check` 步防「改了 md 忘了重新生成」（另一会话）
- [x] **本会话补的那部分**：`tests/test_gen_legal_html.py` 原先是未跟踪文件，差点连生成器一起
      漏提（`fe944dce` 提了生成器和 CI 的 `--check` 步，却没提守着它的测试），补提为
      `89520a3c`。又补 3 条（`c4618584`）：`--check` 的**失败侧**原先一条没测 —— CI 的整个
      门禁就压在那个非零返回上，一个永远返回 0 的 `--check` 照样是绿的。同时测了
      `core.autocrlf=true` 的干净检出不会被误判为过时
- [x] **修登出后门禁失效**（负责人报的 bug：登出后 ✕ 回来了、点遮罩也能关掉）。根因是
      硬门禁只在冷启动探一次 `/auth/status`，而登出发生在 `HubLoginPanel` 内部，不往上说
      一声，父层 `authGate` 就停在启动那次的 `passed` —— 门只在冷启动那一下存在。
      加 `onLoginStateChanged` 回调链到 `recheckAuthGate`，补 2 条回归断言
- [x] **答「是否改了协议层」：没有。** `git show --stat 82df399e` 的 12 个文件里没有
      `protocol.py`，该文件上次改动是别人的 `a8aed458`（#664）
> **⚠ 这笔改到了错的机器上，尚未在生产生效。**
> `9ed700c` 提在 **47.100.84.197**（阿里云内地节点）。但 `account.genuineknowledge.cn`
> 解析到 **8.222.255.23**（新加坡节点）—— 本 spec 第 109 行写的就是它。判据三条：
> 内地节点上 `modules/` 只有 `auth`、`analytics`，**没有 `llm`**，也没有 litellm 容器、
> 没有 `.env.upstream`；而公网 `/openapi.json` 有全部 3 条 `/llm/v1` 路由。
> 两台机器都没有 git remote，各自一份独立副本，所以 `9ed700c` 只存在于内地那台。
> **代码本身是对的**（改法与验证见下），只是要由有权限的人在新加坡节点上重放一遍。
> 我没有 8.222.255.23 的授权（免密授权当时只给了内地那台），没去连。

- [x] **云端 `Message` 日志**（本仓之外，`/srv/psi-cloud`，commit `9ed700c`，**内地节点**）：两个 non-OK
      分支都补 `Message` 与 `RequestId`（找阿里云工单唯一能对上的凭据）。`check_code` 那支
      原先**一行日志都没有** —— 调用失败与「码填错了」在日志里长得一样，而用户两边看到的
      都是「验证码不正确」。字段名用 `api_code` 不用 `code`：那个方法里 `code` 是用户填的
      验证码，日志里出现 `code=` 会让人以为把验证码打出去了
- [x] 云端验证：容器内桩掉 `_call` 跑两支，手机号仍走 `mask_phone`，验证码明文未出现。
      **镜像必须重建**（`build: .`，没有 src 挂载，重启不生效），重建后 `/healthz` 200、容器 healthy
- [x] **修「验证码通过后闪一屏空账户面板」**（负责人报的体感问题：只有窗框和标题，一秒多后
      自己关闭）。根因在本会话自己写的 `refresh()`：先 `setStage('done')` 把 C1 渲染出来，
      再去 `getAuthMe()` + `listAuthDevices()`，等这两个请求回来才关窗 —— 那一秒多就是两个
      往返。给 `refresh` 加 `enterAccount` 参数，关窗那两条路径传 `false`，只探登录态、不进
      C1，顺带把登录路径上的这两个请求也省了。
      **修完 5 条测试红了，查了才发现它们是靠这个 bug 过的** —— 冒烟夹具把 `show` 钉死为真、
      `onClose` 是空函数，面板压根关不掉，于是 4 条用例用「在面板里登录一次」当到达 C1 的
      铺垫，而按原型 D4 登录成功就该关窗回工作台，那个落点不存在。改为 `seedLoggedIn()` 直接
      置已登录态（侧栏点进来看账户才是 C1 的真实入口），并补 1 条回归断言这条路径不渲染 C1
- [ ] **安装器协议页仍未实测。** 本机没装 Inno Setup，编译那一半改由 CI 收 ——
      `haitun-inno-setup` job 是 `on: push` 无分支过滤，推特性分支就会编译，**结果待看**。
      A3–A6（勾选门禁、两个链接打开且带样式、离线可读、`ScaleY` 0/48/72/108 的排版）
      必须在 Windows 上装一遍真包，**只有负责人能收**

**这里踩到的一个坑**：本机裸 `python` 是 **3.7.9**，跑生成器会报
`TypeError: 'type' object is not subscriptable`（`tuple[LegalDoc, ...]`）。CI 走 `uv run python`
（3.14）没这问题。**这不是生成器的 bug** —— 本仓的 Python 命令一律用 `.venv/Scripts/python.exe`
或 `uv run`。

**psi-cloud 仓没有任何测试套件**（全仓 0 个 `test_*.py`），且那台机器上没有 ruff、装不上
（容器到不了 PyPI）。所以云端那笔只做了语法检查与 88 列扫描 + 桩跑实证，**ruff 没跑**。

---

## 收尾

- [x] 提交：本仓只提交 spec 与本 plan 及客户端代码改动，**不提交其它文档**
- [x] 提交说明须写明：加独立容器改了 `docker-compose.yml`，属「独立容器」类变更，不违反「新增业务模块不改编排」的契约
- [x] 服务器改动无 remote 可推，在服务器本地 commit 并打 tag `llm-gateway-2026-08-14`
- [x] 把 spec 的「客户端改动」节按 Task 7 的修正更新（三向同步：spec ←→ 代码不能对不上）
- [x] 已合并 origin/main（`448af493`，4 处冲突）。`_auth_manager.py` 的 `TCPConnector` 取并集
      （keepalive + dns cache + 显式 `ssl=client_ssl_context()`，后者少了会「全超时而 curl 秒回」）；
      `gateway/__init__.py` 删掉上游在 8b 处重复的 AuthManager 装配，保留本分支 6b 处那份 ——
      它还接了 `aim._resolve_key`，必须排在 AI 恢复之前
- [x] 全量验证：gateway 200 passed / 2 skipped、ai 17 passed、spa-v2 167 passed、build 通过、
      ruff 干净、211 文件 formatted、ty 干净、生成器 `--check` 一致、`.iss` 的 BOM 仍在
- [ ] **PR 需负责人自己开**（本机没装 `gh`，Program Files / chocolatey / AppData 都查过）：
      https://github.com/genuineknowledge/psi-agent/pull/new/feat/llm-gateway
- [ ] 交付文档补 A/T 两段：A 段只放路径与 commit，T 段贴 A1–A8 的验证证据（A8 与 A3 是手工证据）

## 已知开放项（归属他人，不阻塞本 plan 前 8 个任务）

1. 上线用哪把 key —— 截图里那把 `sk-d56c...` 在群里明文流转过且本次调查用它验证过，视为已泄露，应另生成
2. `scripts/dev-feishu.ps1:19` 是否受影响（Task 9 的前置）
3. `/srv/psi-cloud` 无 git remote、服务器上唯一副本 —— 优先级高于本方案，建议单独排
