---
name: code-explainer
description: 详细解释代码文件，融入完整的项目架构——文件角色、数据流、关联关系、关键函数、修改入口。当用户询问"解释一下这个文件"、"这段代码是干什么的"、"怎么改"、或想要在架构上下文中理解特定源码时加载。
category: developer-productivity
---

# Code Explainer

## 原则

- **输出深度取决于问法中的关键词**，不依赖用户主动声明身份。
- **先定位再解释**：每级输出第一句都是"这个文件属于哪一层"。
- **用读代码工具验证**：解释前先用 `head` / `rg` / `cat` 确认文件存在，不凭记忆。

## 输出级别

### 级别一：完整详细版（触发词：逐行解释、剖析、完整解释、详细解析）

适用场景：新人需要最完整的输出，包含架构定位图 + 每个函数逐段解释 + 修改引导。

```
# 文件: <路径>

## 架构定位
<项目全链路图，标出这个文件的位置>
<这个文件在链路上负责哪一段>

## 归属与角色
<属于哪一层、在该层中负责什么>

## 关键结构
<主要的 class 和 async def 函数，每个一句话说明职责>

## 数据流（含跨层关系）
<这个文件的数据怎么走：输入从哪来 → 内部处理 → 输出交到哪>
<每一步用到的外部模块/上一层/下一层>

## 关联模块
<调用了哪些模块、被哪些模块调用、共享了哪些公用组件>

## 关键代码段说明
<用户关注的具体函数或关键代码块的逐段解释>
<每段解释后，附一句"如果你想改 XX，这里就是你该动的地方">
```

### 级别二：精简概览版（触发词：解释一下这个文件、这个文件是干什么的）

适用场景：已经知道文件归属，不需要再画架构图，但要快速了解整体结构。

```
归属层 | 文件职责
关键类/函数一句话版 | 数据流概览
关联模块 | 不建议改的范围
如果担心太长可直接问我更具体的部分
```

### 级别三：函数聚焦版（触发词：这段代码是干什么的、这个函数怎么实现的）

适用场景：已经知道文件归属，想了解具体函数的实现。

```
归属层一句话
关键函数（行号 + 一句说明）
关键代码段逐段解释（行号范围 + 职责 + 如果改这里会怎样）
不动区域提醒
```

### 级别四：改动定位版（触发词：改哪里、怎么改、改动点在哪里）

适用场景：已经知道代码逻辑，只想知道具体改哪里和不能碰哪里。

```
归属层
关键文件 | 关键函数 | 行号
改动点描述
不动区域
测试命令
```

## 示例

### 级别一：完整详细版

用户："逐行解释 runtime/_manager.py"

```
# 文件: runtime/_manager.py

## 架构定位
Gateway 进程的整体结构：
  Gateway 进程
  ├─ runtime/ 层 —— 实例生命周期管理
  │   ├─ AIManager → 管理 AI 实例生命周期
  │   ├─ SessionManager → 管理 Session 实例生命周期
  │   ├─ RouterManager → 管理 Router 实例生命周期
  │   └─ TitleManager / SummaryManager → 管理会话标题与摘要
  └─ gateway/ 层 —— HTTP 骨架
      └─ server.py → REST API 路由注册
          ↑
   runtime/ 各 Manager 都依赖同一个底层工具文件：
   runtime/_manager.py —— 共享工具函数

_manager.py 在 runtime/ 层中处于最底层——它不调用任何人，
而是被各 Manager 模块共同依赖。

## 归属与角色
属于 runtime/ 层内部工具模块（文件名以 _ 开头，表示不对外暴露）。
不包含任何业务逻辑，只提供 6 个纯工具函数。

## 关键结构
_new_uuid()              生成短 UUID（去掉连字符的 hex 形式），用于标识 AI/Session/Router 实例
_noop()                  空异步函数，作为 persist 回调的默认值
_socket_path()           根据平台生成 socket 文件路径：
                          Linux → /tmp/psi/{kind}/{id}.sock
                          Windows → \\.\pipe\{prefix}\{kind}\{entity_id}
_ensure_socket_dir()     创建 socket 文件的父目录（仅 Linux）
_remove_socket()         删除残留的 socket 文件（仅 Linux）
_wait_socket()           轮询等待 socket 就绪（一直 ping 直到能连上）

## 数据流（含跨层关系）
_manager.py 本身没有数据流——它是一个纯工具库，不维护状态，不产生数据。

但各 Manager 的 create() 方法有固定的调用顺序，展示了 runtime 层与 AI/Session 层的握手：
  _new_uuid() → _socket_path() → _ensure_socket_dir()
  → 【启动目标进程】→ _wait_socket() → 注册到 _entries
  → 失败时 _remove_socket()

  _wait_socket() 这里跨越了 Gateway → AI/Session 的边界：
  Gateway 启动子进程后，需要通过 socket 等待对方就绪
  → 这是 runtime 层和 AI/Session 层之间唯一的「握手」环节

## 关联模块
被调用于（实测 5 个导入方）：
  runtime/_ai_manager.py       ← 6 个函数全部用到
  runtime/_session_manager.py  ← 6 个函数全部用到
  runtime/_router_manager.py   ← 6 个函数全部用到
  runtime/_title_manager.py    ← _noop 仅作默认回调
  runtime/_summary_manager.py  ← _noop 仅作默认回调

调用了：
  sys.platform            → 判断操作系统（决定 socket 还是 pipe）
  uuid.uuid4()            → 生成唯一 ID
  aiohttp                 → NamedPipeConnector / UnixConnector / ClientSession
  anyio.Path / anyio.sleep → 异步文件操作 + 等待

不依赖于 runtime 层的任何其他模块，也不依赖 gateway 层。

## 关键代码段说明

### _socket_path() 约 L29-32
def _socket_path(prefix, kind, entity_id):
    if sys.platform == "win32":
        return rf"\\.\pipe\{prefix}\{kind}\{entity_id}"
    return f"/tmp/{prefix}/{kind}/{entity_id}.sock"

这是最简单的函数，但它承载了一个重要架构决策：
所有组件通信都通过 socket 文件或命名管道，不依赖 TCP 端口。
不同项目用不同文件路径互不干扰，没有端口冲突。
→ 如果你想改 socket 文件的存放位置（比如不想用 /tmp/），就在这里改。

### _wait_socket() 约 L51-74
async def _wait_socket(path, timeout_sec=_SOCKET_READY_TIMEOUT_SECONDS):
    ... 平台分支选择 connector ...
    deadline = anyio.current_time() + timeout_sec
    session = aiohttp.ClientSession(connector=connector)
    try:
        while anyio.current_time() < deadline:
            try:
                async with session.get("http://localhost/") as _resp:
                    pass
                return
            except Exception:
                await anyio.sleep(0.1)
        raise TimeoutError(...)
    finally:
        with anyio.CancelScope(shield=True):
            await session.close()

① 平台分支：Windows 走 NamedPipe，其他走 Unix socket
   → 对应 AGENTS.md 中 socket 传输的跨平台约定
   → 不要简化这个分支，否则 Windows 上会报 NotImplementedError

② deadline 循环：每 0.1 秒 ping 一次，超过 timeout_sec 就抛 TimeoutError
   → 上限来自模块级 _SOCKET_READY_TIMEOUT_SECONDS（L17，当前 120s）
   → 改重试间隔动 anyio.sleep(0.1)；改超时上限动 L17 或调用时传 timeout_sec
   → 这个上限不能去掉：服务起不来时没有上限会把整个 Gateway 请求挂死

③ CancelScope(shield=True)：即使外部 cancel 信号来了，
   也要保证关闭连接、不泄露资源
   → 这是 psi-agent 全框架的统一约定
   → 不要删 shield，否则 cancel 时会泄露 aiohttp 连接

### _remove_socket() 约 L40-48
async def _remove_socket(path):
    if sys.platform == "win32":
        return
    ...

Windows 上直接 return——命名管道由 OS 自动清理，不需要手动删。
→ 这个 return 分支不要删。POSIX 侧要删的原因见根 AGENTS.md「关键注意事项」第 1 条
  （socket 文件残留，进程退出不自动删）；而框架刻意不做 auto-unlink 的原因见同文档
  「为什么 socket 文件不自动 unlink？」一节（保留文件才能热换 Server）。
  所以清理是显式动作、只在 POSIX 做，两边都不能顺手"统一"掉。

### _new_uuid() 与 _noop() 约 L20-26
这两个最简单，但体现了一个设计习惯：
_noop() 用作默认回调值，而不是 None。
这样所有调用处不用写 if callback:，符合 Default over None 原则。
→ 一般不需要改这两处。
```

### 级别二：精简概览版

用户："解释一下 runtime/_manager.py"

```
归属: runtime/ 层内部工具模块（_ 开头 = 不对外暴露）
职责: 提供 6 个纯工具函数：ID生成 / socket路径 / 目录创建 / 文件删除 / 就绪等待 / 空回调
关键结构:
  _socket_path()    — 跨平台路径生成（核心架构决策：通信走 socket/pipe 不走 TCP）
  _wait_socket()    — 轮询握手（runtime 层和 AI/Session 层的唯一握手点）
  _ensure_socket_dir() / _remove_socket() — 目录创建 + 残留清理
  _new_uuid() / _noop() — 工具函数
数据流: 无自身数据流，被各 Manager 的 create/delete 流程按顺序调用
关联: 被 runtime/ 下 _ai_manager / _session_manager / _router_manager / _title_manager / _summary_manager 导入
不建议改: _new_uuid / _noop 不需要动；_remove_socket Windows return 不要删

可继续问我更具体的函数，如"解释一下 _wait_socket 怎么实现的"
```

### 级别三：函数聚焦版

用户："解释一下 _wait_socket 这个函数怎么实现的"

```
归属: runtime/_manager.py

_wait_socket(path: str, timeout_sec: float = _SOCKET_READY_TIMEOUT_SECONDS)  @约 L51-74
职责: 轮询等待 socket/pipe 就绪
调用者: 实测 4 处 —— _ai_manager.py L113 / L202、_router_manager.py L194、_session_manager.py L172

L53-58: 平台分支——Windows 走 NamedPipe，其他走 Unix socket
        → 对应 AGENTS.md 中 socket 传输的跨平台约定
L60-71: deadline 循环——每 0.1 秒 ping 一次，超时抛 TimeoutError
        → 改重试间隔在循环里；改超时上限动 L17 的 _SOCKET_READY_TIMEOUT_SECONDS
          或在调用处传 timeout_sec，不必改本文件
L72-74: CancelScope(shield=True) 保护 session.close()
        → psi-agent 全框架的取消安全统一约定
        → 不要删 shield，否则 cancel 时会泄露连接

不动区域：
- _new_uuid() 和 _noop() 不需要改
- Windows 分支判断不要简化
```

### 级别四：改动定位版

用户："给 socket 等待加超时改哪里"

```
归属: runtime/_manager.py
文件: _manager.py
函数: _wait_socket()
行号: L51-L74
改动点: 超时已实现——上限是模块级 _SOCKET_READY_TIMEOUT_SECONDS（L17，当前 120s），
        由 timeout_sec 参数默认值传入，循环按 deadline 判定。
        要改默认上限动 L17；要让某个调用方单独用别的值，传 timeout_sec 即可，不用改本文件。
不动区域: CancelScope(shield=True) 不要动
测试: 需通过 AIManager/SessionManager 的集成测试验证，见 tests/integration/
```


## 兜底规则

用户请求未命中任何级别的触发词时，默认走**级别二（精简概览版）**。

选择理由：级别一的输出量太大（完整架构图 + 逐行代码），不适合作为默认；级别三/四需要用户有明确的目标函数或改动意图；级别二是信息密度与阅读成本之间的平衡点，用户觉得不够可以追问升级。

## 不要

- 不要在级别二/三/四里画架构定位图——用户没有要求就不要画
- 不要在没有读文件前就开始解释——先用 `head` / `rg` / `cat` 确认
- 不要假定用户知道该问什么——级别一结尾加上"可以继续问我更具体的"引导

