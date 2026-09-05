# SPA v2 — 任务驱动工作台（Gateway 联调版）

> 与 `../spa/`（对话气泡 v1）并行。UI 来自任务/宝箱设计包；**运行时已接 Gateway**：
> Task ≈ Session，对话走 `/sessions/{id}/chat` SSE，历史走 `/history`。
>
> **Gateway 默认控制台**：`GET /` → `/spa-v2/index.html`（`--browser` / webview 打开根地址即 v2）。v1 仍在 `/spa/`。

## 开发 session 交接

本文件是 SPA v2 相关开发在多个 session 之间交接的**第一文档**（与根 `AGENTS.md`、`agents/feishu/AGENTS.md` 一并阅读）。新 session 开工先读本文件；行为 / 协议 / UI 约定变更时先写回本文件再交接。聊天记录不替代文档。

**并行开发**：改本目录时建议单独一棵 `git worktree` + 独立功能分支；勿与 workspace/后端施工共挂同一分支。约定见仓库根 `WORKTREE.md` 与 `AGENTS.md`（「本地并行开发」）。

### 交互表面（赶工临时）

**刻意为之 / 赶工临时**：`SHOW_OVERVIEW_AND_TEMPLATES = false`（`haitun-agent/uiSurface.ts`）时：

- 侧栏隐藏「任务总览」「任务模板」入口；全局搜索不再搜模板
- 卡片栈**不含** overview 伪卡，只滑真实 Session 任务；无任务时主区空态 + CTA「新建任务/聊天」
- 新建页隐藏「从任务模板开始」；返回文案为「返回任务」
- OverviewCard / TemplateLibrary / `INITIAL_TEMPLATES` **代码与数据保留**，改回 `true` 即恢复旧交互

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
| 工作区切换 | 侧栏打开 PathPicker | 设置「切换工作区」→ 全屏选择页；**浏览**走 `/workspace/places` + `/browse`（对齐 v1）；偏好 `gw-v2-workspace` |
| 顶栏新建 | — | 右上角「新建任务/聊天」+ 侧栏同入口（**刻意为之**：不绑 `⌘/Ctrl N`，与 Edge「打开新窗口」冲突；侧栏按钮亦不展示该快捷键）；**分屏聚焦**时对话栏「收起」旁也有同款入口（左栏收起后**仅**保留展开上下文钮，不再并排再建入口） |
| Agent 包 | 与 workspace 合一 | ``GET /defaults``.agent → 新建任务/聊天 ``POST /sessions`` 带 `agent`（可与用户工作区不同）。设置「切换 Agent 包」与工作区同区；全屏 `WorkspaceGate kind=agent`；偏好 `gw-v2-agent`（覆盖 defaults）。**刻意为之**：只影响**新建** Session；已有任务仍用创建时绑定的 `agent` |
| 任务模板库 | — | 卡片正文/分类/交付物/页脚等字号 ≥12–14px（勿回退 8–10px 设计稿字号）。「新建模板」抽屉经 `createPortal` 挂 `document.body`：全屏遮罩 + 右侧贴边抽屉（勿嵌在 `.main-stage` 内导致四边露白） |

设置弹窗保留**切换工作区**与**切换 Agent 包**（真实功能）；设置 / 高级设置是**同一弹窗的两个页面**（点「高级设置」换页、可返回设置；`Esc` 先回主页再关闭），不要叠第二个 `HubDialog`。通知/交付位置等占位项已去掉，避免空壳菜单。
| 任务删除 | 侧栏 trash → DELETE session + 清本地 hist | 侧栏/卡片删除 → ``DELETE /sessions/{id}``（顺带清 JSONL + 标题）+ 清本地状态 |
| 任务置顶 | 侧栏 pin → `gw-pinned-session-ids` | 侧栏历史任务行 pin 钮（`TaskRow`）→ `gw-v2-pinned-task-ids`；**只排侧栏列表**（置顶先、再原序），**不改**卡片栈顺序；bootReady 后再 prune 失效 id（冷启动 `tasks=[]` 时不写盘） |
| 消息操作栏 | 助手：赞/踩/复制/重新生成；用户：复制 + 失败重试 | 同左（`FocusChatThread`）；feedback 仅内存态，刷新历史后不保留 |
| 停止生成 | 输入栏 Send ↔ Stop 切换 | 同左：流式时发送键变为停止（`abortRef.abort()`）；停止后草稿回填输入框 |
| 任务翻页 | — | 总览：卡片栈两侧 `card-arrow`。**分屏/聚焦**：左右箭头贴在**对话面板**（`context-chat`）左右缘——随对话区走（上下文栏开则在右栏内；侧栏+上下文都收起则贴主区左右）。顶栏另有 `NN / MM` 翻页器。键盘 `←`/`→`（输入框内 `Alt+←/→`）。窄屏隐藏侧箭头，保留顶栏翻页器 |

## 映射

```text
任务卡          ↔  Gateway Session（同 workspace；可选独立 agent 包）
新建任务/聊天   ↔  POST /sessions（可带 agent）+ POST /titles（首条文案的 `titleFromPrompt`，与乐观 UI 一致）+ 首条 chat SSE（文案与附件同总览对话框：`File[]` multipart）；**首条发送后立刻进入分屏聚焦**（左上下文 / 右对话），不再停在新建页本地气泡
卡片内对话      ↔  POST /sessions/{id}/chat（multipart chunks）
任务台标题      ↔  **刻意为之**：对标 DeepSeek——取聊天里**首条** user 文案（`titleFromHistoryMessages`）。`ensureHistory` / `refreshHistory` / 回合成功后同步 `POST /titles`；**无 user 的空 chat 默认不改标题**（避免首条落盘前抢读把乐观标题盖成「新任务」）。**Stop 撤回**传 `emptyMeansDefault` 才回落「新任务」。**不再**用 `POST /titles/generate` 另开 LLM 起标题。
任务历史文案    ↔  GET /sessions/{id}/history（AppData `histories/` 优先 + legacy 双读）
任务卡中间步 N/M ↔  GET /sessions/{id}/todos（``todo`` tool → AppData `todos/{id}.json`，legacy `.psi/todos` 双读）
分屏「任务历史」 ↔  GET /sessions/{id}/todo-segments（`todos/{id}.segments.json`；点选回放该段步骤）
路径默认        ↔  GET /defaults（agent + workspace + appdata）；workspace 软默认 `{Desktop}/haitun交付`（宣布路径；目录随首个 Session 创建）；UI 主要用 agent/workspace；localStorage `gw-v2-workspace` / `gw-v2-agent` 可覆盖 defaults（路径须仍是目录）；appdata 为记忆区根（todos/history/Gateway state 已迁 AppData，前端仍走 REST，不直读盘）；打开即用 AI 仍走空池惰性 POST `/ais`
```

**新建任务/聊天输入**：单个大框（对齐总览 `context-chat`）——框内上部是预设快捷按钮（单行），底部是细条真输入（回形针 + 文本框 + 发送）；附件 chip 在细条上方。发送时随首轮 `streamSessionChat` 上传；可纯附件无文案。页内「返回任务总览」始终回总览（`goHome`）；顶栏在从模板进入时可显示「返回模板库」（`newTaskReturnView`）。
**模型选择（防踩坑） / 启动渲染管线（刷新稳定）**：

```text
GET /spa-v2/     → 302 → index.html（redirect 须先于 add_static，否则 403）
App              → GET /defaults → 选定 workspace / agent（localStorage 覆盖 / defaults）
Workbench boot   → GET /sessions + /titles + /summaries
                 → hydrateAiForSessions()（只读现有模型池，不复活/不删除）
                 → setTasks（**从不**因空 AI 池跳过 sessions）
                 → 仅池仍空时 openModelsOnce
Hub「使用免费模型」→ **保留**已连接真实模型；hydrateAiForSessions() 读取现有池 → 无免费条目时 `createAi(DEFAULT_REMOTE_AI)` 并强制选中免费模型
发消息           → ensureSessionAi（优先任务绑定的模型；已被删除则用当前模型配置重绑旧 id，通道继续可用）
```

不盲选 `ais[0]`。**不自动删除任何已连接模型**——只有「已连接」行的删除按钮会删除，且一次删除该配置（`provider+model+api_key+base_url`）的**全部实例**（同一模型被多个 Session 绑定的重复条目会一起删掉）；删除当前模型后回落到剩余模型，新连接/切免费都不影响其它模型，新连接的模型立即成为当前模型。优先 localStorage 选中 AI（含用户主动选的免费条目），免费条目与真实 key 可以同时保留在池中。Gateway **不**级联删 Session——AI 删除后 Session 仍挂旧 `ai_id`；该任务下一次对话用**当前选中模型**，并把旧 `ai_id` **重绑到当前模型配置**（池全空时才回落免费默认），Session 通道保持可用，刷新后任务卡与可聊性不变。模型池「已连接」按同配置 **折叠展示**（仅 id 不同只显示一行；key 不同则分列）；无显式 id 的 `POST /ais` 同配置复用已有实例。**展示层**（`labelAisForDisplay`）：副标题区分「免费」与「自有 Key ···末四位」；同名标题再加 `(1)/(2)`；**重命名**独立存 `gw` 无关的 `spa-v2-ai-aliases`（按 `aiConfigKey`，id 重绑不丢）。workspace 过滤用 `sessionMatchesWorkspace`（空 workspace 视为本工作区）。

### 任务卡三步进度（分层）

上层只判定生命周期阶段，下层再填推进细节：

| 层 | 职责 |
|----|------|
| **阶段** `phase` | `advance` 推进 → `deliver` 产出与确认 → `done` 本轮完成（`taskProgress.resolveTaskProgress`） |
| **推进细节** | **有 Session `todo`** → 真实 checklist 步骤 + 角标 `N/M`；**无 todo** → **单行活动态**（待继续 / 正在处理 / 正在整理交付 / 本轮已完成），不定进度脉冲，**不**画假三步轨、**不**用启发式 % |
| **投影** | `applyTaskProgress` 唯一写入口，生成 `steps` / `progress` / `progressIndeterminate` / `progressLabel` / `hasTodoTrack` / `updated` |

**刻意为之（todo 策略不在前端）**：何时建 `todo` 由 agent 包 `skills/task-planning/SKILL.md` 判定；前端**只读** `GET …/todos`。侧栏语义：**有清单报步数，没清单报忙闲**。

**任务卡布局（首页 / 左栏）**：角标圆环已去掉；右上角放**宝箱**；底部为**直线进度条**（有 todo → `N/M` 填充；无 todo 忙时 indeterminate 扫条）。中间步骤区固定 **3×2** 视口高度，超出用小翻页（每页 6 项），视口内仍可纵向滚动；不挤占下方进度条。

**导航（刻意）**：
- **侧栏 / 搜索选任务** → 直接进入分屏聚焦（`chatExpanded`），不再停在中间卡片面。**刻意为之（手感）**：不做卡片左右滑动进出场（双层 ~470ms 卡顿）；若当前在卡片面，先切到目标卡再跑**与点对话栏相同的展开 CSS**；若已在分屏内换任务，仅轻量淡入。启动后预取最近若干条 `/history`，悬停侧栏行再预取。
- **任务总览左右划** → 仍是卡片面；点/轻触卡片主体（除宝箱 / 删除 / 步骤翻页 / **底部三格信号钮**）= 与点对话栏相同，进入分屏。**刻意为之**：滑动层 `setPointerCapture` 会吞掉子元素 `click`，因此在 `pointerup` 且未越过滑动阈值时打开分屏（不单靠 `onClick`）。
- **总览三格信号（运行中 / 待您处理 / 新交付物）** → 可点，走 `openSignal(kind)`（`taskSignals.ts`）展开侧栏对应筛选列表。侧栏顶栏仍只有「待您处理 / 新交付物」两钮（与原先一致）；「运行中」仅卡片入口。**待您处理** 目前只认 `status===attention`（联调几乎恒空，接口预留）。`.overview-metrics` **无框内顶部 padding**（外框顶边与竖分隔线齐平），整块带 `data-card-interactive`，避免空白区点穿进卡片 → 对话。
- **分屏「收起」旁** → 「新建任务/聊天」（顶栏新建在聚焦态仍隐藏，由此处补入口；样式与顶栏/侧栏蓝色主按钮一致）。左栏收起后对话栏左上角**只保留展开上下文钮**，不再并排「新建」按钮。
- **任务并行执行（刻意为之）**：每个任务（Session）有独立的流式状态——`AbortController` / SSE epoch / 过程轴按任务维护；「新建任务/聊天」并发送**不会**打断正在执行的任务，停止按钮只停止当前任务。后端每个 Session 是独立 channel，天然支持并发。

- 流式中：无 todo → `正在处理` + indeterminate；有交付物生成中可进 `deliver`（「正在整理交付」）。
- 有 todo 且全部 completed 仍在流式 → `deliver`（追加「产出与确认」）。
- 回合成功结束：`turnSettled=true` → `phase=done`（本轮**对话**已结算）。**有 todo 时步骤勾选 / `progressLabel` / 进度条 % 一律跟 AppData 清单**，不因结算而强行画满 `N/N` 或绿勾（Agent 未维护则如实 `1/N`）；清单已全部 completed 时才显示「本轮已完成 · N/N」。无 todo → 单行「本轮已完成」。任务历史标题：清单未完成用「本轮已回复 · N/M」。**软提示（A）**：回合成功后若仍有 `in_progress`，toast 提醒用户可让 Agent 勾选——**不改磁盘**（与 haitun `todo` 的自指 `warnings[]`（C）配套；不做自动 completed）。
- 空 todo 轮询**不会**把已 `done` 的卡打回推进中（保留 `turnSettled`）。
- **进度条 CSS**：`.task-linear-progress.done` 会强制 `width:100%`，仅在清单真完成（或无 todo 且 phase=done）时加该类。

### 对话气泡操作（对齐 spa v1）

- **用户消息**：悬停显示复制；发送失败（`failed`）时显示**红色回退箭头**（`RotateCcw`）。加载 `/history` 后经 `normalizeFailedTurns` 把「有 user、无完整 agent 回复」标成 `failed`/`incomplete`（与 spa v1 同款）。**点击箭头 ≠ 立刻重发**：效果对齐 Stop——撤回该 user（及空 agent stub），文案与附件**顶掉**输入框里半成品草稿并 focus，由用户再按发送。
- **助手消息**：完整回复结束后显示操作栏——点赞 / 点踩（互斥切换）、重新生成（丢掉该助手气泡并用上一条用户消息重跑 SSE）、复制。
- **停止生成**：流式进行中输入栏右侧为红色停止键（替换发送）。中止后撤回本轮乐观 user+agent，把原文案与附件还原到输入框（对齐 Cursor）——**不**标 `failed`、不留红箭头气泡。**刻意为之**：Stop 后不立刻 `refreshHistory`（会与 Session abandon 竞态，把尚未剥离的 user 灌回并被 `normalizeFailedTurns` 标成异常）；标题只按本地剩余气泡改。停止键用 `pointerdown` + 短时 `suppressSubmit`，避免 Stop 变回 Send 后同一次点击误触重发。另用 `streamEpoch` / `signal.aborted` 丢掉中止后的迟到 SSE。网络等非 Abort 失败仍标记 `failed` / 可重试。
- **粘贴 / 拖放附件**：对话栏 / 新建任务/聊天输入支持 `Ctrl/Cmd+V` 粘贴，以及从资源管理器或其他窗口拖入文件——均等价于回形针选文件，进入同一附件 chip 再走 multipart；纯文字粘贴不拦截。识图等由 workspace tool 处理。拖入时输入区高亮并提示「松开以添加附件」（`useComposerFileDrop` + `filesFromClipboard`）。
- **换行**：输入为 `textarea`；`Enter` 发送，`Ctrl/Cmd+Enter` 换行（`Shift+Enter` 亦换行）。
- **流式吸底（对齐 spa v1 / Cursor）**：`FocusChatThread` 距底 ≤60px 才跟随新内容滚底；手动上拉后不打断阅读；滚回底部恢复跟随。**发消息必跳底**：无论当前滚动位置，新增气泡切片里出现 `role=user` 即强制吸底并重新粘滞（`sendMessage` 一次追加 user+空 agent，不能只看 `messages.at(-1)`）。**直播思考框**（`.focus-chat-live-thinking`）同一规则：贴底才粘滞跟随思考增长；上拖断开、内容在下方继续生成；再拉回底恢复粘滞——禁止每 token 无条件 `scrollTop=scrollHeight`（会把框「粘死」）。
- SSE `reasoning`：**刻意压缩**仍走同一字段；用 `kind`（`thinking` / `tool_call` / `tool_result`）区分——**≠** `/history` 消息 provenance `kind`。过程轴见 `services/turnProgress.ts`（对标 Cursor）：
  - **封存行**：仅 `tool_call` 短句（如 `读取 \`a.py\``）；thinking / `tool_result` **不**封存（`tool_result` 尾行回「规划下一步…」，刻意不要「整理结果…」行）。
  - **尾行**：只活「规划下一步…」/「撰写回复…」；**刻意**永不把「规划下一步」推进 `lines`。
  - **步骤临时气泡（刻意为之）**：每轮 `tool_call` 前的自然语言**拼进**同一个临时气泡（`message.interimText`，虚线框），不因尾行回到「规划下一步…」而藏掉，也不把每段收进「已调用工具」。新一段 content 在临时气泡下方的普通气泡里继续长；再来 `tool_call` 时把该段并入临时气泡。回合结算**隐去**临时气泡，只留最后一段作 `message.text`（不进 tools/思考区）。`contentSegments` / `streamSegmentBodies` / `settleContentSegments`。`historyToChat` 合并连续 assistant 时同样只留末段。
  - **回合结束后过程拆分封装（对标 Cursor）**：流式期间过程轴 + 临时/最终气泡；回合结束把思考挂到 `message.reasoning`、把过程轴封存行挂到 `message.tools`（短句列表）。`FocusChatThread` **分开两块**（工具优先）：①「已调用 N 个工具」——读 `message.tools`（**默认展开**）；②「已思考」——`stripToolMarkersFromReasoning(reasoning)` 散文（**默认收起**）。`/history` 透出 JSONL `reasoning`（仅思考），并把各轮结构化 ``tool_calls`` 投影为独立字段 ``tools: [{name, arguments}]``（**刻意为之**：不塞进 reasoning；Session 的 `[Tool Call:]` 只走 SSE）。`historyToChat` 用 `summarizeToolCall` 生成短句并在合并连续 assistant 时拼接。刷新同任务即可还原工具列表 + 思考。
  - **`preferResultBelowRule`（刻意为之）**：仅展示层——短计划在 `---` 之上时偏好渲染下半段结果；**不改** JSONL / 复制源可选策略以实现为准。
  - **任务摘要 `summary`（刻意为之）**：不再截取助手末条回复。回合成功后（及历史缺摘要时）`POST /summaries/generate` 另开一轮模型写 1～2 句；Gateway `SummaryManager` 持久化到 AppData state（与 titles 同级）。左栏标题为「任务摘要」；任务卡正文同字段。展示侧仍 `plainTextFromMarkdown` 兜底。对话气泡仍走完整 Markdown。段标题（P1）可复用该 summary 写入 open todo-segment。

- 流式进行中不显示助手操作栏。

### 左栏：摘要 vs 历史

| UI | 数据 |
|----|------|
| 「任务摘要」 | `task.summary` ← LLM `/summaries/generate`（持久化） |
| 任务卡正文 | 同上 |
| 「执行步骤」 | live：`GET …/todos`；点历史段：该段快照（只读） |
| 「任务历史」 | `GET …/todo-segments`（``merge=false`` 开新子任务段；``merge=true`` 只更新当前段）。可点击切上方 checklist；当前 open 段点选等价 ``live``。P1：回合 summary 可 `POST …/todo-segments/{id}` 覆盖段标题。**不是**聊天 `/history` 时间线 |

**刻意为之**：无 Agent 写 `todo` 则历史为空；不以每条 user 消息切段。新一轮 SSE 自动回到 live 清单。

### 历史展示隔离（对齐敲定协议 / spa v1）

- Gateway `/history` 按 Session ``kind`` **白名单**过滤：只返回 `chat` 气泡，以及 `schedule.display` 的 assistant；`schedule.silent`（含 heartbeat）不返回。
- `historyToChat` 再剥 `[SEND:]`/`[RECV:]`，并丢弃空行 / 泄漏的 `schedule.silent`（防御）。
- **`historyToChat` 合并连续 assistant（刻意为之）**：Session 每轮 `tool_calls` 会把带正文的 assistant 落盘。刷新合并时**只保留最后一段**正文（与当场 `settleContentSegments` 一致），前面步骤叙述丢弃；files/`sends` stub 按 basename 去重合并。合并只发生在相邻 assistant 之间，遇 `user` 切断。
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
# 勿传 --appdata / PSI_APPDATA：与安装包一致，软默认 platformdirs（%LocalAppData%\Haitun）
uv run psi-agent gateway --gateway desktop --listen tcp:127.0.0.1:8765

# 终端 2
cd src/psi_agent/gateway/spa-v2
# PowerShell: $env:GATEWAY_ORIGIN="http://127.0.0.1:8765"
npm run dev
# → http://localhost:5174/spa-v2/
```

**刻意为之**：本地联调不要再用仓库内 `.appdata-spa-dev` 等沙盒记忆区；安装包 / CLI / spa 开发共用同一 AppData 软默认，会话与「已思考」等状态才一致。

**改完验收 / Agent 约定（硬）**：经 Gateway（`http://127.0.0.1:8765/`）看页面时，**每次改完 spa-v2 源码必须立刻 `npm run build`**，再让对方硬刷；不要只改源码不 build，也不要等用户追问「build 了吗」。纯 Vite `:5174` 联调可靠 HMR，但本仓默认验收路径是 Gateway 挂 `dist/`。

生产/联调：`npm run build` 后 Gateway 自动挂载 `spa-v2/dist` → `http://<gateway>/spa-v2/`。

安装包：PyInstaller / Nuitka CI 会构建并 `--add-data` / `--include-data-dir` 打入 `spa-v2/dist`；有该目录时安装版默认 `GET /` → v2。

单测：`npm test`（vitest）。DOM 行为测试在文件头写 `@vitest-environment jsdom`，纯逻辑测试留在默认 node 环境（快得多）。

## 登录（C 端账号）

界面 `components/user-hub/HubLoginPanel.tsx`（挂在 `UserHub` 头像菜单的「登录账号」），纯逻辑抽到 `services/authFlow.ts` 以便 vitest 直接测，验证码 6 格是 `HubOtpInput.tsx`。

**token 不进浏览器**：页面只 `fetch('/auth/*')` 打本机 Gateway，凭证由 Gateway 侧持有并加密落盘。两段式注册的 `tempToken` 同样扣在 Gateway 进程内 —— 页面脚本一旦持有凭证，XSS 即等于凭证泄露。所以 `verifyAuthCode` 的返回里没有 `tempToken`，`completeAuth` 也不需要传。

**错误码文案对着实际后端写**，不对着契约文档写：`AUTH_ERROR_TEXT` 的键取自云端 `core/errors.py` 的 `code` 常量（`code_invalid` / `rate_limited` / `unauthorized` / `forbidden` / `not_found` / `conflict` / `identity_taken` / `last_identity` / `invalid_input` / `provider_failure`）+ FastAPI 校验失败的 `invalid_request`。文档里出现过 `invalid_code` / `code_expired` / `invitation_*` 这些**服务端从未发出**的码；照文档写只会得到一份自洽的幻觉。未收录的码 `?? raw` 原样透出，宁可露英文码也不吞线索。

`/auth/*` 未配置时全部 404，`getAuthStatus()` 把它翻成 `available: false` 而非抛错 —— 界面据此显示「本地模式」说明。注意 `available` 是**前端合成**的（404 → false，其余 → true），服务端响应体里没有这个字段。

**新用户信号是 `registrationRequired`，不是 `tempToken`。** 凭证被 Gateway 扣下后，响应里补一个不含凭证的布尔标记，`needsComplete()` 判它。曾经判 `tempToken` —— 扣掉凭证后该判断恒为 false，新用户被当成登录失败弹回输入页，而 154 个测试全绿，因为假后端 `__fixtures__/fakeAuthBackend.ts` 还在回旧字段。**改了 Gateway 的响应形状，假后端必须同步**，否则测的是一份已不存在的契约。

**登录成功的落点是关窗回工作台**（原型 D4），不是账户面板：`finishAndClose()` 关窗 + toast，侧栏靠 `notifyAuthChanged()` 广播就地更新。账户面板（C1）只由「已登录后主动从侧栏点进来」到达。

**登录态跨组件共享走 `services/useAuthAccount.ts`**（事件广播，无 module 级可变全局）。侧栏账户区必须读它而非 localStorage 里的本地昵称，否则登录完外面还显示「用户」和「登录账号」。

**首屏是硬门禁**（2026-08-15 团队决定，此前是可跳过的软门禁）：`HaiTunAgentWorkspace` 的 `authGate` 在 boot 后探一次登录态，未登录则弹登录窗并**关不掉**，同时压住首屏引导与模型池自动弹窗。

| 关注点 | 做法 |
| --- | --- |
| 为什么必须硬 | C 端默认模型的 key 由云端按登录态下发，未登录时 AI 子进程拿到空 key，**any-llm 在本地就抛** `No openai API key provided ... set the OPENAI_API_KEY environment variable`（走不到云端，没有 401）。放人进来只是把拦截点从登录窗推迟到第一次对话，还换成一句与本产品无关的话 |
| 三个关闭通道全堵 | ✕ 与遮罩点击：`HubLoginPanel` 的 `mandatory` 透传成 `HubDialog` 的 `blocking`（遮罩退化为 `aria-hidden` 装饰层）。Esc：`UserHub` 的 keydown effect 里 `if (loginRequired) return`。**三处分散在两个组件，漏一个就漏一个绕过口** —— `HubLoginPanel.smoke.test.tsx` 的 `describe('硬门禁：不可跳过')` 逐条守 |
| 不能只用 `panel === 'login'` | `show={loginRequired \|\| panel === 'login'}`。否则用户点侧栏别的入口（模型池/设置）就把登录窗顶掉，门只拦得住第一下 |
| 登录成功才放行 | `onLoginGateDone` **重新探一次** `/auth/status`（`recheckAuthGate`），不是直接 `setAuthGate("passed")` |
| 两种刻意放行 | `available === false`（部署方显式关掉登录，没有门可守，拦下去只会得到一个点不动的表单）；探测抛错（连「是否需要登录」都不知道，且 Gateway 不通本身会由别处报错） |
| 断网时不放行 | D3 屏在 `mandatory` 下撤掉「暂不登录，继续使用」，只留「重试」，并把文案改成「登录后才能使用」—— 退不出去必须给出原因 |

**登录屏上没有任何协议文字。** 演变过三轮：必勾复选框 → 一行被动告知（`.hub-legal-note`）→ 整句去掉。**因为同意动作已前移到安装期** —— 安装向导第一页是必勾的协议页（`.github/inno-setup/haitun.iss`），装过软件的人必然已经同意过，登录窗再说一遍只是噪音。`agreed` / `shakeAgree` state、`onSend` 的前置检查、`LEGAL_TERMS` / `LEGAL_PRIVACY` 常量均已删净。`HubLoginPanel.smoke.test.tsx` 用 `queryByRole(...)).toBeNull()` 反向守着，防止有人「顺手加回来」。

协议正文仍在 `public/terms.html` / `privacy.html`（安装器读的就是这两个，见下），只是 SPA 目前不再链向它们。**若将来要在界面上重新放出协议入口，引用必须走 `import.meta.env.BASE_URL`** —— 本 SPA 挂在 `/spa-v2/` 下，写死绝对路径会打到站点根目录 404（海豚图标曾这么碎过，`DOLPHIN` 常量就是为此）。

**这两个 HTML 是生成物，不要手改。** 源是 `legal/Haitun_软件许可及服务协议_1.0.md` 与 `legal/Haitun_隐私保护政策_1.0.md`，由 `scripts/gen_legal_html.py` 生成；`legal.css` 是手写的，两页共用。安装器的协议页以 `dontcopy` 引同一路径（`.github/inno-setup/haitun.iss`），**安装期与产品内共用一份产物** —— 各存一份必有一份过时。改了 md 要重新生成，CI 有 `--check` 步骤守着。加粗（`**`）写在 md 源里而非生成器里：加粗属法律判断，得留在人能审的 diff 里。

## 目录

```text
src/
  App.tsx                 # 工作区门禁 → 工作台
  components/WorkspaceGate.tsx
  services/               # api / sse / chatStream / sessionBridge / bootstrapAi / turnProgress / reasoningDisplay / clipboardFiles / composerFileDrop / authFlow / useAuthAccount
  haitun-agent/           # 任务 UI（设计包）；focus-chat-thread 含「已思考」展开
  components/user-hub/    # 用户中心（自 v1：资料 / 大模型 / 登录 / 设置）
  styles/globals.css
public/                   # 不打包, 由站点根提供; 引用一律走 import.meta.env.BASE_URL
  haitun-dolphin.png
  terms.html              # 用户服务协议（工程稿, 发布前需法务复核）
  privacy.html            # 隐私政策（同上）
  legal.css               # 两份协议共用样式
```
