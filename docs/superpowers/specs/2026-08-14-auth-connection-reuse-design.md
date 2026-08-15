# 认证链路连接复用设计

> **目标**：把登录每次点击的延迟从 600–1000ms 压到单 RTT（~210ms），办法是不再重复支付 TCP + TLS 握手的两个 RTT。
>
> **来源**：`docs/onboarding/psi-agent 登录延迟优化.md` 的 W / H 两段（含 2026-08-14 链路实测数据与验收标准 A1–A7）。本文只写代码细节，不复制该文档的诊断与选型理由。
>
> **范围**：`gateway/_auth_manager.py` 的连接池配置与预热，`gateway/__init__.py` 一处注入。不改 SPA、不改认证语义、不引入重试。

## 设计目标

一句话：`_ensure_session()`（`src/psi_agent/gateway/_auth_manager.py:143-146`）没传 connector，于是走 aiohttp 默认 `keepalive_timeout=15s`，而登录每步间隔都超过 15 秒——连接池里的连接每次都已被回收，「复用 `self._session`」在网络层从未成立。

本设计做三件事，**行为语义零变更**：

1. 显式配置 connector，让连接活过「等短信」那段
2. 预热：用户还在看界面时就把连接建好，第一次点击也走热连接
3. 明确重试边界：幂等 GET 可重试一次，四个业务 POST 永不重试

## 一、连接池配置

`_ensure_session()` 改为显式传入 connector：

```python
# 连接保活时长。默认 15s 对登录场景完全无效 —— 用户「输手机号 → 等短信 → 输验证码」
# 每步间隔 5–90s, 15s 的池子每次都空。取值必须**低于服务端空闲超时**, 否则池里
# 会留下对端已关的连接; 实测方法见本文「二、keepalive 取值的实测方法」。
_KEEPALIVE_SECONDS = 120.0

# DNS 缓存。aiohttp 默认 10s, 而首次解析实测 200ms; 云端地址不会变, 没必要重解析。
_DNS_CACHE_SECONDS = 600
```

```python
def _ensure_session(self) -> aiohttp.ClientSession:
    if self._session is None or self._session.closed:
        connector = aiohttp.TCPConnector(
            keepalive_timeout=_KEEPALIVE_SECONDS,
            ttl_dns_cache=_DNS_CACHE_SECONDS,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS),
        )
    return self._session
```

`ClientSession` 拥有该 connector（未传 `connector_owner=False`），故现有 `aclose()`（`_auth_manager.py:136-141`）关 session 时会一并关 connector，**清理路径无需改动**。

**不加 `enable_cleanup_closed`（已实测确认）**：aiohttp 曾建议对 SSL 连接开启此项，用于绕开 CPython 一个 transport 不能正确关闭的 bug。该 bug 已在 Python 3.14 修复，因此本仓环境（aiohttp 3.14.1 + Python 3.14.7）下传入它会得到：

```
DeprecationWarning: enable_cleanup_closed ignored because
https://github.com/python/cpython/pull/118960 is fixed in Python version 3.14.7
```

即**纯 no-op 加一条告警**。仓规禁止 `noqa` 压制，故不加。

同批实测确认的另两项：`keepalive_timeout=120.0` 正常生效（读回 `connector._keepalive_timeout == 120.0`）；`ttl_dns_cache` 签名默认值确为 `10`，改成 600 有实际意义。

## 二、keepalive 取值的实测方法（验收项 A3）

`_KEEPALIVE_SECONDS = 120.0` 目前是**估值**，取的是 nginx / 负载均衡常见默认区间的保守下沿。它必须在实施时被换成实测值，理由与前车之鉴：

会话中曾用「耗时是否 < 400ms」反推连接是否复用，得到自相矛盾的结果（空闲 2s 判为断开、10s 判为复用、30s 判为断开、120s 又判为复用）。原因是冷连接耗时本身在 600–998ms 之间抖动，**RTT 抖动足以淹没这个判据**。该数据无效。

正确方法是用 `aiohttp.TraceConfig` 直接观测连接事件，不看耗时：

```python
trace = aiohttp.TraceConfig()
trace.on_connection_reuseconn.append(...)  # 复用了池里的连接
trace.on_connection_create_start.append(...)  # 新建连接 = 池里那条已失效
```

以 `keepalive_timeout` 设成远大于待测值（如 600s）的 session，按空闲梯度（10 / 30 / 60 / 90 / 120 / 180s）各发一次 `GET /auth/me`，记录每次触发的是 reuse 还是 create。**第一次出现 create 的梯度即服务端空闲超时上界**，`_KEEPALIVE_SECONDS` 取该值下方一档。

结论（实测值 + 梯度数据）写回任务文档 T 段。若实测值低于 120s，需回头修本文常量并在任务文档 A 段记录该偏差。

### 实测结果（2026-08-14）

对 `https://account.genuineknowledge.cn/auth/me` 按上述方法实测：

| 空闲时长 | 连接事件 | 判定 |
| --- | --- | --- |
| 首次 | `create` | 建连 |
| 10s | `reuse` | 复用 |
| 30s | `reuse` | 复用 |
| 60s | `reuse` | 复用 |
| 90s | `reuse` | 复用 |
| 120s | `reuse` | 复用 |
| 180s | `reuse` | 复用 |

**整条梯度没有出现过 create** —— 服务端空闲超时比 180s 还长，梯度没能测到它的上界。

因此 `_KEEPALIVE_SECONDS = 120.0` **不需要改**，且这个取值现在有了比原估值更强的保证：客户端会先于服务端回收连接，池里不会攒下对端已关的连接。这正是「取值须比服务端空闲超时短」那条约束想要的方向。

没有继续往上加 keepalive，因为登录全程的最大间隔约 90s（等短信），120s 已经完整覆盖；再加只是让空闲连接占着资源更久，换不到任何用户可感知的收益。

### 冷热耗时实测（同日）

| | 实测 |
| --- | --- |
| 冷连接 | 740 / 992 / 711ms，均值 **814ms** |
| 热连接 | 208 / 232 / 213ms，均值 **218ms** |
| 每次节省 | **597ms** |

比设计阶段估的 420ms 更多。差额来自冷连接不止 3 个 RTT —— TLS 证书链校验等握手开销也一并省掉了。热连接均值 218ms 与实测 RTT 226ms 吻合，即热路径已经压到「一个 RTT」这个物理下限，客户端侧没有进一步可榨的空间。

## 三、预热

### 触发点

两处，都不阻塞响应：

| 时机 | 位置 | 覆盖的场景 |
|---|---|---|
| Gateway 启动 | `gateway/__init__.py:225` `AuthManager.create()` 之后 | 用户开机即登录 |
| SPA 探测 `/auth/status` | `server.py:882` `_auth_status` | 用户过很久才打开登录面板 |

选 `/auth/status` 作第二触发点是因为它**本来就是本地调用、不打云端**（`status()` 只读内存，`_auth_manager.py:355-366`），SPA 在登录面板挂载时必然探它（`spa-v2/src/services/api.ts:376`）。挂在这里前端一行不用改，也不必新增端点。

### 机制：复用 Gateway 的 task group

仓内 manager 已有注入 task group 的惯例——`_ai_manager.py:47` 声明 `_tg: Any`，`:104` 用 `self._tg.start_soon(...)`。`AuthManager` 照此加 `_tg`，由 `gateway/__init__.py:147` 那个 `tg` 注入，**不自造 memory object stream 之类的新机制**。

```python
def nudge_warm(self) -> None:
    """请求预热一次。同步返回, 不阻塞调用方 (``/auth/status`` 要立即回响应)。"""
```

`_warm()` 发一次 `GET /me` 且 `auth=False`：

- 不带 token → 云端回 401，**无副作用**
- 走 `_call` 但**不经 `_on_response`**，因此这个 401 不会误清用户的本地凭证（`_on_response` 才是清凭证的那处，`_auth_manager.py:193-197`）
- 无论登录与否都可发，行为一致

### 两条必须守住的约束

**1. 异常绝不能逃出 `start_soon`。** anyio task group 里任一子任务抛异常会拆掉整个 group，即**预热失败会连带打死 Gateway**。`_call` 自身已 `except Exception` 收敛成 `(0, {...})`（`:189-191`），但 `_warm()` 仍需自己再兜一层——这里的代价是整个进程，不值得赌上游实现不变。对应验收项 A6。

**2. 并发与频次要挡住。** 用 `_warming` 标志位 + `_last_warm` 时间戳，做到：同一时刻只有一个预热在飞；距上次预热不足 `_WARM_THROTTLE_SECONDS`（5s）则直接跳过。SPA 可能连续探 `/auth/status`，不挡会连发。5 秒只是防抖，与 keepalive 无关。

**副作用提示**：预热会给云端增加 `GET /me` 请求。若云端将来对 `/me` 按 IP 限流，节流后最坏频率是每 5 秒一次，风险低，但值得在此留档。

## 四、重试边界

调长 keepalive 会让「池里的连接对端已关」这个窗口变大。aiohttp 的 connector 从池里取连接时会检查存活并丢弃已死的，所以**系统性失效已被它挡住**，剩下的是「取出瞬间还活着、请求发出时被关」的窄竞态。

对这个窄竞态，只允许一种处置：

| 请求 | 遇 `ServerDisconnectedError` / `ClientOSError` | 理由 |
|---|---|---|
| `GET /me`（含预热）、`GET /sessions` | **重试一次** | 幂等，重试无副作用 |
| `POST /sms/send`、`POST /otp` | **不重试** | 可能真的发出了短信，重试即二次发送，还会撞限频 |
| `POST /verify/*`、`POST /complete`、`POST /identities/*` | **不重试** | 见下 |

**verify 类绝不重试的具体理由**（不只是「POST 非幂等」这条通则）：`spa-v2/src/services/authFlow.ts:238-249` 记录了已踩过的坑——D1 是兜底屏、文案一律「验证码不正确」，任何后端异常都会被显示成用户抄错了码。若 verify 重试导致验证码被消耗两次，用户看到的是「验证码不正确」而码完全正确，只会一遍遍重输。**把性能优化变成正确性缺陷，比慢 400ms 糟得多。**

实现上，`_call` 增一个仅内部可见的重试判据（HTTP 方法为 GET 且异常属于连接类），默认关闭、由调用点显式开启，避免「将来有人新增 POST 时默认继承了重试」。

## 五、测试计划

新增 `tests/psi_agent/gateway/test_auth_connection.py`。沿用既有测试惯例（`test_auth_manager.py` 开头的 docstring）：一律 `monkeypatch.setattr` 顶替，不直接赋值方法（签名不兼容会被 `ty` 拦，而 `# type: ignore` 是 mypy 语法，本仓用 `ty`，压不住）。

| 用例 | 断言 | 对应验收项 |
|---|---|---|
| connector 配置生效 | session 的 connector 上 `keepalive_timeout == 120.0`、`ttl_dns_cache == 600` | A1 |
| 预热发的是无副作用请求 | `_call` 收到 `("GET", "/me")` 且 `auth=False` | A4 |
| 预热异常不外溢 | `_call` 抛异常时 `nudge_warm()` 不抛，且 `send_code` 仍正常 | A6 |
| 节流生效 | 5 秒内连续 `nudge_warm()` 三次，`_call` 只被调 1 次 | A4 |
| 预热的 401 不清凭证 | 已登录态下预热回 401，`status()["loggedIn"]` 仍为 True | A4 |
| 四个 POST 不重试 | 注入连接错误，`_call` 调用次数恒为 1（逐个 POST 参数化） | A5 |
| 幂等 GET 重试一次 | 注入一次连接错误，第二次成功，最终返回 200 | A5 |

连接复用的端到端验证（A2、A3）用 `TraceConfig` 对真实云端测，属手动验证，不进 CI——CI 不该依赖外网。方法见第二节，结论贴任务文档 T 段。

回归：`uv run pytest tests/psi_agent/gateway/`（A7）。

## 六、三向同步

按《真知开发规范 SOP》的信息归属原则，本次改动要同步的是 `src/psi_agent/gateway/AGENTS.md`——认证连接策略只属于 gateway 层，根 `AGENTS.md` 不重复。

需写入 gateway 的 `AGENTS.md`：

- 认证客户端的 keepalive / DNS 缓存取值及**为什么不能照抄 aiohttp 默认**
- 预热的两个触发点，以及「`/auth/status` 是本地调用，故可安全用作预热钩子」
- 「四个业务 POST 永不重试」这条硬约束及其理由（避免后人为了鲁棒性顺手加上）

## 七、不做什么

- 不加心跳保活。它要引入常驻定时任务，而 connector 的存活检查已挡住系统性失效，剩下的窄竞态用「GET 可重试 + POST 不重试」覆盖。**触发条件**：若实测发现服务端空闲超时短到连一次「等短信」都撑不过（< 60s），心跳才重新进入考虑。
- 不动 `_TIMEOUT_SECONDS = 30.0`（`:98`）。它管单请求上限，与握手开销无关。
- 不引入 HTTP/3。服务端已通告 `alt-svc: h3=":443"`，能再省握手 RTT，但 aiohttp 不支持 h3，换客户端库的代价远超收益。
范围层面的排除项（不做国内边缘接入 / 不迁服务 / 不改 SPA 与认证语义）及其理由归任务文档 W 段第 4 节，此处不复述。


