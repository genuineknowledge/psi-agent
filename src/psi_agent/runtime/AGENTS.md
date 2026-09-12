# Runtime 层设计文档

## 概述

Runtime 持有 **AI / Session / Router 三类实例的注册表与生命周期**，以及从这些实例的落盘产物投影出前端可渲染结构的四个只读/轻写 manager（title / summary / history / todo）。

这一层认识的最高层概念**只有内核**：`psi_agent.session`、`psi_agent.ai`、`psi_agent.router`、`psi_agent.protocol`、`psi_agent.channel._core`。它知道怎么 spawn 一个 Session、怎么解析 Router 的类型化依赖、怎么把 JSONL 历史投影成前端能渲染的结构；它**不**认识任何接入形态——没有网页界面、没有飞书、没有桌面托盘、没有登录。

## 依赖方向（这个包存在的意义）

```
gateway/  ──→  runtime/  ──→  session/ ai/ router/ protocol/ channel._core
（REST + SPA        （实例注册表          （内核）
  + 飞书 + 认证）      + 生命周期）
```

**方向单一，永不回头。** gateway 组装这些 manager 并把它们接到 REST + Web UI 上（`gateway/server.py`、`gateway/__init__.py`），`gateway/feishu/_feishu_manager.py` 复用 `SessionManager` 给每个飞书用户按需 spawn 独立 Session。runtime 反过来对 gateway 一无所知。

这条边由一条可执行的闸门守着，改动本包后必须为空：

```bash
git grep -n "from psi_agent.gateway" -- src/psi_agent/runtime/   # 必须无输出
```

包外共享助手（`psi_agent._appdata` 路径运算、`psi_agent._workspace_paths` 工作区路径机制、`psi_agent._sockets` 传输解析）**刻意**放在 gateway 与 runtime 两个包之外，正是为了让本包不必为了拿一个路径去 import 产品线包。ToC 品牌字面量（`haitun交付` / `agents/feishu`）留在 `gateway/_defaults.py`，由调用方**作为参数传入**——所以建 Session 的 `SessionManager` 不反向依赖产品线。

## 命名

`psi_agent.runtime`（顶层包）与 `psi_agent.session.runtime_context`（`session` 包下的模块）只是字面相近，在 Python 命名空间里**互不遮蔽**，全库 4 处 `runtime_context` 导入点也都是全限定写法（`from psi_agent.session.runtime_context import ...`）。两者语义也不同：本包管的是「实例的注册与生死」，`runtime_context` 管的是「单次 agent 运行期内的 ContextVar 作用域」。

## 模块

| 文件 | 职责 |
|------|------|
| `_manager.py` | 共享 helpers（_new_uuid/_noop/_socket_path/_ensure_socket_dir/_remove_socket/_wait_socket） |
| `_ai_manager.py` | `AIManager` — AI 实例注册表 + 生命周期 + AiInfo |
| `_router_manager.py` | `RouterManager` — Router 实例注册表、类型化 AI/Router 依赖解析和生命周期管理 |
| `_session_manager.py` | `SessionManager` — Session 实例注册表 + 生命周期 + SessionInfo（含 `agent`、`active_schedules` / `deactive_schedules`） |
| `_scheduler_manager.py` | `SchedulerManager` — 每个 workspace 恰好一个**全量激活**（`active_schedules=("*",)`）的调度 Session，按需 spawn（跳过的 workspace 由常驻 `watch_loop` 每 30s 重查，首个定时任务出现即自动拉起），对 SPA / state 隐藏 |
| `_title_manager.py` | 会话标题 CRUD + AI 自动生成 |
| `_summary_manager.py` | 任务摘要 CRUD + AI 自动生成（spa-v2；与 title 同级持久化） |
| `_chat_manager.py` | SSE 流式对话管理（复用 ChannelCore） |
| `_history_manager.py` | JSONL 历史读取（``{appdata}/histories/{session_id}.jsonl``，legacy ``{workspace}/histories/`` 双读；delete 两侧都清） |
| `_todo_manager.py` | 会话 todo 列表读取（``{appdata}/todos/{session_id}.json``，legacy ``{workspace}/.psi/todos/`` 双读） |

各 manager 的行为细节、Socket 路径约定、`_wait_socket` 120s 超时的由来、SchedulerManager 的「定时任务归 workspace，触发权归 session × schedule」不变量、免费模型的 key 替换钩子，仍记在 `gateway/AGENTS.md` 对应小节——那里同时讲了 REST 侧的接线，拆开会让两边都读不完整。

## `SessionManager.create` 的 workspace 判据（拒绝，而非兜底）

`create()` 末尾有一行兜底：`workspace = workspace.strip() or self._default_workspace or os.getcwd()`。对 ToC / 桌面端这是对的（一台机器一个人一个工作区）；对多人共用一个 Gateway 的接入形态，它会让**忘记传 workspace 的调用方**静默把所有人的产出写进同一个公共目录。

〔生产实测〕63 个飞书会话里 14 个 `feishu-ou_*` 形状的会话 workspace 指向 `/workspace` 根目录而非各自子目录，根目录已散着约 290 个混放文件。而**初始成因至今未定**：`FeishuManager.route()` 那条路吃不到这个兜底（它传的 workspace 永远非空），所以另有一条建 `feishu-ou_*` 形状 id 却不给 workspace 的路径。

所以判据放在 `create()` 里，而不是去堵某条已知路径：这是**每条建 session 的路径都必经的唯一收口**，不知道是谁写的那条路也照样过不去。

```python
_guarded_id_prefix: str = ""       # 空 = 判据不启用
_guarded_workspace_root: str = ""
```

- **两个字段由调用方注入**，本包不认识 `"feishu-"` 这个产品词。`Gateway.run` 传 `_guarded_id_prefix=FEISHU_SESSION_PREFIX, _guarded_workspace_root=self.feishu_workspace_root`（`gateway/__init__.py`）。默认空 → 判据整段不启用，ToC / 桌面端的兜底行为逐字节不变。
- **判据必须在那行 `or` 兜底之前。** 兜底一旦生效，「调用方到底给没给 workspace」这个信息就永久丢了——那正是那 14 个会话落进公共区时发生的事。
- 命中前缀且 **没给 workspace → 抛 `ValueError`**，不去派生一个默认值。内核不知道那三种目录形状的派生规则（`<root>/<open_id>` / `chat-<chat_id>` / `.private/<open_id>`），硬猜只会造出第 4 种形状。派生是 `FeishuManager.workspace_for` 的职责。
- 命中前缀且 workspace **不严格位于 root 之下 → 抛 `ValueError`**。判定走 `_workspace_paths.is_strictly_under`（基于 `os.path.relpath`），不是裸 `startswith`：后者放过 `<root>/../evil`、`<root>-evil` 兄弟目录，Windows 上跨盘符还会抛 `ValueError`。**root 自身不算「在 root 之下」**——那恰恰是生产上那个坏形状。纯路径运算、不 resolve 符号链接（要能判还不存在的路径）。
- **`skip_workspace_guard=True` 只给 state 恢复用**（`gateway/__init__.py` 里那处 `sm.create`）。给恢复也上判据的话，那 14 个人重启后会被**静默迁走**：历史按 session_id 存在 appdata 里不会丢，但他们过去的产出仍在根目录那 290 个文件里，agent 从此看不见自己写过的东西。让问题可见是这一步的目标，改数据是另一个独立决定。

用例见 `tests/psi_agent/gateway/test_session_workspace_guard.py`（9 条：root 下合法通过、缺 workspace 拒、workspace 等于 root 拒、root 外与 `..` 穿越拒、`<root>-evil` 兄弟目录拒、群聊与私密区两种合法形状通过、非命中前缀的 id 仍走兜底、未配置时判据不启用、恢复豁免且 workspace 不被改写）。

## 测试

测试仍在 `tests/psi_agent/gateway/`（`test_manager.py` / `test_session_manager.py` / `test_router_manager.py` / `test_scheduler_manager.py` / `test_history_manager.py` / `test_todo_manager.py` / `test_summary_manager.py` / `test_chat_manager.py`）与 `tests/integration/test_gateway.py`。**没有随代码搬家**：它们大量经 `create_core_app()` 走 REST 断言，本质是 gateway 装配后的行为，搬过来反而要把 aiohttp 装配也搬过来。跑子树注意 `-o testpaths=` 必须写在路径**之前**：

```bash
.venv/Scripts/python.exe -m pytest -o testpaths= tests/psi_agent/gateway tests/integration/test_gateway.py -q --no-cov
```
