# SPA v2 — 任务驱动工作台（Gateway 联调版）

> 与 `../spa/`（对话气泡 v1）并行。UI 来自任务/宝箱设计包；**运行时已接 Gateway**：
> Task ≈ Session，对话走 `/sessions/{id}/chat` SSE，历史走 `/history`。
>
> **Gateway 默认控制台**：`GET /` → `/spa-v2/index.html`（`--browser` / webview 打开根地址即 v2）。v1 仍在 `/spa/`。

## 开发 session 交接

本文件是 SPA v2 相关开发在多个 session 之间交接的**第一文档**（与根 `AGENTS.md`、`examples/haitun-workspace/AGENTS.md` 一并阅读）。新 session 开工先读本文件；行为 / 协议 / UI 约定变更时先写回本文件再交接。聊天记录不替代文档。

**并行开发**：改本目录时建议单独一棵 `git worktree` + 独立功能分支；勿与 workspace/后端施工共挂同一分支。约定见仓库根 `WORKTREE.md` 与 `AGENTS.md`（「本地并行开发」）。

## 少用全局变量

遵循根 `AGENTS.md` 第 15 条：React/模块代码中避免模块级可变全局（如裸 `let`/`Map` 跨组件共享、挂到 `window` 的状态）。状态放进组件 props、context、或明确归属的模块 API；**仅赶工临时**可破例，并标注「赶工临时」。

## 与 spa v1

| | spa (v1) | spa-v2 |
|--|----------|--------|
| 产品隐喻 | Session 对话气泡 | 任务卡片 + 交付物宝箱 |
| 技术栈 | Vue 3 + Pinia | React 19 + Vite |
| base | `/spa/` | `/spa-v2/` |
| 对话 | Gateway SSE | 同左（同一套 API） |
| 交付物 | 气泡 blob chip | 宝箱 UI；SSE `blob` 写入 `deliverables`；抽屉内按 blob 真实渲染（对齐 spa v1：MD/HTML/图片音视频/代码/CSV/PDF/DOCX/XLSX/PPTX，重库动态 `import()`；无 blob 时明确空态）。MD 预览与聊天气泡共用 `renderMd` + `.md-table-card`。**刻意为之**：`renderMd` 超链接 `target=_blank`；附件 chip / 预览抽屉仍本页。DOCX：`ignoreWidth` 去掉页宽；**页边距仍是绝对长度**，预览 CSS 强制 `section.docx` 宽 100% + 适中 padding，避免窄抽屉里正文挤成细条；表格/图片 `max-width:100%` 防横向溢出。有 `[SEND:]` path 时，气泡 chip / 宝箱 / 预览抽屉可「在文件夹中显示」（`POST /workspace/reveal`） |
| 账户区 | 头像菜单合一 | 头像菜单仅资料/登录；**模型池**与**设置**为侧栏独立快捷入口 |
| 默认工作区 | 无 / 必须先选 | 启动读 ``GET /defaults``.workspace（Gateway 软默认 `{Desktop}/haitun交付`，**只宣布不建目录**；首个 Session/对话时服务端再 mkdir）；遗留 `*-workspace` / 字面量 `workspace` / `haitun-workspace` 会忽略 |
| 工作区切换 | 侧栏打开 PathPicker | 设置「切换工作区」→ 选择页；**浏览**按钮走 `/workspace/places` + `/browse`（对齐 v1） |
| 顶栏新建 | — | 右上角「新建任务」+ 侧栏同入口（`⌘/Ctrl N`） |
| Agent 包 | 与 workspace 合一 | ``GET /defaults``.agent → 新建任务 ``POST /sessions`` 带 `agent`（可与用户工作区不同） |

设置弹窗暂时只保留**切换工作区**（真实功能）；通知/交付位置等占位项已去掉，避免空壳菜单。
| 任务删除 | 侧栏 trash → DELETE session + 清本地 hist | 侧栏/卡片删除 → ``DELETE /sessions/{id}``（顺带清 JSONL + 标题）+ 清本地状态 |
| 消息操作栏 | 助手：赞/踩/复制/重新生成；用户：复制 + 失败重试 | 同左（`FocusChatThread`）；feedback 仅内存态，刷新历史后不保留 |
| 停止生成 | 输入栏 Send ↔ Stop 切换 | 同左：流式时发送键变为停止（`abortRef.abort()`）；停止后草稿回填输入框 |

## 映射

```text
任务卡          ↔  Gateway Session（同 workspace；可选独立 agent 包）
新建任务        ↔  POST /sessions（可带 agent）+ POST /titles + 首条 chat SSE（文案与附件同总览对话框：`File[]` multipart）；**首条发送后立刻进入分屏聚焦**（左上下文 / 右对话），不再停在新建页本地气泡
卡片内对话      ↔  POST /sessions/{id}/chat（multipart chunks）
任务历史文案    ↔  GET /sessions/{id}/history（AppData `histories/` 优先 + legacy 双读）
任务卡中间步 N/M ↔  GET /sessions/{id}/todos（``todo`` tool → AppData `todos/{id}.json`，legacy `.psi/todos` 双读）
路径默认        ↔  GET /defaults（agent + workspace + appdata）；workspace 软默认 `{Desktop}/haitun交付`（宣布路径；目录随首个 Session 创建）；UI 主要用 agent/workspace；appdata 为记忆区根（todos/history/Gateway state 已迁 AppData，前端仍走 REST，不直读盘）；打开即用 AI 仍走空池惰性 POST `/ais`
```

**新建任务输入**：单个大框（对齐总览 `context-chat`）——框内上部是预设快捷按钮（单行），底部是细条真输入（回形针 + 文本框 + 发送）；附件 chip 在细条上方。发送时随首轮 `streamSessionChat` 上传；可纯附件无文案。页内「返回任务总览」始终回总览（`goHome`）；顶栏在从模板进入时可显示「返回模板库」（`newTaskReturnView`）。
**模型选择（防踩坑）**：启动 / 新建任务不盲选 `ais[0]`。池里若已有真实 key，会清掉残留的 `haitun-default` 占位项，并优先用户选中（localStorage）的 AI；仅空池才走免费远程。Session 创建时固定 `ai_id`——已绑坏 AI 的旧任务需新建。

### 任务卡三步进度（分层）

上层只判定生命周期阶段，下层再填推进细节：

| 层 | 职责 |
|----|------|
| **阶段** `phase` | `advance` 推进 → `deliver` 产出与确认 → `done` 本轮完成（`taskProgress.resolveTaskProgress`） |
| **推进细节** | **有 Session `todo`** → 真实 checklist 步骤 + 角标 `N/M`；**无 todo** → **单行活动态**（待继续 / 正在处理 / 正在整理交付 / 本轮已完成），不定进度脉冲，**不**画假三步轨、**不**用启发式 % |
| **投影** | `applyTaskProgress` 唯一写入口，生成 `steps` / `progress` / `progressIndeterminate` / `progressLabel` / `hasTodoTrack` / `updated` |

**刻意为之（todo 策略不在前端）**：何时建 `todo` 由 agent 包 `skills/task-planning/SKILL.md` 判定；前端**只读** `GET …/todos`。侧栏语义：**有清单报步数，没清单报忙闲**。

**任务卡布局（首页 / 左栏）**：角标圆环已去掉；右上角放**宝箱**；底部为**直线进度条**（有 todo → `N/M` 填充；无 todo 忙时 indeterminate 扫条）。中间步骤区固定 **3×2** 视口高度，超出用小翻页（每页 6 项），视口内仍可纵向滚动；不挤占下方进度条。

- 流式中：无 todo → `正在处理` + indeterminate；有交付物生成中可进 `deliver`（「正在整理交付」）。
- 有 todo 且全部 completed 仍在流式 → `deliver`（追加「产出与确认」）。
- 回合成功结束：`turnSettled=true` → `done`；有 todo 则步骤全勾，无 todo 则单行「本轮已完成」。
- 空 todo 轮询**不会**把已 `done` 的卡打回推进中（保留 `turnSettled`）。

### 对话气泡操作（对齐 spa v1）

- **用户消息**：悬停显示复制；发送失败（`failed`）时显示重试（重新 POST 该轮）。
- **助手消息**：完整回复结束后显示操作栏——点赞 / 点踩（互斥切换）、重新生成（丢掉该助手气泡并用上一条用户消息重跑 SSE）、复制。
- **停止生成**：流式进行中输入栏右侧为红色停止键（替换发送）。中止后撤回本轮乐观 user+agent，把原文案与附件还原到输入框（对齐 Cursor）。**刻意为之**：停止键用 `pointerdown` + 短时 `suppressSubmit`，避免 Stop 变回 Send 后同一次点击误触重发（旧逻辑清空输入框，误触 submit 是空操作所以「一点就停」；回填草稿后误触会立刻再跑一轮，看起来像打断后又在气泡里重出）。另用 `streamEpoch` / `signal.aborted` 丢掉中止后的迟到 SSE。网络等非 Abort 失败仍标记 `failed` / 可重试。
- **粘贴附件**：对话栏 / 新建任务输入 `Ctrl/Cmd+V` 时，剪贴板中的**任意文件**（含截图）等价于回形针选文件，进入同一附件 chip 再走 multipart；纯文字粘贴不拦截。识图等由 workspace tool 处理。
- **换行**：输入为 `textarea`；`Enter` 发送，`Ctrl/Cmd+Enter` 换行（`Shift+Enter` 亦换行）。
- SSE `reasoning`：**刻意压缩**仍走同一字段；用 `kind`（`thinking` / `tool_call` / `tool_result`）区分——**≠** `/history` 消息 provenance `kind`。过程轴见 `services/turnProgress.ts`（对标 Cursor）：
  - **封存行**：仅 `tool_call` 短句（如 `读取 \`a.py\``）；thinking / `tool_result` **不**封存（`tool_result` 尾行回「规划下一步…」，刻意不要「整理结果…」行）。
  - **尾行**：只活「规划下一步…」/「撰写回复…」；**刻意**永不把「规划下一步」推进 `lines`。
  - **`hideAgentProse`（刻意为之，对标 Cursor）**：仅在过程轴仍为「规划下一步…」（工具 / thinking）时藏正文，避免半截计划与过程轴抢戏；一旦 SSE `content` 到达、尾行切到「撰写回复…」，**正文必须边到边显示**（过程轴仍可挂在上方）。回合结束再收起过程轴。
  - **`preferResultBelowRule`（刻意为之）**：仅展示层——短计划在 `---` 之上时偏好渲染下半段结果；**不改** JSONL / 复制源可选策略以实现为准。

- 流式进行中不显示助手操作栏。

### 历史展示隔离（对齐敲定协议 / spa v1）

- Gateway `/history` 按 Session ``kind`` **白名单**过滤：只返回 `chat` 气泡，以及 `schedule.display` 的 assistant；`schedule.silent`（含 heartbeat）不返回。
- `historyToChat` 再剥 `[SEND:]`/`[RECV:]`，并丢弃空行 / 泄漏的 `schedule.silent`（防御）。
- **`historyToChat` 合并连续 assistant（刻意为之）**：Session 每轮 `tool_calls` 会把带正文的 assistant 落盘（todo 多步常见「Step N ✅ …」或短计划各占一行）。流式时 `appendStreamingAgent` 累进同一气泡；刷新若不合并会拆成多个气泡并各挂操作栏。合并只发生在相邻 assistant 之间，遇 `user` 切断；files/`sends` stub 按 basename 去重合并。
- 气泡渲染同样 `stripTransferMarkers`（与 v1 一致）。

任务 `status` / `deliveryState` 仍是前端展示字段（Gateway 尚无 Task/Delivery 资源）。交付物分两轨：

| 字段 | 含义 |
|------|------|
| `deliverables` | **历史交付物**：当前 Session 累计全部产出（从 `/history` 的 `sends` 重水合，刷新后列表仍在） |
| `newDeliverables` | **新交付物**：本轮未确认的；宝箱金色 / 侧栏「新交付物」只看这个；「保存到成果库」后清空 |
| `deliverablePaths` | basename → `[SEND:]` 路径；刷新后抽屉/气泡经 `GET /workspace/file` 懒加载预览（**刻意**不传 `root`，避免绝对 SEND 路径被 workspace 门禁 403）；「在文件夹中显示」走 `POST /workspace/reveal`（有 path 才可点） |

SSE `blob` 到达时同时写入 `deliverables` + `newDeliverables`（有 `path` 则写入 `deliverablePaths`）。流式追加文本时必须保留 `message.files`。

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

**改完验收**：用户经 Gateway 看页面时，前端改动后先 `npm run build` 再让对方刷新（硬刷）；不要只改源码不 build。
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
