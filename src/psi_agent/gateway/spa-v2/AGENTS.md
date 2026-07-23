# SPA v2 — 任务驱动工作台（Gateway 联调版）

> 与 `../spa/`（对话气泡 v1）并行。UI 来自任务/宝箱设计包；**运行时已接 Gateway**：
> Task ≈ Session，对话走 `/sessions/{id}/chat` SSE，历史走 `/history`。
>
> **Gateway 默认控制台**：`GET /` → `/spa-v2/index.html`（`--browser` / webview 打开根地址即 v2）。v1 仍在 `/spa/`。

**并行开发**：改本目录时建议单独一棵 `git worktree` + 独立功能分支；勿与 workspace/后端施工共挂同一分支。约定见仓库根 `WORKTREE.md` 与 `AGENTS.md`（「本地并行开发」）。

## 与 spa v1

| | spa (v1) | spa-v2 |
|--|----------|--------|
| 产品隐喻 | Session 对话气泡 | 任务卡片 + 交付物宝箱 |
| 技术栈 | Vue 3 + Pinia | React 19 + Vite |
| base | `/spa/` | `/spa-v2/` |
| 对话 | Gateway SSE | 同左（同一套 API） |
| 交付物 | 气泡 blob chip | 宝箱 UI；SSE `blob` 写入 `deliverables`；抽屉内按 blob 真实渲染 MD/HTML/图片/文本（无 blob 时明确空态，非占位纸面） |
| 账户区 | 头像菜单合一 | 头像菜单仅资料/登录；**模型池**与**设置**为侧栏独立快捷入口 |
| 默认工作区 | 无 / 必须先选 | 无记忆时用 Gateway **cwd**（从 haitun-workspace 启动即该目录）；遗留字面量 `workspace` 会忽略 |
| 工作区切换 | 侧栏打开 PathPicker | 设置「切换工作区」→ 选择页；**浏览**按钮走 `/workspace/places` + `/browse`（对齐 v1） |
| 顶栏新建 | — | 右上角「新建任务」+ 侧栏同入口（`⌘/Ctrl N`） |

设置弹窗暂时只保留**切换工作区**（真实功能）；通知/交付位置等占位项已去掉，避免空壳菜单。
| 任务删除 | 侧栏 trash → DELETE session + 清本地 hist | 侧栏/卡片删除 → ``DELETE /sessions/{id}``（顺带清 JSONL + 标题）+ 清本地状态 |
| 消息操作栏 | 助手：赞/踩/复制/重新生成；用户：复制 + 失败重试 | 同左（`FocusChatThread`）；feedback 仅内存态，刷新历史后不保留 |
| 停止生成 | 输入栏 Send ↔ Stop 切换 | 同左：流式时发送键变为停止（`abortRef.abort()`） |

## 映射

```text
任务卡          ↔  Gateway Session（同 workspace）
新建任务        ↔  POST /sessions + POST /titles + 首条 chat SSE；**首条发送后立刻进入分屏聚焦**（左上下文 / 右对话），不再停在新建页本地气泡
卡片内对话      ↔  POST /sessions/{id}/chat（multipart chunks）
任务历史文案    ↔  GET /sessions/{id}/history
任务卡中间步 N/M ↔  GET /sessions/{id}/todos（workspace ``todo`` tool → `.psi/todos/{id}.json`）
打开即用 AI     ↔  空池先开模型面板；「免费」清空配置；对话时惰性 POST `/ais`（远程默认）
```

**模型选择（防踩坑）**：启动 / 新建任务不盲选 `ais[0]`。池里若已有真实 key，会清掉残留的 `haitun-default` 占位项，并优先用户选中（localStorage）的 AI；仅空池才走免费远程。Session 创建时固定 `ai_id`——已绑坏 AI 的旧任务需新建。

### 任务卡三步进度（分层）

上层只判定生命周期阶段，下层再填推进细节：

| 层 | 职责 |
|----|------|
| **阶段** `phase` | `advance` 推进 → `deliver` 产出与确认 → `done` 本轮完成（`taskProgress.resolveTaskProgress`） |
| **推进细节** | 有 workspace `todo` → 中间步 `N/M` + 当前项文案；**无 todo** → 中间步固定「推进中」 |
| **投影** | `applyTaskProgress` 唯一写入口，生成 `steps` / `progress` / `updated` |

- 流式中：无 todo 保持 `advance`+「推进中」；todo 全部完成则进入 `deliver`（右侧「产出与确认」working）。
- 回合成功结束：`turnSettled=true` → `done`，三步全勾；有交付物进度 100%。
- 加载历史若已有 assistant 回复，同样 `withCompletedTurn` 投影为 `done`。
- 空 todo 轮询**不会**把已 `done` 的卡打回推进中（保留 `turnSettled`）。

### 对话气泡操作（对齐 spa v1）

- **用户消息**：悬停显示复制；发送失败（`failed`）时显示重试（重新 POST 该轮）。
- **助手消息**：完整回复结束后显示操作栏——点赞 / 点踩（互斥切换）、重新生成（丢掉该助手气泡并用上一条用户消息重跑 SSE）、复制。
- **停止生成**：流式进行中输入栏右侧为红色停止键（替换发送）；中止后该轮标记 `stopped` / `failedReason=stopped`，可重试。
- SSE `reasoning` 故意不渲染（与 v1 一致：不拆多气泡、不展示 thinking 流）。
- 流式进行中不显示助手操作栏。

### 历史展示隔离（对齐敲定协议 / spa v1）

- Gateway `/history` 按 Session ``kind`` **白名单**过滤：只返回 `chat` 气泡，以及 `schedule.display` 的 assistant；`schedule.silent`（含 heartbeat）不返回。
- `historyToChat` 再剥 `[SEND:]`/`[RECV:]`，并丢弃空行 / 泄漏的 `schedule.silent`（防御）。
- 气泡渲染同样 `stripTransferMarkers`（与 v1 一致）。

任务 `status` / `deliveryState` 仍是前端展示字段（Gateway 尚无 Task/Delivery 资源）。交付物分两轨：

| 字段 | 含义 |
|------|------|
| `deliverables` | **历史交付物**：当前 Session 累计全部产出（从 `/history` 的 `sends` 重水合，刷新后列表仍在） |
| `newDeliverables` | **新交付物**：本轮未确认的；宝箱金色 / 侧栏「新交付物」只看这个；「保存到成果库」后清空 |
| `deliverablePaths` | basename → `[SEND:]` 路径；刷新后抽屉经 `GET /workspace/file` 读盘预览 |

SSE `blob` 到达时同时写入 `deliverables` + `newDeliverables`。流式追加文本时必须保留 `message.files`。

History 在剥 `[SEND:]` 前抽出路径放进消息的 `sends`；纯 SEND、无正文的 assistant 行也会返回（`text: ""` + `sends`），前端气泡跳过空文本但仍累计交付物。

## 本地开发

需先有 Gateway 在跑。Vite 默认把 API 代理到 `http://127.0.0.1:8765`：

```bash
# 终端 1 — Gateway（端口以日志为准，若不是 8765 则设 GATEWAY_ORIGIN）
uv run psi-agent gateway --listen tcp:127.0.0.1:8765

# 终端 2
cd src/psi_agent/gateway/spa-v2
# PowerShell: $env:GATEWAY_ORIGIN="http://127.0.0.1:8765"
npm run dev
# → http://localhost:5174/spa-v2/
```

生产/联调：`npm run build` 后 Gateway 自动挂载 `spa-v2/dist` → `http://<gateway>/spa-v2/`。

安装包：PyInstaller / Nuitka CI 会构建并 `--add-data` / `--include-data-dir` 打入 `spa-v2/dist`；有该目录时安装版默认 `GET /` → v2。

## 目录

```text
src/
  App.tsx                 # 工作区门禁 → 工作台
  components/WorkspaceGate.tsx
  services/               # api / sse / chatStream / sessionBridge / bootstrapAi
  haitun-agent/           # 任务 UI（设计包）
  components/user-hub/    # 用户中心（自 v1：资料 / 大模型 / 登录 / 设置）
  styles/globals.css
```
