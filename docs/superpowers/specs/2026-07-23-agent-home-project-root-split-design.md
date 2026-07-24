# Agent 路径 / AppData / 工作区 — 设计说明

**日期**: 2026-07-23  
**状态**: 当面拍板稿（覆盖此前「工具区/记录区/D:/agent」等草案）  
**尚未**写入根 `AGENTS.md` 为现行开发原则

---

## 1. 总览

| 概念 | 是什么 | 落点 |
|------|--------|------|
| **Agent 路径** | 不同配置的 Agent（能力积木包） | 今日仓内 `examples/`（如 `haitun-workspace`）；**后续改名**即可，语义不变 |
| **AppData** | 本机全局数据，跨 session | 由 **`platformdirs`** 解析的应用数据根（见 §1.1）；其下再分 `state/`、`history/` |
| **工作区** | 用户当前工程目录 | **用户选取**（Cursor 式 open folder）；不装 Agent 积木，也不默认塞 history/state |

AppData 内同时存两层（今日分散在「工作区 histories」与「Gateway 进程旁 state/」）：

1. **history** — 各会话对话记录（原 `histories/*.jsonl` 一类）  
2. **state** — Gateway **跨 session** 的文件库（见 spa-v2 树上的 `state/`：`latest.json` + 时间戳快照，持久化 AI / Session 注册 / 标题等，**不是**聊天 JSONL）

history 侧因条目多、场景杂，用 **`meta.json`** 做索引：每出现一条新历史就更新，记下该历史的**名字**、所属 **workspace**、所属 **agent**。

### 1.1 AppData 根：用 `platformdirs`，禁止写死 `%AppData%`

上级评审约定（刻意为之）：

- **禁止**在代码或安装逻辑里手写 `%AppData%\Haitun`、`~/Library/...`、`~/.local/share/...` 等平台路径。  
- **必须**用已有依赖 **`platformdirs`**（`pyproject.toml` 已有 `platformdirs>=4.10.1`；Channel 下载目录、Gateway PathPicker 快捷位置已在用）。  
- 推荐形态（实现时可收拢到一小段共享 helper，参数名以实现 PR 为准）：

```python
from platformdirs import user_data_dir

app_data_root = user_data_dir(appname="Haitun", appauthor=False)
# Windows / macOS / Linux 各自落到规范的用户应用数据目录
# 其下再：{app_data_root}/state/ … 与 {app_data_root}/history/ …
```

- 允许 CLI / 环境变量**覆盖**该根（便于测试与便携安装）；未覆盖时默认走 `platformdirs`。  
- 文档与示意图里若出现「AppData」字样，均指 **platformdirs 解析结果**，不是 Windows 环境变量字面量。

---

## 2. 目录树（示意）

```text
<repo>/
└── examples/                               （Agent 路径 ← 后续改名）
    ├── haitun-workspace/                   （某一种 Agent 配置）
    │   ├── tools/  skills/  schedules/ …
    │   ├── systems/  flows/  bin/ …
    │   └── SOUL.md  USER.md  …             （静态能力；不放 history/state）
    └── <other-agent>/                      （以后多种配置并列）

{platformdirs.user_data_dir(...)}/          （AppData 全局根 ← 勿写死 %AppData%）
├── state/                                  （跨 session 文件库 ← 今日 Gateway state/）
│   ├── latest.json                         （当前：ais / sessions / titles 等）
│   └── YYYYMMDD-HHMMSS.json                （启动/持久化快照，与现逻辑同类）
│
└── history/                                （会话记录 ← 今日 workspace/histories）
    ├── meta.json                           （索引：每条历史的 name / workspace / agent）
    └── <session_id>.jsonl                  （或按需分子目录；正文仍按会话存）

D:/任意用户文件夹/                            （工作区 ← 用户选取；可空）
└── （仅用户文件）
```

说明：

- **不要**再把 history 写进 Agent 包或用户工作区。  
- **state** 以 spa-v2 / Gateway 现行 `state/` 为准理解：跨会话恢复用的注册表与快照，不是聊天流。  
- AppData 把 **state + history 两层收拢到同一全局根** 下，便于安装版与换工作区。  
- 全局根 **只**通过 `platformdirs`（+ 可选覆盖）得到。
---

## 3. `history/meta.json`（约定意图）

新建一条历史（新 session / 新 jsonl）时更新。条目至少包含：

| 字段 | 含义 |
|------|------|
| 历史 id / 文件名 | 对应哪条 history |
| name | 展示名（可与标题同步或独立） |
| workspace | 所属工作区路径（用户选取） |
| agent | 所属 Agent（对应 `examples/` 下哪套配置，或稳定 id） |

用途：列表、按工作区过滤、按 Agent 过滤；避免「打开一个工作区却扫不清哪些历史属于它」。

具体 JSON schema 实现阶段再定；本文只定**职责与更新时机**。

---

## 4. 与今日实现的对应

| 今日 | 改后 |
|------|------|
| `examples/haitun-workspace` 当「工作区」整包用 | 明确为 **Agent 路径** 下的一套配置；工作区另选 |
| `{workspace}/histories/{id}.jsonl` | **AppData/history/** |
| Gateway cwd 下 `state/latest.json`（spa-v2 树可见） | **AppData/state/** |
| Session 列表主要靠 state 里 sessions + 各处 history | history 用 **meta.json** 补齐 name / workspace / agent |
| 前端「选工作区」= 换能力包根 | 「选工作区」= 只换用户目录；能力来自所选 Agent |

---

## 5. 读/写地址（最小改动面）

| 数据 | 读/写根 |
|------|---------|
| tools / skills / schedules / system / 人格 MD | **Agent 路径**（当前某 `examples/<agent>/`） |
| 对话 history 文件 | **AppData/history/** |
| history 索引 | **AppData/history/meta.json**（新历史时更新） |
| Gateway ais/sessions/titles 与快照 | **AppData/state/** |
| 用户文件与产出落盘 | **工作区**（用户选取） |

积木内容不重做；主要是**换根路径 + 补 meta + AppData 收拢 state/history**。

---

## 6. 验收直觉

- 换工作区：Agent 能力仍在；列表能靠 meta 按 workspace 筛历史。  
- 换/多 Agent：meta 能区分历史属于哪个 agent。  
- 工作区目录内不出现 `histories/`、`state/`。  
- Gateway 重启后仍能从 AppData/state 恢复跨 session 注册信息。

---

## 7. 待拍板 / 后续

1. ~~AppData 用哪套路径 API~~ → **已定：`platformdirs.user_data_dir`**；待定仅剩 appname 字符串（建议 `Haitun`）与是否允许 env 覆盖  
2. `examples/` 改名为何（如 `agents/`）及多 Agent 选择 UI  
3. meta 与现有 `/titles`、state.sessions 的字段如何去重合并  
4. 旧 `workspace/histories` 与 cwd `state/` 的一次性迁移  
5. Schedule display 的 user kind 问题见另文 `2026-07-23-schedule-display-user-kind.md`（正交）
---

## 8. 废止

此前草案中的 `D:/agent/tools-area`、`records-area`、「记录挂能力包旁」等表述，**以本文为准全部覆盖**。

---

## 9. 程序读取逻辑与接口 — 最小改动清单（可见方案）

目标：积木内容尽量不动；只改「从哪读 / 写到哪」和会话字段。下列按层列出**精确到文件**的涉及面（自然语言说明改什么）。

### 9.1 Session：一条路径拆成三条

| 文件 | 改什么 |
|------|--------|
| `src/psi_agent/session/__init__.py` | `Session` 今日只有 `workspace`。改为能传入 **agent 路径**、**用户工作区**、**AppData（或 history/state 根）**；空值时给默认（agent → 现 haitun-workspace 或配置；history/state → AppData；workspace → 用户目录）。`run()` 里解析后传给 `SessionAgent.create`。 |
| `src/psi_agent/session/agent.py` | `create()` 今日用同一 `workspace_path` 加载 conversation / tools / schedules / system。改为：**tools、schedules、system** 只从 **agent 路径** 取；**conversation** 只从 **AppData/history** 取；并把 **用户工作区** 注入运行时（供工具用）。`reload`/refresh 仍扫 agent 下 tools、schedules。 |
| `src/psi_agent/session/conversation.py` | `from_workspace`：今日写 `{workspace}/histories/{id}.jsonl`。改为写 **AppData/history/{id}.jsonl**；新建历史时顺便更新同目录 **meta.json**（name、workspace、agent）。方法可改名以表意，逻辑是换根 + 维护 meta。 |
| `src/psi_agent/session/system_prompt.py` | `from_workspace`：仍读 `{根}/systems/system.py`，但根改为 **agent 路径**（不再是用户工作区）。 |
| `src/psi_agent/session/tool_registry.py` | 加载目录改由调用方传入 **agent/tools**（文件内部可不动，只改传入路径）。 |
| `src/psi_agent/session/schedule_registry.py` | 同上，**agent/schedules**；注释里「workspace/schedules」措辞一并改掉。 |
| `src/psi_agent/session/runtime_context.py` | 在现有 `session_id` ContextVar 之外，增加（或等价注入）**当前用户工作区**、**agent 路径**、**AppData/history 根**，供 workspace 工具读取，避免再猜 `__file__`。 |

### 9.2 Gateway：state / history / 会话字段

| 文件 | 改什么 |
|------|--------|
| 新建共享小模块（建议 `src/psi_agent/_app_paths.py` 或放 gateway 内 helper） | 封装 `platformdirs.user_data_dir(appname=..., appauthor=False)` → AppData 根；再拼 `state/`、`history/`；支持可选覆盖。**禁止**字符串写死 `%AppData%`。 |
| `src/psi_agent/gateway/_state.py` | 今日默认 `state/latest.json`（相对进程 cwd）。改为默认落在 **`user_data_dir(...)/state/`**；load/save 语义不变（ais / sessions / titles + 时间戳快照）。 |
| `src/psi_agent/gateway/__init__.py` | 用上述 helper 构造 `GatewayState` 路径；恢复 Session 时把 **agent / workspace / history 根** 传全；CLI 可覆盖 AppData 根与默认 agent 路径。 |
| `src/psi_agent/gateway/_session_manager.py` | `SessionInfo` / `create()`：今日只有 `workspace`。扩展为至少区分 **用户工作区** 与 **agent 路径**（命名实现自定）；创建 `Session(...)` 时两套都传入；`state` 快照里 sessions 条目同步带上这些字段；`get_workspace` 语义改为「用户工作区」，必要时新增 `get_agent` 一类读取。 |
| `src/psi_agent/gateway/_history_manager.py` | 今日 `Path(workspace)/histories/{id}.jsonl`。改为读 **AppData/history/**；delete 同路径；列表/过滤可走 **meta.json**。 |
| `src/psi_agent/gateway/_todo_manager.py` | 今日 `{workspace}/.psi/todos/{id}.json`。最小方案：改到 **AppData** 下与该 session 记录并列的位置（或 history 旁约定子路径），**不要**再写进用户工作区。 |
| `src/psi_agent/gateway/server.py` | `POST /sessions`：body 里 `workspace` = 用户工作区；增加可选 **agent**（默认 Gateway 默认 agent）；创建后写 history meta。`GET .../history`、`.../todos`：改用 AppData 路径，不再 `get_workspace` 拼 histories。OpenAPI 文案同步。 |
| `src/psi_agent/gateway/_openapi.py` | Session 模型与创建参数：补充 agent；workspace 说明改为「用户工作区」；todos/history 描述去掉「在 workspace 目录下」。 |
| `src/psi_agent/gateway/_feishu_manager.py` | 今日每用户一个「整包 workspace」。改为：共享默认 **agent**；每用户 **工作区或记录分区** 按产品定；不要再复制整份 examples 当用户根。 |

### 9.3 前端 spa-v2：选文件夹 ≠ 选 Agent

| 文件 | 改什么 |
|------|--------|
| `src/psi_agent/gateway/spa-v2/src/App.tsx` | `gw-v2-workspace` 只表示用户工作区；默认不要再当成 haitun-workspace 能力包。 |
| `src/psi_agent/gateway/spa-v2/src/components/WorkspaceGate.tsx` | 文案：选的是工程目录，不是 Agent；去掉「tools/history 落在该目录」类表述。 |
| `src/psi_agent/gateway/spa-v2/src/services/api.ts` | `createSession`：继续传 `workspace`（用户目录）；增加传 **agent**（或依赖服务端默认）。Session 类型字段与后端对齐。 |
| `src/psi_agent/gateway/spa-v2/src/haitun-agent/*`（建任务 / 列表过滤处） | 按 **用户工作区** 过滤任务；能力包路径不再等于工作区。交付物读盘仍用工作区作 `root`。 |
| `src/psi_agent/gateway/spa-v2/AGENTS.md` | 落地后改映射表与默认工作区说明（实现 PR 同步）。 |

（spa v1 若仍维护：同类改 `api` / 选工作区文案；可二期。）

### 9.4 Agent 包内工具：区分「能力根」与「用户工作区」

| 文件 | 改什么 |
|------|--------|
| `examples/haitun-workspace/tools/_background_process_registry.py`（及各处 `resolve_workspace`） | 今日：`WORKSPACE_DIR` 或 `__file__` 上溯 = 整包根。拆成：**用户工作区**（文件读写默认）vs **能力包根**（skills/schedules/flows 等）；优先读 Session 注入的 ContextVar / 环境变量。 |
| `examples/haitun-workspace/tools/` 下 `skill_manage.py`、`schedule_manage.py`、`flow_manage.py` 等 | 改能力积木 → 相对 **agent 路径**；`read`/`write`/`bash` 等 → 相对 **用户工作区**。 |
| `examples/haitun-workspace/tools/_todo_store.py`（及 Gateway Todo 约定） | 待办改写 **AppData** 侧，与 history 同属全局记录，不写用户工作区。 |
| `examples/haitun-workspace/systems/system.py` | 扫 skills / 人格 MD：相对 **agent 路径**；不要因用户换工作区而丢能力。 |

### 9.5 接口一览（对外可见变化）

| 接口 / 持久化 | 最小变化 |
|---------------|----------|
| `POST /sessions` | `workspace` = 用户工作区；新增可选 `agent`（默认 Gateway 配置的 examples 下某套）。 |
| `GET /sessions` | 返回项带齐 workspace + agent（及既有 id、ai_id、channel_socket）。 |
| `GET /sessions/{id}/history` | 仍该 URL；内部改读 AppData/history；展示过滤逻辑可不变。 |
| `GET /sessions/{id}/todos` | 仍该 URL；内部改读 AppData 侧待办路径。 |
| `state/latest.json` | 内容结构可先不变；**文件位置**迁到 `user_data_dir(...)/state/`；sessions 条目增加 agent 等字段。 |
| **新增** `{app_data}/history/meta.json` | 非必须新 HTTP；Gateway 创建/恢复会话时维护即可。若前端要按工作区列历史，可后续加只读索引 API。 |
| Gateway CLI | 可选覆盖 AppData 根、默认 agent 路径；**默认根必须来自 platformdirs**。 |

### 9.6 建议不碰（本阶段）

- 不重命名/搬迁 `examples/haitun-workspace` 内部 tools、skills 目录结构（改名 `examples/` → `agents/` 可另做）。  
- 不改 AI 层、Channel SSE 协议。  
- Schedule 的 display user kind（另文）可并行，不阻塞本方案路径拆分。  
- 不引入第二套「手写 AppData 路径」逻辑（与 Feishu/Telegram 已用的 platformdirs 风格对齐即可）。

### 9.7 建议施工顺序

1. **`platformdirs` AppData helper** + `GatewayState` 迁路径 + `Conversation`/`HistoryManager` 迁 history + meta 写入  
2. `Session`/`SessionManager`/`POST /sessions` 拆 agent vs workspace  
3. spa-v2 只绑用户工作区 + 传 agent 默认  
4. haitun 工具 `resolve_*` 与 todo 落点  
5. 文档：`session/AGENTS.md`、`gateway/AGENTS.md`、`spa-v2/AGENTS.md`、haitun `AGENTS.md` 与本文对齐  

### 9.8 开工分工（worktree）

| 树 | 先做 |
|----|------|
| 框架 / Gateway（本仓 `src/psi_agent`，可与 workflow 或独立 feat 分支） | §9.1–9.2、`_app_paths` + state/history 迁根 |
| `Haitun-develop-spa-v2` | §9.3 |
| `Haitun-develop-workspace` | §9.4 |
| `Haitun-develop-workflow` | 方案与 AGENTS 落盘（本文）；少改业务代码 |