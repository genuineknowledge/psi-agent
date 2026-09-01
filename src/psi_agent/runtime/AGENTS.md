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
| `_scheduler_manager.py` | `SchedulerManager` — 每个 workspace 恰好一个**全量激活**（`active_schedules=("*",)`）的调度 Session，按需 spawn，对 SPA / state 隐藏 |
| `_title_manager.py` | 会话标题 CRUD + AI 自动生成 |
| `_summary_manager.py` | 任务摘要 CRUD + AI 自动生成（spa-v2；与 title 同级持久化） |
| `_chat_manager.py` | SSE 流式对话管理（复用 ChannelCore） |
| `_history_manager.py` | JSONL 历史读取（``{appdata}/histories/{session_id}.jsonl``，legacy ``{workspace}/histories/`` 双读；delete 两侧都清） |
| `_todo_manager.py` | 会话 todo 列表读取（``{appdata}/todos/{session_id}.json``，legacy ``{workspace}/.psi/todos/`` 双读） |

各 manager 的行为细节、Socket 路径约定、`_wait_socket` 120s 超时的由来、SchedulerManager 的「定时任务归 workspace，触发权归 session × schedule」不变量、免费模型的 key 替换钩子，仍记在 `gateway/AGENTS.md` 对应小节——那里同时讲了 REST 侧的接线，拆开会让两边都读不完整。

## 测试

测试仍在 `tests/psi_agent/gateway/`（`test_manager.py` / `test_session_manager.py` / `test_router_manager.py` / `test_scheduler_manager.py` / `test_history_manager.py` / `test_todo_manager.py` / `test_summary_manager.py` / `test_chat_manager.py`）与 `tests/integration/test_gateway.py`。**没有随代码搬家**：它们大量经 `create_core_app()` 走 REST 断言，本质是 gateway 装配后的行为，搬过来反而要把 aiohttp 装配也搬过来。跑子树注意 `-o testpaths=` 必须写在路径**之前**：

```bash
.venv/Scripts/python.exe -m pytest -o testpaths= tests/psi_agent/gateway tests/integration/test_gateway.py -q --no-cov
```
