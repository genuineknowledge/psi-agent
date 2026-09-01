# 登录连接复用 实施计划

## 目标

把登录每一步的网络耗时从冷连接 600–1000ms（3 RTT）降到热连接 ~210ms（1 RTT），不改任何认证语义。

三处改动：

1. **连接池配好**：给 `AuthManager._ensure_session()` 传显式 `TCPConnector`，keepalive 与 DNS 缓存都撑过等短信的间隔。
2. **预热**：Gateway 启动时、SPA 探 `/auth/status` 时，各发一次无副作用的 `GET /me` 把连接焐热，用 Gateway 现成的 anyio task group。
3. **重试边界**：幂等 GET 遇连接被对端关闭重试一次；四个业务 POST 永不重试。

设计文档：`docs/superpowers/specs/2026-08-14-auth-connection-reuse-design.md`
任务文档（含验收标准 A1–A7）：`docs/onboarding/psi-agent 登录延迟优化.md`

## Global Constraints

- **异步一律走 anyio**，`asyncio` 原生 API 与 `pathlib` 禁用。
- **零抑制**：不写 `noqa`，不加 `per-file-ignores`。代码本身要过 lint。
- **类型检查是 `ty`，不是 mypy** — `# type: ignore` 无效，不要写。
- ruff：行长 120，target py313。新模块首行 `from __future__ import annotations`。
- **不加 `enable_cleanup_closed`**：Python 3.14.7 下已是 no-op 且抛 `DeprecationWarning`，本仓库禁止抑制。
- **四个业务 POST 永不自动重试**：`/sms/send`、`/otp`、`/verify/*`、`/compl`。验证码被消耗两次，前端 D1 兜底屏会说「验证码不正确」，而码是对的。
- 不改 SPA、不改错误码表、不改两段式注册流程、不动 `tempToken` 不出进程的约束。
- 单测替换一律走 `monkeypatch.setattr`，不要直接赋值 `m._call = fake`（签名不兼容，`ty` 会拒）。
- 异步测试照 `test_auth_manager.py` 的写法：`@pytest.mark.anyio`（已验证可用）。

## 基线（2026-08-14 实测）

按文件计（避开下面那个 `--cov` 陷阱后的真实数字）：

| 文件 | 基线 |
| --- | --- |
| `test_auth_manager.py` | 6 passed |
| `test_auth_store.py` | 7 passed |
| `test_auth_connection.py` | 新建 |

全仓 `uv run pytest` 有 50+ 个既有失败，全在 `tests/psi_agent/session/` 与 `tests/integration/`，与本次改动无关。**每个任务只需保证 auth 三个文件全绿**，不要试图修那些既有失败。

常用命令：

```
uv run ruff check src/psi_agent/gateway/_auth_manager.py tests/psi_agent/gateway/test_auth_connection.py
uv run ruff format src/psi_agent/gateway/_auth_manager.py tests/psi_agent/gateway/test_auth_connection.py
uv run ty check
```

`ty check` 目前有 **2 个既有 error**（`agents/feishu/tools/run_flow.py` 的 `os.killpg`，Windows 上没这个函数），与本次改动无关。判据是**数量不增**，不是归零。

### ⚠ 跑单个测试文件必须覆盖 addopts

`pyproject.toml` 的 `addopts` 里有个**裸 `--cov`**。它接可选值，于是会把紧跟其后的第一个路径参数**当成自己的值吞掉** —— `uv run pytest tests/xxx.py` 会静默变成跑全量 1299 个测试（其中 50+ 个既有失败，且要跑 5 分钟以上）。

所以本计划里所有测试命令都写成：

```
uv run pytest -o addopts="--strict-markers -ra" tests/psi_agent/gateway/test_auth_connection.py -q
```

（传两个以上路径时 `--cov` 只吞掉第一个，看起来「正常」，但第一个文件其实没被跑到 —— 更隐蔽，别依赖这种写法。）

## 涉及文件

| 文件 | 改什么 |
| --- | --- |
| `src/psi_agent/gateway/_auth_manager.py` | 连接池常量与 connector、`_attempt`/`_call` 拆分与重试、预热字段与方法、`create()` 收 `tg` |
| `src/psi_agent/gateway/__init__.py` | 建 `AuthManager` 时注入 task group 并触发首次预热 |
| `src/psi_agent/gateway/server.py` | `_auth_status` 顺手 nudge 预热 |
| `tests/psi_agent/gateway/test_auth_connection.py` | 新建。连接与传输层的测试，与 `test_auth_manager.py` 的「响应改造」关注点分开 |
| `src/psi_agent/gateway/AGENTS.md` | 三向同步：连接池取值、预热触发点、POST 不重试约束 |

## Task 1：连接池配置

**为什么**：`_auth_manager.py:143-146` 建 `ClientSession` 时没传 connector，于是吃 aiohttp 默认的 `keepalive_timeout=15`。而登录每一步之间的间隔（输手机号 5–20s、等短信 30–90s）都超过 15s——代码里「复用 `self._session`」成立，网络层一次都没复用上。

**Files**：`src/psi_agent/gateway/_auth_manager.py`、`tests/psi_agent/gateway/test_auth_connection.py`（新建）

### Step 1 先写失败的测试

新建 `tests/psi_agent/gateway/test_auth_connection.py`：

```python
"""连接与传输层的行为。

与 test_auth_manager.py 分开: 那边测「云端响应怎么被改造成前端契约」,
这边测「连接怎么建、怎么复用、什么时候重试」。两个关注点，两个文件。
"""

from __future__ import annotations

import pytest

from psi_agent.gateway._auth_manager import (
    _DNS_CACHE_SECONDS,
    _KEEPALIVE_SECONDS,
    AuthManager,
)


@pytest.mark.anyio
async def test_session_connector_keeps_connection_across_sms_wait(tmp_path) -> None:
    """连接池的 keepalive 必须撑过等短信的间隔, 否则每步都要重新握手。"""
    m = await AuthManager.create("https://example.invalid", appdata_root=tmp_path)
    try:
        session = m._ensure_session()
        connector = session.connector
        assert connector is not None
        # 等短信最长约 90s; keepalive 必须比它长, 否则连接在等待期间就被回收了。
        assert _KEEPALIVE_SECONDS > 90.0
        assert connector._keepalive_timeout == _KEEPALIVE_SECONDS
        # 云端地址不变, 没必要每 10s 重新解析一次 DNS。
        assert _DNS_CACHE_SECONDS >= 600
        assert connector._ttl_dns_cache == _DNS_CACHE_SECONDS
        # 同一个 session 复用同一个 connector, 不能每次调用都新建。
        assert m._ensure_session().connector is connector
    finally:
        await m.aclose()
```

跑 `uv run pytest tests/psi_agent/gateway/test_auth_connection.py -q --no-cov`，预期 `ImportError: cannot import name '_KEEPALIVE_SECONDS'`。

### Step 2 加常量

`_auth_manager.py`，紧跟在 `_TIMEOUT_SECONDS = 30.0`（第 98 行）后面：

```python
# 连接保活时长。aiohttp 默认 15s, 撑不过登录任一步的间隔:
# 输手机号 5-20s、等短信 30-90s。默认值下每一步都是冷连接 (TCP 1 RTT + TLS 1 RTT
# + 请求 1 RTT), 境外云 RTT 约 210ms, 每步白付约 420ms。
# 取值比服务端空闲超时短, 避免池里留着对端已关的连接 (实测见 gateway/AGENTS.md)。
_KEEPALIVE_SECONDS = 120.0

# DNS 缓存。默认 10s, 云端地址不变, 没必要反复解析。
_DNS_CACHE_SECONDS = 600
```

### Step 3 传 connector

改 `_ensure_session()`：

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

`ClientSession` 默认持有该 connector，`aclose()` 里 `session.close()` 会一并关掉，清理逻辑不用改。

### Step 4 验证并提交

**已完成**（commit `16d720e5`）。三个 auth 文件合计 14 passed（13 基线 + 1 新增），ruff 干净，`ty` 无新增。

实施时踩到两处，已修正：`ttl_dns_cache` 不在 connector 上（`connector._ttl_dns_cache` 不存在），实际落在 `connector._cached_hosts._ttl`；`session.connector` 的静态类型是 `BaseConnector | None`，要先 `assert isinstance(connector, aiohttp.TCPConnector)` 收窄，`ty` 才认那些私有字段。

提交只带这两个文件，**不要带 `AGENTS.md`**（工作区里那处改动是别人的）：

```
perf(auth): 登录连接复用, 冷连接 3 RTT → 热连接 1 RTT

问题: _ensure_session() 没传 connector, 吃 aiohttp 默认 keepalive=15s。
登录每步间隔 (输号 5-20s、等短信 30-90s) 都超过 15s, 代码里复用 session,
网络层一次没复用上, 每步白付 TCP+TLS 两个 RTT (境外云约 420ms)。

改法: 显式 TCPConnector, keepalive 120s、DNS 缓存 600s。
不加 enable_cleanup_closed — Python 3.14.7 下已 no-op 且抛
DeprecationWarning, 本仓库禁止 noqa 抑制。

验证: 新增 test_auth_connection.py 断言 keepalive 撑过等短信间隔、
connector 被复用。keepalive 具体取值待实测 (A3)。
```

## Task 2：重试边界

**为什么**：keepalive 拉长后，池里的连接有更大概率在对端已经被关掉。aiohttp 取连接时会检查，但存在窄窗口——取出时看着活着，请求发出前对端关了，抛 `ServerDisconnectedError`。这个只能靠重试兜。**但重试只能给幂等 GET**，业务 POST 重试会把验证码消耗两次。

**Files**：`src/psi_agent/gateway/_auth_manager.py`、`tests/psi_agent/gateway/test_auth_connection.py`

**依赖**：Task 1（同一测试文件）

### Step 1 先写失败的测试

追加到 `test_auth_connection.py`。先加导入 `aiohttp`、`from unittest.mock import ANY` 不需要，用计数器即可：

```python
@pytest.mark.anyio
async def test_idempotent_get_retries_once_on_stale_connection(tmp_path, monkeypatch) -> None:
    """池里的连接被对端关掉时, GET 重试一次就能成功, 用户看不到失败。"""
    m = await AuthManager.create("https://example.invalid", appdata_root=tmp_path)
    m._token = "tok"
    calls: list[str] = []

    async def fake_attempt(
        method: str, path: str, payload: dict[str, Any] | None = None, *, auth: bool = False
    ) -> tuple[int, dict[str, Any]]:
        calls.append(path)
        if len(calls) == 1:
            raise aiohttp.ServerDisconnectedError
        return 200, {"ok": True}

    monkeypatch.setattr(m, "_attempt", fake_attempt)
    try:
        status, body = await m.me()
        assert status == 200
        assert body == {"ok": True}
        assert len(calls) == 2  # 第一次撞死连接, 第二次拿新连接成功
    finally:
        await m.aclose()
```

`fake_attempt` 的签名与真实 `_attempt` 逐字对齐（含 `*` 与默认值），这样 ruff 与 `ty` 都不需要抑制。文件顶部导入需补 `from typing import Any` 与 `import aiohttp`。

### Step 2 POST 不重试的测试

四个业务 POST 逐个覆盖。`complete()` 需要先有 `_pending_temp_token`，否则它在 `_call` 之前就返回 400：

```python
@pytest.mark.anyio
@pytest.mark.parametrize(
    "op",
    ["send_code", "verify", "complete", "bind"],
)
async def test_business_post_never_retries(tmp_path, monkeypatch, op: str) -> None:
    """业务 POST 永不重试。重试会把验证码消耗两次, 用户拿着对的码却过不去。"""
    m = await AuthManager.create("https://example.invalid", appdata_root=tmp_path)
    m._token = "tok"
    m._pending_temp_token = "tmp"  # complete() 的前置
    calls: list[str] = []

    async def fake_attempt(
        method: str, path: str, payload: dict[str, Any] | None = None, *, auth: bool = False
    ) -> tuple[int, dict[str, Any]]:
        calls.append(path)
        raise aiohttp.ServerDisconnectedError

    monkeypatch.setattr(m, "_attempt", fake_attempt)
    ops = {
        "send_code": lambda: m.send_code(phone="13800000000"),
        "verify": lambda: m.verify(code="123456", phone="13800000000"),
        "complete": lambda: m.complete(display_name="x"),
        "bind": lambda: m.bind(code="123456", phone="13800000000"),
    }
    try:
        status, body = await ops[op]()
        assert status == 0
        assert body["error"] == "upstream_unreachable"
        assert len(calls) == 1  # 只发一次, 绝不重试
    finally:
        await m.aclose()
```

### Step 3 抽出 `_attempt`

把现在 `_call`（`_auth_manager.py:148-191`）里 `try:` 内部那一整段搬进新方法 `_attempt`，逐字保留：`session.request`、JSON 解析、裸数组装 `{"items": ...}`、非 dict 兜底、`Retry-After` 抄进 body。**唯一区别**：连接异常不在这里吞，往上抛给 `_call` 决定要不要重试。

```python
    async def _attempt(
        self, method: str, path: str, payload: dict[str, Any] | None = None, *, auth: bool = False
    ) -> tuple[int, dict[str, Any]]:
        """发一次请求并把响应改造成前端契约。连接异常往上抛, 由 ``_call`` 决定重试。"""
        headers: dict[str, str] = {}
        if auth:
            if not self._token:
                return _UNAUTHORIZED, {"error": "unauthorized"}
            headers["Authorization"] = f"Bearer {self._token}"
        url = f"{self.endpoint}{self.prefix}{path}"
        session = self._ensure_session()
        async with session.request(method, url, json=payload, headers=headers) as resp:
            ...  # 原 167-188 行整段搬过来, 不改一个字
```

### Step 4 重写 `_call`

```python
    async def _call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        auth: bool = False,
        retry: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """发一次云端请求, 返回 ``(状态码, 响应体)``。

        网络异常收敛成 ``(0, {"error": ...})`` —— 调用方 (HTTP 路由) 据此回 502,
        而不是让异常冒到 aiohttp 中间件变成 500。

        ``retry`` 只对幂等 GET 生效, 且必须在调用点显式开启。业务 POST 永不重试:
        验证码被消耗两次后, 前端 D1 兜底屏会说「验证码不正确」, 而码是对的。
        """
        if not self.endpoint:
            return 0, {"error": "auth_endpoint_not_configured"}
        attempts = 2 if (retry and method == "GET") else 1
        last: Exception | None = None
        for i in range(attempts):
            try:
                return await self._attempt(method, path, payload, auth=auth)
            except aiohttp.ServerDisconnectedError as e:
                # 只认这一种: keepalive 拉长后, 池里的连接可能在取出与发出之间
                # 被对端关掉。不能捕 ClientOSError 或 ClientConnectionError ——
                # ServerDisconnectedError 与 ClientConnectorError (DNS 失败、连接
                # 被拒) 都是它们的子类, 罩上去会把真正连不通的情况也重试一遍,
                # 白等一个超时周期。
                last = e
                if i + 1 < attempts:
                    logger.info(f"连接已被对端关闭, 重试一次 {method} {path}")
                    continue
            except Exception as e:
                last = e
                break
        logger.warning(f"认证服务请求失败 {method} {path}: {last!r}")
        return 0, {"error": "upstream_unreachable", "detail": repr(last)[:200]}
```

### Step 5 只给两个 GET 开重试

- `me()`（第 300 行）：`self._call("GET", "/me", auth=True, retry=True)`
- `list_devices()`（第 310 行）：`self._call("GET", "/sessions", auth=True, retry=True)`

DELETE 不开。`revoke_device` / `unbind` 按 HTTP 语义算幂等，但重试拿到 404 会让界面说「设备不存在」，而第一次其实成功了——误导比省一个 RTT 重要。

### Step 6 验证并提交

预期 `test_auth_connection.py` 6 passed（Task 1 的 1 + 本任务的 1 + 4 个参数化），`test_auth_manager.py` 仍 6、`test_auth_store.py` 仍 7。三件套（ruff check / format / ty check）全过。

```
fix(auth): 重试边界, 幂等 GET 重试一次, 业务 POST 永不重试

问题: keepalive 拉长后, 池里的连接可能在取出与发出之间被对端关掉。
不兜就是一次用户可见的失败; 兜过头就是验证码被消耗两次。

改法: _call 拆出 _attempt。只捕 ServerDisconnectedError —— 不捕
ClientOSError/ClientConnectionError, 因为 ClientConnectorError (DNS 失败、
连接被拒) 也是它们的子类, 罩上去会把真正连不通的也重试, 白等一个超时。
retry 必须在调用点显式开启且只对 GET 生效, 防止以后给新 POST 顺手加上。
DELETE 也不开: 重试拿到 404 会说「设备不存在」, 而第一次已经成功。

验证: 新增 5 个测试, 四个业务 POST 逐个断言只发一次。
```

## Task 3：预热能力

**为什么**：连接池只在**发过一次请求之后**才有连接可复用。用户点「获取验证码」是这个进程里的第一次请求，必然冷。趁用户还在看界面时先把连接建好，这一次点击就能落在热连接上。

本任务只做 `AuthManager` 侧的能力，接线在 Task 4。

**Files**：`src/psi_agent/gateway/_auth_manager.py`、`tests/psi_agent/gateway/test_auth_connection.py`

**依赖**：Task 2（`_attempt` 已存在）

### Step 1 先写失败的测试

```python
class _FakeTaskGroup:
    """只记下被 start_soon 的协程并立刻跑掉, 够测预热的调度与节流。"""

    def __init__(self) -> None:
        self.started = 0

    def start_soon(self, func: object, *args: object) -> None:  # 签名照 anyio.TaskGroup
        # 只计数, **不调用 func** —— 调了会造出一个没人 await 的协程,
        # 冒出 RuntimeWarning: coroutine was never awaited。
        self.started += 1


@pytest.mark.anyio
async def test_warm_401_does_not_clear_credentials(tmp_path, monkeypatch) -> None:
    """预热必须绕开 _on_response, 否则每次预热都会把已登录用户踢下线。"""
    m = await AuthManager.create("https://example.invalid", appdata_root=tmp_path)
    m._token = "real-user-token"

    async def fake_call(
        method: str, path: str, payload: dict[str, Any] | None = None, *, auth: bool = False, retry: bool = False
    ) -> tuple[int, dict[str, Any]]:
        assert auth is False  # 预热不带 token
        return 401, {"error": "unauthorized"}

    monkeypatch.setattr(m, "_call", fake_call)
    try:
        await m._warm()
        assert m._token == "real-user-token"  # 没被清掉
    finally:
        await m.aclose()
```

### Step 2 预热不能拖垮 Gateway 的测试

```python
@pytest.mark.anyio
async def test_warm_swallows_errors_and_resets_flag(tmp_path, monkeypatch) -> None:
    """预热失败必须自己吞掉: 异常逃出 start_soon 会拆掉整个 task group, 连带杀死
    Gateway。且失败后要复位 _warming, 否则一次失败永久堵死后续预热。"""
    m = await AuthManager.create("https://example.invalid", appdata_root=tmp_path)

    async def boom(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        raise RuntimeError("network down")

    monkeypatch.setattr(m, "_call", boom)
    try:
        await m._warm()  # 不抛
        assert m._warming is False
    finally:
        await m.aclose()


@pytest.mark.anyio
async def test_nudge_warm_throttles_consecutive_calls(tmp_path, monkeypatch) -> None:
    """SPA 挂载时可能连发几次 /auth/status, 节流保证只热一次。"""
    m = await AuthManager.create("https://example.invalid", appdata_root=tmp_path)
    tg = _FakeTaskGroup()
    m._tg = tg
    try:
        await m.nudge_warm()
        # 手工复位 _warming: 假 task group 不真跑 _warm, 标志不会自己落下。
        # 不复位的话这个用例考的是 _warming 那道闸, 而不是节流那道 —— 两道闸都要考。
        m._warming = False
        await m.nudge_warm()
        m._warming = False
        await m.nudge_warm()
        assert tg.started == 1  # 5s 内的连发只热一次
    finally:
        await m.aclose()


@pytest.mark.anyio
async def test_nudge_warm_without_task_group_is_silent(tmp_path) -> None:
    """没注入 task group 时静默跳过 —— 预热是优化, 缺了不算故障。"""
    m = await AuthManager.create("https://example.invalid", appdata_root=tmp_path)
    try:
        await m.nudge_warm()  # 不抛
    finally:
        await m.aclose()
```

### Step 3 加常量与字段

常量，跟在 `_DNS_CACHE_SECONDS` 后面：

```python
# 预热节流。SPA 挂载登录面板时可能连发几次 /auth/status, 没必要每次都热一遍。
_WARM_THROTTLE_SECONDS = 5.0
```

字段，加在 `_session` 后面（都有默认值，不破坏 dataclass 字段顺序）：

```python
    _tg: Any = None
    """Gateway 的 anyio TaskGroup (``ty`` 不识别的第三方类型)。只用来调度预热;
    没注入就不预热, 功能不受影响。"""
    _warming: bool = False
    _last_warm: float = 0.0
```

### Step 4 实现两个方法

```python
    async def nudge_warm(self) -> None:
        """请求把连接焐热。不阻塞调用方 —— 只是往 task group 里塞个任务就返回。"""
        if self._tg is None or self._warming:
            return
        now = anyio.current_time()
        if now - self._last_warm < _WARM_THROTTLE_SECONDS:
            return
        # 检查到置位之间没有 await, 协作式调度下不会被抢占, 因此不需要锁。
        self._warming = True
        self._last_warm = now
        self._tg.start_soon(self._warm)

    async def _warm(self) -> None:
        """发一次无副作用的 ``GET /me`` 把 TCP+TLS 建好。

        **不带 token** (``auth=False``): 云端回 401, 而 401 不经 ``_on_response``,
        因此不会把已登录用户踢下线。

        异常必须在这里吞掉 —— 逃出 ``start_soon`` 会拆掉整个 task group, 连带杀死
        Gateway。``_call`` 目前自己收敛异常, 但预热的代价太高, 不赌它将来不变。
        """
        try:
            await self._call("GET", "/me", retry=True)
        except Exception as e:
            logger.debug(f"连接预热失败, 忽略: {e!r}")
        finally:
            # 必须复位, 否则一次失败就永久堵死后续预热。
            self._warming = False
```

`_warm` 走 `_call` 而不是 `_attempt`，是为了让 `retry=True` 生效：预热撞上一个陈旧连接时重试一次正好拿到新连接，这本来就是它要干的事。

### Step 5 验证并提交

预期 `test_auth_connection.py` 9 passed（6 + 3），另两个文件仍 6 / 7。三件套全过。

```
perf(auth): 连接预热, 首次点击也落在热连接上

问题: 连接池只在发过一次请求之后才有连接可复用。用户点「获取验证码」
是进程里的第一次请求, 必然冷 —— 连接池配好了也救不了这一次。

改法: nudge_warm() 往 Gateway 的 task group 里塞一次无 token 的
GET /me。不带 token 是关键: 云端回 401, 而 401 不经 _on_response,
不会把已登录用户踢下线。异常在 _warm 内吞掉, 逃出 start_soon 会拆掉
整个 task group 连带杀死 Gateway; finally 复位 _warming, 否则一次
失败永久堵死后续预热。5s 节流挡住 SPA 挂载时的连发。

验证: 新增 3 个测试 —— 401 不清凭证、失败不抛且复位标志、连发只热一次。
```

## Task 4：接线

**为什么**：Task 3 造了能力，没人调。两个触发点：Gateway 启动（最早的机会）、SPA 探 `/auth/status`（登录面板挂载时必然发生，因此**不需要改前端**）。

**Files**：`src/psi_agent/gateway/_auth_manager.py`、`src/psi_agent/gateway/__init__.py`、`src/psi_agent/gateway/server.py`、`tests/psi_agent/gateway/test_auth_connection.py`

**依赖**：Task 3（`nudge_warm()` 与 `_tg` 字段；测试里的 `_FakeTaskGroup` 也在同一文件）

### Step 1 先写失败的测试

```python
@pytest.mark.anyio
async def test_create_accepts_task_group(tmp_path) -> None:
    """create(tg=...) 把 task group 存下来, 不传时行为不变。"""
    tg = _FakeTaskGroup()
    m = await AuthManager.create("https://example.invalid", appdata_root=tmp_path, tg=tg)
    try:
        assert m._tg is tg
    finally:
        await m.aclose()

    plain = await AuthManager.create("https://example.invalid", appdata_root=tmp_path)
    try:
        assert plain._tg is None
    finally:
        await plain.aclose()
```

### Step 2 `create()` 收 `tg`

签名加末位关键字参数（`_auth_manager.py:119`）：

```python
    async def create(
        cls, endpoint: str, appdata_root: str = "", platform: str = "", *, tg: Any = None
    ) -> AuthManager:
        """建一个 manager 并从磁盘恢复登录态 (满足 R3: 跨重启保持)。

        ``tg`` 是 Gateway 的 anyio TaskGroup, 只用于连接预热; 不传则不预热。
        """
```

构造时带上 `_tg=tg`。

### Step 3 Gateway 启动即预热

`__init__.py:225` 改成：

```python
                authm = await AuthManager.create(self.auth_endpoint, appdata_root=appdata_root, tg=tg)
                # 趁用户还没点「获取验证码」, 先把连接建好, 省下 TCP+TLS 两个 RTT。
                await authm.nudge_warm()
```

`tg` 就是第 147 行 `async with anyio.create_task_group() as tg:` 的那个，此处在作用域内。

### Step 4 `/auth/status` 顺手预热

`server.py:882-884`：

```python
async def _auth_status(request: web.Request) -> web.Response:
    """当前登录态。SPA 据此决定显示登录引导还是身份信息; 不含 token。

    顺手把连接焐热: SPA 挂载登录面板时必然探这个端点, 是最自然的预热时机 ——
    因此前端不用改。本身只读内存, 不打云端, 加上预热也不会变慢。
    """
    authm = _auth(request)
    await authm.nudge_warm()
    return _json(authm.status())
```

`nudge_warm()` 只是 `start_soon` 后立刻返回，不会让这个响应变慢。

### Step 5 自动化验证

预期 `test_auth_connection.py` 10 passed（9 + 1），另两个文件仍 6 / 7。三件套全过。

`ty check` 会容忍 `_tg: Any` —— 与 `_ai_manager.py:47` 同一写法（`# anyio.TaskGroup (ty不识别的第三方类型)`），照抄那条注释。

### Step 6 手工验证 Gateway 还能起来

改了 Gateway 启动路径，必须真起一次。先看清参数：

```
uv run psi-agent gateway --help
```

然后按它给出的参数起一个实例，确认：

1. 启动日志里没有 `连接预热失败` 之外的新报错，进程不退。
2. Gateway **不因为预热失败而崩** —— 这是本任务最大的风险点。可以把 `PSI_AUTH_ENDPOINT` 指到一个不存在的地址（如 `https://127.0.0.1:9`）再起一次，确认进程照旧起来、只在日志里留一条 debug。
3. 探一次 `/auth/status`，确认立刻回 JSON、不被预热拖慢。

**注意**：手工起 Gateway 会在 `state/` 落下含明文 api_key 的快照。`state/` 已在 `.gitignore` 里（`git check-ignore -v state/gateway.json` 可验证）。**不要删 `state/` 目录** —— 里面可能有别人的真实数据。

### Step 7 提交

```
feat(auth): 接线连接预热, Gateway 启动与 /auth/status 各触发一次

改法: create() 收可选 tg; __init__.py 建 manager 时注入并立刻预热;
_auth_status 顺手 nudge。选 /auth/status 是因为 SPA 挂载登录面板时
必然探它 —— 前端一行不用改。它本身只读内存不打云端, 加预热也不会变慢。

验证: 新增 create(tg=) 的测试; 手工起 Gateway 确认进程正常, 并把
PSI_AUTH_ENDPOINT 指到不可达地址复验预热失败不会拆掉 task group。
```

## Task 5：实测 keepalive（验收项 A3）

**为什么**：`_KEEPALIVE_SECONDS = 120.0` 目前是**估值**。如果服务端空闲超时比它短，池里就会攒着对端已关的连接，每次取用都要撞一次 `ServerDisconnectedError` 再重试——比不开 keepalive 还慢。这是全计划**唯一需要前置测量**的项，也是唯一可能要回改 Task 1 常量的地方。

**曾经踩过**：上一轮用「耗时 < 400ms 就算复用」反推 keepalive，得到自相矛盾的结果（2s→断、10s→复用、30s→断、65s→断、120s→复用）。原因是冷连接本身就在 600–998ms 波动，RTT 抖动盖过了判据。**必须直接观察连接事件，不能用耗时反推。**

**Files**：临时探针脚本（用完删掉）；可能回改 `src/psi_agent/gateway/_auth_manager.py` 的常量

**依赖**：Task 1（常量已存在）

### Step 1 写探针

临时文件 `scripts/_probe_keepalive.py`（**用完必须删**，别提交）。核心是 `aiohttp.TraceConfig` 的连接事件，不是计时：

```python
from __future__ import annotations

import aiohttp
import anyio

_IDLE_STEPS = [10, 30, 60, 90, 120, 180]
_URL = "https://account.genuineknowledge.cn/auth/me"


async def main() -> None:
    events: list[str] = []
    trace = aiohttp.TraceConfig()

    async def on_reuse(session, ctx, params) -> None:
        events.append("reuse")

    async def on_create(session, ctx, params) -> None:
        events.append("create")

    trace.on_connection_reuseconn.append(on_reuse)
    trace.on_connection_create_start.append(on_create)

    connector = aiohttp.TCPConnector(keepalive_timeout=600.0, ttl_dns_cache=600)
    async with aiohttp.ClientSession(connector=connector, trace_configs=[trace]) as s:
        async with s.get(_URL) as r:
            await r.read()
        print(f"首次建连: {events}")
        for idle in _IDLE_STEPS:
            events.clear()
            await anyio.sleep(idle)
            async with s.get(_URL) as r:
                await r.read()
            verdict = "复用" if "reuse" in events else "新建 (服务端已关)"
            print(f"空闲 {idle}s → {verdict} {events}")


anyio.run(main)
```

### Step 2 跑探针

```
uv run python scripts/_probe_keepalive.py
```

梯度总计约 **8.5 分钟**，Bash 默认 120s 超时不够，**必须显式给 `timeout: 600000`**。

### Step 3 按实测定值

第一次出现「新建」的那一档，就是服务端空闲超时所在区间。`_KEEPALIVE_SECONDS` 取**它下面那一档**。

- 实测 ≥ 120s 全复用 → 120.0 不动，把 Task 1 注释里的「实测见 AGENTS.md」补成实测结论。
- 实测在 60–120s 之间断 → 把常量改成断点下面那一档，并在任务文档 A 段记一条偏差说明。
- **实测低于 60s** → 停下来告诉用户。此时预热的价值下降（等短信 30–90s 必然跨过超时），是否要加心跳是一个新决策，不在本计划范围内，不要自己扩。

### Step 4 复验登录全程

把探针的 `_IDLE_STEPS` 临时改成 `[75]`（等短信的典型间隔），重跑一次，确认「发码 → 等 75s → 校验」这一程确实复用连接。

### Step 5 删探针并回写文档

```
rm scripts/_probe_keepalive.py
```

**探针必须删**：它对着真实生产地址发请求，留在仓库里迟早被人误跑。

回写两处（不要互相复制，各归其位）：

- **设计文档** `docs/superpowers/specs/2026-08-14-auth-connection-reuse-design.md` 第 2 节末尾：梯度原始数据、首次「新建」出现在哪一档、最终取值、与估值的偏差。
- **任务文档** `docs/onboarding/psi-agent 登录延迟优化.md` A 段：逐条核验 A1–A7 并附证据（测试名 / 探针输出 / commit），补上实际分支与关键 commit。

签名（创建人 / 审核人）**留空，由用户自己填** —— 不要代签。

```
docs(auth): keepalive 实测取值, 替换估值

方法: TraceConfig 的 on_connection_reuseconn / on_connection_create_start
直接观察连接事件。不用耗时反推 —— 冷连接本身在 600-998ms 波动,
RTT 抖动会盖过判据 (上一轮就是这么得出自相矛盾的结论的)。
```

## Task 6：三向同步

**为什么**：`AGENTS.md` ←→ 文档 ←→ 代码必须同步。按信息归属原则，这三条只写在 `gateway/AGENTS.md` 一处，根 `AGENTS.md` 不重复。

**Files**：`src/psi_agent/gateway/AGENTS.md`

**依赖**：Task 5（keepalive 取值已定）

### Step 1 找到落点

```
grep -n '认证\|AuthManager' src/psi_agent/gateway/AGENTS.md
```

写进已有的认证段落，**语气和结构照抄邻居** —— 那份文档是「为什么这么定」的口径，不是 API 手册。

### Step 2 补三条

1. **连接池取值**：为什么不能吃 aiohttp 默认（keepalive 15s + DNS 10s 撑不过 5–90s 的登录步距，每步白付约 420ms，占总耗时约三分之二），Task 5 的实测空闲超时，以及**不加 `enable_cleanup_closed`** 的理由（Python 3.14.7 下已 no-op 且抛 `DeprecationWarning`，本仓库禁止 noqa 抑制）。
2. **两个预热触发点**：Gateway 启动、`/auth/status` 被探。说清 `/auth/status` 为什么能当钩子（只读内存、不打云端），以及预热为什么**不带 token**（绕开 `_on_response`，否则每次预热都把已登录用户踢下线）。
3. **四个业务 POST 永不重试**：连原因一起写，防止以后有人为了「提高鲁棒性」顺手加上。验证码被消耗两次后，前端 D1 兜底屏会说「验证码不正确」，而码是对的 —— 性能优化变成正确性缺陷。

### Step 3 提交

```
docs(gateway): 同步连接复用三条约束到 AGENTS.md

连接池取值与理由、两个预热触发点、四个业务 POST 永不重试。
按信息归属只写在 gateway/AGENTS.md 一处, 根 AGENTS.md 不重复。
```

## 验收对照（A1–A7）

| 项 | 由谁保证 | 证据形态 |
| --- | --- | --- |
| A1 连接复用生效 | Task 1 | `test_session_connector_*` |
| A2 冷启动首次点击也热 | Task 3 + 4 | 预热三测 + 手工起 Gateway |
| A3 keepalive 为实测值 | **Task 5** | 探针梯度输出 |
| A4 幂等 GET 重试一次 | Task 2 | `test_idempotent_get_retries_once_*` |
| A5 业务 POST 永不重试 | Task 2 | 四个参数化用例 |
| A6 预热不影响登录态 | Task 3 | `test_warm_401_does_not_clear_credentials` |
| A7 预热失败不拖垮 Gateway | Task 3 + 4 | `test_warm_swallows_errors_*` + 不可达地址手工复验 |

三向同步归 Task 6。全部完成后把任务文档状态从「待评审」推进到「已交付」，签名留给用户填。

