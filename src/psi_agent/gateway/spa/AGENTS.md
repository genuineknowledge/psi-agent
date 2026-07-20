# SPA 前端设计文档

## 概述

Web 控制台是一个本地打包、无外部 CDN 依赖的 Vue 3 单页应用（SPA），由 Vite 构建为静态文件，通过 Gateway 的 `/spa/` 路由服务。支持多格式文件预览（代码/PDF/Word/PPT/Excel/CSV），依赖已显著增加，不再是轻量前端。

## 前端设计约束

以下是开发本 SPA 时必须遵守的约定，与根 `AGENTS.md` 的「设计理念」同级——凡新增/改动组件、样式、状态，先对照本节：

1. **本地打包 / 无外链**：所有依赖经 npm 安装、由 Vite 打进 `dist/`。禁止在 `index.html` 或组件里引 `<script src="https://...">` / `<link href="https://...">` 外链——离线与二进制打包环境下外链会失效。字体、图标、CSS 一律走本地包（如 `material-symbols`）。

2. **重依赖必须懒加载**：文件预览类大库（codemirror / pdfjs-dist / docx-preview / pptx-preview / xlsx / papaparse）一律用动态 `import()` 按需加载，禁止顶层静态 import 拖进主 bundle。新增同类大库照此办理，并在「技术栈」的文件预览依赖表补一行「用途」。

3. **技术栈封闭**：默认不再引入 **Vue Router**（单页无路由）、**TypeScript**、**CSS 预处理器**；已采用 **Pinia** 做领域 store 拆分（见下条）。确需新增第三方库时，先评估体积与是否可 tree-shake，并在「技术栈」表补一行「选择理由」。

4. **按领域拆分 Pinia store**：全局/跨组件状态按领域拆进 `src/stores/` 下的 4 个 setup-store（`ai.js`/`session.js`/`chat.js`/`ui.js`），组件内经 `storeToRefs` 取 state（保响应式），action 直接从 store 实例调用；不再用 `provide/inject`。组件内裸 `ref` 只用于纯本地 UI 状态（如某个 input 的临时值）。跨组件副作用用 store 字段信号传递（如 `chat.uploadResetToken` 递增通知 InputBar 清空 file input），不要组件间直接互相调方法。新增状态先判断归属哪个领域 store，避免混进无关 store。

5. **服务端是唯一数据源**：AI / Session 列表、标题从 Gateway REST GET 获取，`localStorage` 只存 UI 偏好与对话缓存（见「localhost 持久化策略」）。新增持久化项必须用 `gw-` 前缀 key，并同步更新持久化表。

6. **纯逻辑抽到无副作用模块**：会话过滤/置顶/排序等纯计算放 `sessionList.js`、快捷键判定放 `shortcuts.js` 这类无状态工具模块（纯函数、可单测、不碰 DOM 与全局 store），组件只负责调用与渲染。此类模块**必须配套 `*.test.js`（Vitest）**：判定/过滤/排序等纯逻辑先写单测再实现，视觉/DOM 行为不强求单测。

7. **App.vue 只做编排**：`App.vue` 负责跨组件事件、弹窗控制、drag-drop、主题/侧栏切换与启动流程，**不写具体业务逻辑**。会话逻辑放 `Sidebar.vue` / `useSession.js`，输入发送逻辑放 `InputBar.vue` / `useChat.js`。新业务优先落到对应组件或 composable。

8. **composable 不碰 DOM**：`composables/` 里是纯逻辑（`useChat` 等），不直接操作 DOM。滚动委托 `useScroll`，需要 DOM 的行为通过 store 信号或回调交给组件层。DOM 操作前若依赖渲染结果，必须 `await nextTick()`。

9. **弹窗统一走 BaseDialog**：所有弹窗基于 `BaseDialog.vue`（overlay + dialog + actions 插槽），不各自手写遮罩/定位。新弹窗 = 新建一个基于 BaseDialog 的组件。

10. **样式分层不越界**：全局 MD3 token 在 `styles/tokens.css`，组件基类在 `styles/components.css`，app-shell 布局在 `styles/layout.css`；**组件专属样式（含其移动端 `@media`）一律写在该组件的 `<style scoped>`**。颜色/圆角/阴影/间距用 `--md-*` CSS variable，禁止硬编码色值，保证双主题一致。

11. **优先用 VueUse 而非手写样板**：通用组合式逻辑一律优先用 `@vueuse/core`，不再手写。当前已用：`useClipboard`（复制）、`useEventListener`（事件监听，自动清理）、`useBreakpoints`（`{ mobile: 768 }` 移动端断点）、`useStorage`（`gw-` 前缀持久化）、`useDropZone`（拖拽上传）、`useColorMode`（主题）。新增同类需求先找 VueUse 有无对应函数；用库默认值会改变既有行为时（如 `useColorMode` 的 `writeDefaults`/`disableTransition`）必须显式配置对齐，不得引入可感知回归。**故意不用 VueUse 的两类**：① 自定义协议——`useSSE.js`（POST + 自定义分帧的 SSE，`useEventSource` 仅支持 GET，替不了）；② 纯业务 composable——`useChat`/`useSession`/`useScroll`（聊天流程/会话编排/流式吸底逻辑，无通用等价物）。

11. **响应式与移动端**：断点统一用 `768px`。移动端键盘/视口适配集中在 `useKeyboard.js`，桌面端需清空其注入的内联样式，不要在别处再写第二套 viewport 逻辑。

12. **AI 回复渲染的边界**：`renderMd` 的输出来自可信 AI 后端、非用户输入，故未做 sanitize；若将来渲染用户可控内容，必须先引入 sanitize。用户输入一律经 `htmlEscape` 后显示。

## 技术栈

| 领域 | 技术 | 版本 | 选择理由 |
|------|------|------|----------|
| 框架 | Vue 3 Composition API `<script setup>` | `^3.4.21` | SFC 组织清晰 |
| 状态管理 | Pinia（setup-store） | `^3.0.4` | 按领域拆分 store，替代单一 `reactive()` 单例 |
| 组合式工具 | `@vueuse/core` | `^14.3.0` | 通用组合式函数（剪贴板/事件监听/断点/存储/拖拽/主题），替代手写样板 |
| 构建 | Vite | `^8.1.0` | 快 HMR + 静态打包 |
| 测试 | Vitest | `^3.2.7` | 纯逻辑/store 单测（`npm test`），复用 Vite 配置；`environment: 'node'`，`include: ['src/**/*.test.js']` |
| Markdown | marked | `^18.0.5` | AI 回复非用户输入无需安全 sanitize |
| LaTeX | KaTeX | `^0.17.0` | 同步 `renderToString()` 比 MathJax 快 5-10x，SSE 逐帧重渲染关键 |
| 图标 | Material Symbols | `^0.45.4` | Google MD3 官方当前图标集，CSS `font-variation-settings` 支持 weight/fill 可调 |
| Vue 插件 | `@vitejs/plugin-vue` | `^6.0.7` | Vite SFC 编译 |

**文件预览依赖**（按需动态 import，懒加载，不进主 bundle）：

| 库 | 版本 | 用途 |
|----|------|------|
| `codemirror` | `^6.0.2` | 代码文件语法高亮预览 |
| `highlight.js` | `^11.11.1` | AI 回复中 Markdown 代码块的语法高亮（`utils.js:renderMd`，用 `lib/common` 仅含常用语言） |
| `pdfjs-dist` | `^5.6.205` | PDF 预览 |
| `docx-preview` | `^0.3.7` | Word（.docx）预览 |
| `pptx-preview` | `^1.0.7` | PowerPoint（.pptx）预览 |
| `xlsx` | `^0.18.5` | Excel 表格预览 |
| `papaparse` | `^5.5.4` | CSV 解析预览 |

**未引入**：
- **Vue Router** — 单页无路由
- **TypeScript** — 未引入
- **CSS 预处理器** — 手写 CSS variables（MD3 token 系统）

## 打开即用（远程默认 AI）

空 AI 池时 SPA 经 `bootstrapAi.js` 直接 `POST /ais`（沿用既有创建接口），挂上远程默认模型。默认值集中在 SPA：`DEFAULT_REMOTE_AI`（根域名 `https://haitun.addchess.cn` / `deepseek-v4-flash-free`；占位 key `haitun-default`）。Gateway AI 把该 `base_url` 原样交给 any-llm（`api_base`），请求上游 `{base_url}/chat/completions`。真正的 upstream（当前为 OpenCode Zen）与 API key **只在公司域名所在 VM 的 Nginx 注入**，不进 SPA / 安装包。Gateway **不**提供 `/ais/bootstrap` 或内置默认 AI。

## 目录结构

```
spa/
├── package.json                     # 依赖声明 + dev/build/preview/test 脚本
├── vite.config.js                   # base=/spa/, @ alias, test（Vitest：node 环境 + src/**/*.test.js）
├── index.html                       # #app mount + interactive-widget=resizes-visual
├── src/
│   ├── main.js                      # createApp + app.use(createPinia()) + import CSS + v-focus directive
│   ├── App.vue                      # 根组件：编排层 — 跨组件 handler + 弹窗 + drag-drop；sidebar/input 业务逻辑已移入各组件
│   ├── stores/                      # Pinia 领域 store（4 个 setup-store）
│   │   ├── ai.js                    # useAiStore：ais, selectedAiId, aiForm, fetchedModels, loadingModels, modelPanelOpen
│   │   ├── session.js               # useSessionStore：sessions, selectedSessionId, draftSession, sessionTitles, sessionMessages, sessionInputs, sessionStreaming, sessionAbortControllers, …
│   │   ├── chat.js                  # useChatStore：messages, inputText, streaming, abortController, selectedFiles, userHasScrolledUp, uploadResetToken
│   │   └── ui.js                    # useUiStore：loadingEnv, isLightMode, isDragging, dlgAI, dlgSess, snackbar, dlgConfirm, isSidebarCollapsed, isMobileSidebarOpen, sessionSearchFocusToken, hubMenuOpen, hubPanel + action showAlert/toggleSidebar/closeMobileSidebar/focusSessionSearch/toggleHubMenu/openHubPanel/…
│   ├── sendMarkers.js               # stripTransferMarkers（剥掉气泡/历史展示里的 [SEND:]/[RECV:]，防绝对路径泄露）
│   ├── mdTable.js                   # GFM 表格复制/下载（MessageBubble + FilePreview MD 预览共用）
│   ├── assistantSegments.js         # 助手分段辅助（一轮 user → 至多一个主 assistant 气泡）
│   ├── utils.test.js                # renderMd GFM 表格规范化单测（Vitest）
│   ├── sendMarkers.test.js          # stripTransferMarkers 单测
│   ├── api.js                       # fetch 封装(api) + chat 流请求(streamChat)
│   ├── providers.js                 # 预置 provider 配置 (deepseek/openai/anthropic/gemini)，供 AiDialog 高级配置
│   ├── modelPresets.js              # Hub 快捷连接预设表（展示 + provider/model/base_url 默认），配套 modelPresets.test.js
│   ├── userProfile.js               # 用户资料 localStorage key + readAvatarDataUrl
│   ├── sessionList.js               # 会话列表纯逻辑工具：置顶(pin)持久化、搜索过滤、显示名、标题 payload 构造。导出：PINNED_SESSIONS_KEY、getSessionDisplayName、loadPinnedSessionIds、savePinnedSessionIds、togglePinnedSessionId、buildSessionTitlePayload、buildVisibleSessions
│   ├── shortcuts.js                 # 快捷键判定纯函数：matchSidebarShortcut(event) → 'new-session'|'focus-search'|null（用 event.code + Ctrl/Cmd+Shift）
│   ├── shortcuts.test.js            # matchSidebarShortcut 单测（Vitest）
│   ├── stores/ui.test.js            # ui store focusSessionSearch token 单测（Vitest）
│   ├── components/
│   │   ├── Sidebar.vue              # 会话列表：新建/双击改名/删除/置顶(pin)/搜索过滤（置顶与搜索逻辑在 sessionList.js）
│   │   ├── SessionStreamIndicator.vue # 侧栏流式 spinner / 后台完成未读蓝点
│   │   ├── ChatArea.vue             # 消息列表容器 + 自动滚动 + 空状态
│   │   ├── MessageBubble.vue        # 单条消息：Markdown + 附件 + 助手操作栏（赞踩/复制/重新生成）
│   │   ├── ThinkingBubble.vue       # 等待首 token 的三个脉冲圆点
│   │   ├── InputBar.vue             # 输入 UI：textarea 自适应高度 + 文件选择 + 发送按钮；发送逻辑委托给 useChat.js；内嵌 ModelPanel
│   │   ├── ModelPanel.vue           # 输入栏旁模型切换（只切换/删除已连接；链接走 User Hub）
│   │   ├── BaseDialog.vue           # 弹窗外壳(overlay + dialog + actions，插槽:title/默认/actions)
│   │   ├── NoWorkspaceView.vue      # 无工作区引导页（打开工作区 CTA）
│   │   ├── NoSessionView.vue        # 有工作区无会话引导页（新对话 CTA）
│   │   ├── UserHub.vue              # 右上角用户菜单（资料/大模型/登录/设置）
│   │   ├── HubModelsPanel.vue       # Hub「大模型」：预设卡片 + API Key；右上角「高级配置」→ AiDialog
│   │   ├── HubProfilePanel.vue      # Hub「我的资料」：称呼 + 头像上传
│   │   ├── HubLoginPanel.vue        # Hub「登录」占位（本地模式说明）
│   │   ├── HubSettingsPanel.vue     # Hub「设置」：主题切换等
│   │   ├── AiDialog.vue             # 链接大模型弹窗（基于 BaseDialog，高级四项配置）
│   │   ├── SessDialog.vue           # （已废弃）原创建会话确认弹窗；新对话改为 draft + 首条发送 POST
│   │   ├── PathPickerDialog.vue     # 通用路径选择器（工作区/导出复用）
│   │   ├── ConfirmDialog.vue        # 通用确认弹窗（基于 BaseDialog）
│   │   ├── FilePreview.vue          # Teleport 到 body 的抽屉式文件预览；MD 表格工具栏与气泡一致
│   │   └── Snackbar.vue             # MD3 toast 提示
│   ├── composables/
│   │   ├── useSSE.js                # ReadableStream SSE 逐行解析 async generator
│   │   ├── useKeyboard.js           # visualViewport 键盘适配 + 手动上滚检测
│   │   ├── useTheme.js              # 暗色/亮色切换（VueUse useColorMode + gw-theme + <html> light-mode class）
│   │   ├── useMainView.js           # mainView 三态推导（no-workspace / no-session / chat）；配套 useMainView.test.js
│   │   ├── useSession.js            # selectSession/selectWorkspace/startDraftChat/promoteDraftToSession/discardDraft（App.vue + Sidebar 共用）
│   │   ├── usePathPicker.js         # openPathPicker() Promise API + PathPicker 状态
│   │   ├── useScroll.js             # 消息容器滚动控制（注册容器 + 未锁定则滚底 + 上滚检测）
│   │   └── useChat.js               # sendMessage / resendFailedMessage / regenerateAssistantMessage / runChatTurn；不含 DOM 操作
│   ├── messageTurn.js               # 失败回合判定（normalizeFailedTurns、applyTurnOutcome）；配套 messageTurn.test.js
│   └── styles/
│       ├── tokens.css               # MD3 颜色/elevation/shape token（双主题）
│       ├── components.css           # MD3 组件基类（button, spinner 等 + 弹窗共用的 .field 表单字段；dialog 外壳已由 BaseDialog.vue 承担）
│       └── layout.css               # 仅 app-shell：#app, #root-layout, #chat, mobile-topbar, .mobile-overlay, .drop-overlay（含各自的移动端 @media）。dialog 外壳已移入 BaseDialog.vue scoped。组件专属样式（含其移动端 @media 规则）均在各组件 <style scoped>
└── dist/                            # `vite build` 输出 (gitignore)
```

## 文件职责清单

逐文件说明每个文件负责哪个组件 / 承担什么逻辑。改动前先在此定位，避免把逻辑落错层（对照「前端设计约束」7–8：`App.vue` 只编排、composable 不碰 DOM）。

### 入口与配置

| 文件 | 负责 | 关键逻辑 |
|------|------|----------|
| `index.html` | HTML 外壳 | 定义 `#app` 挂载点（内含初始 loading spinner，Vue mount 后被替换）；`viewport` 带 `interactive-widget=resizes-visual`（iOS 键盘缩小 visual viewport）；`<link rel=icon>` 由 Gateway 在 `--icon` 时服务 |
| `vite.config.js` | 构建配置 | `base:'/spa/'` 匹配 Gateway 静态路由；`@`→`/src` alias；输出 `dist/`，assets 进 `dist/assets/`；`test`（Vitest：`environment:'node'` + `include:['src/**/*.test.js']`） |
| `package.json` | 依赖声明 | `dev/build/preview/test` 脚本（`test`=`vitest run`）；Vue + 文件预览大库（懒加载，不进主 bundle）；devDep 含 `vitest` |
| `src/main.js` | 应用引导 | `createApp(App)`；`app.use(createPinia())`（在 `app.mount` 之前挂载）；顶层 `import` 全局 CSS（material-symbols、katex、tokens/components/layout）；注册全局 `v-focus` 指令（mounted 时 `el.focus()`，供 Sidebar 改名 input 使用）；`mount('#app')` |

### 状态与数据层（无组件，纯逻辑）

| 文件 | 负责 | 关键逻辑 / 导出 |
|------|------|-----------------|
| `src/stores/ai.js` | AI 领域 store | `useAiStore` setup-store，导出 `ais/selectedAiId/aiForm/fetchedModels/loadingModels/modelPanelOpen`（详见「状态管理」表） |
| `src/stores/session.js` | Session 领域 store | `useSessionStore` setup-store，导出 `sessions/selectedSessionId/draftSession/sessionTitles/sessionMessages/…`；`draftSession` 为 client-only 草稿（首条发送前不 POST）；`pinnedSessionIds` 在 setup 内经 `loadPinnedSessionIds(window.localStorage)` 初始化 |
| `src/stores/chat.js` | Chat 领域 store | `useChatStore` setup-store，导出 `messages/inputText/streaming/abortController/selectedFiles/userHasScrolledUp/uploadResetToken` |
| `src/stores/ui.js` | UI 领域 store | `useUiStore` setup-store，导出 `loadingEnv/isLightMode/isDragging/dlgAI/dlgSess/snackbar/dlgConfirm/isSidebarCollapsed/isMobileSidebarOpen/sessionSearchFocusToken/hubMenuOpen/hubPanel` + action `showAlert`/`toggleSidebar`/`closeMobileSidebar`/`focusSessionSearch`/`toggleHubMenu`/`openHubPanel`/`closeHubPanel` |
| `src/api.js` | HTTP 封装 | `api(method,path,body)` — JSON fetch，非 2xx 抛错；`streamChat(sessionId,formData)` — POST multipart，返回 `body.getReader()` 供 SSE 消费。`G()` 取 `window.location.origin` |
| `src/utils.js` | 纯工具 + 持久化 | `renderMd`（marked+KaTeX，见「Markdown 渲染流程」）；`htmlEscape`（用户输入转义）；`mimeType`（扩展名→MIME）；localStorage 读写：`saveActiveState/loadActiveState`（`gw-active-ids`）、`saveHistory/loadHistory/clearHistory`（`gw-hist-<id>`：role/text/files + stopped/failed/failedReason/feedback） |
| `src/providers.js` | 预置 provider 表 | 导出 `PROVIDERS` 数组：13 家供应商的 `v`(值)/`l`(标签)/`base`(默认 base_url)/`models`(预置模型名)，供 AiDialog 高级下拉 |
| `src/modelPresets.js` | Hub 快捷连接预设 | 导出 `MODEL_PRESETS`（id/label/mark/accent + provider/model/base_url）、`getModelPreset`、`presetToAiPayload`；HubModelsPanel 只填 api_key |
| `src/userProfile.js` | 用户资料持久化 | `LS_USER_NAME`/`LS_USER_AVATAR` + `readAvatarDataUrl(file)`（≤512KB 图片 → data URL） |
| `src/sessionList.js` | 会话列表纯逻辑 | 无副作用工具（可单测）：`PINNED_SESSIONS_KEY`、`PLACEHOLDER_SESSION_TITLE`（「新对话」）、`isPlaceholderSessionTitle`、`getSessionDisplayName`(标题优先、回退 workspace/占位)、`loadPinnedSessionIds/savePinnedSessionIds/togglePinnedSessionId`(置顶持久化 + 去重规范化)、`buildSessionTitlePayload`、`buildVisibleSessions`(搜索过滤 + 置顶排在前) |
| `src/shortcuts.js` | 快捷键判定纯逻辑 | `matchSidebarShortcut(event)` → `'new-session'`(Ctrl/Cmd+Shift+O) / `'focus-search'`(Ctrl/Cmd+Shift+K) / `null`；用 `event.code` 而非 `key`（规避 Shift 大小写/输入法差异），`ctrlKey||metaKey` 兼容 Windows/macOS。配套 `shortcuts.test.js` 单测 |

### 根组件

| 文件 | 负责 | 关键逻辑 |
|------|------|----------|
| `src/App.vue` | 编排层 | 组装 `#root-layout`；消费 `useMainView()` 切换主区三态：`NoWorkspaceView` / `NoSessionView` / 聊天（空消息欢迎 + `ChatArea` + **仅 chat 态挂载** `InputBar`）；`handleNewSession` → `startDraftChat`（不再弹 SessDialog）；拖拽上传仅在 `isChatActive` 时生效；`onMounted` 恢复 workspace/sessId（不恢复 draft）。**不写发送业务逻辑** |

### 组件 `src/components/`

| 文件 | 组件职责 | 关键逻辑 |
|------|----------|----------|
| `Sidebar.vue` | 会话列表侧栏 | 工作区树 + draft 虚拟行「新对话」（client-only）+ 真实 session 列表；「+」/底部按钮 emit `new-session` → `startDraftChat` |
| `InputBar.vue` | 输入区 UI | 仅 chat 态由 App 挂载；Enter 发送（`useChat.sendMessage`，首条发送时 `promoteDraftToSession`） |
| `ModelPanel.vue` | 会话模型切换 | 由 InputBar 内嵌；chip 显示当前模型；浮层只切换/`delete` 已连接模型（**不**提供链接入口——链接走右上角 User Hub → 大模型） |
| `BaseDialog.vue` | 弹窗外壳 | 所有弹窗的基类：overlay + dialog + `title`/默认/`actions` 三插槽；`show` prop 控制，overlay 点击 emit `close`；`.ok`/`.cancel` 按钮样式在此 scoped |
| `UserHub.vue` | 用户菜单入口 | 头像下拉 → 打开 HubProfile/Models/Login/Settings 面板；桌面 `#topbar` + 移动 `#mobile-topbar` 共用 |
| `HubModelsPanel.vue` | Hub 大模型配置 | 启动时由 App 打开；预设 + API Key → POST `/ais`；「使用免费模型」（蓝色）= 关闭不传参；「高级配置」→ AiDialog |
| `HubProfilePanel.vue` | Hub 我的资料 | 称呼 + 头像上传（写 `gw-user-name`/`gw-user-avatar`）；MessageBubble 与右上头像同步 |
| `AiDialog.vue` | 链接大模型弹窗（高级） | 基于 BaseDialog；provider 自定义下拉（选中回填 base_url）；模型名自定义下拉（替代原生 datalist：↑↓/Enter/Esc 键盘导航 + 输入过滤 + `@mousedown.prevent` 防 blur）；base_url/api_key `@change` 触发 `fetchModels`；无 AI 时 `handleCancel` 拒绝关闭 |
| `SessDialog.vue` | 创建会话弹窗 | 基于 BaseDialog；确认当前工作区 + 大模型后 emit `create` |
| `PathPickerDialog.vue` | 路径选择器 | 资源管理器式 UI（左侧快捷位置 + 面包屑 + 列表 + 底部路径确认）；由 `usePathPicker.openPathPicker()` 控制；侧栏「打开工作区」直接调用，无中间 WorkspaceDialog |
| `usePathPicker.js` | 路径选择 composable | `openPathPicker({ mode, title, initialPath, … }) → Promise<path\|null>`；fetch `/workspace/roots` + `/workspace/browse` |
| `FilePreview.vue` | 文件预览抽屉 | `Teleport` 到 `body` 的抽屉面板；按扩展名分派：图片/音视频/SVG 用 Blob URL，`.md` 用 `renderMd`，`.html`/`.htm` 用沙箱 iframe（`sandbox=""` + blob URL，无脚本/无同源），其它代码用 codemirror，csv/pdf/office 懒加载；不解析磁盘路径、不回灌 workspace；MD 预览里 GFM 表与气泡同款 `.md-table-card` 工具栏（点击委托同 `mdTable.js`）；emit `close` |
| `sendMarkers.js` | 传输标记清理 | `stripTransferMarkers`：展示/历史回放前去掉 `[SEND:…]` **与** `[RECV:…]`（防绝对路径泄露到气泡与复制；附件走 blob chip）。**展示层临时过滤**——服务端 JSONL 仍可能存标记；长期需改 history 投影或协议。`stripSendMarkers` 为同名别名 |
| `mdTable.js` | 气泡/预览表格工具 | `wrapMdTableHtml` 给 GFM `<table>` 加复制/下载工具栏；`matrixToTsv` / `downloadMatrixXlsx`；`MessageBubble` 与 `FilePreview` MD 预览事件委托处理点击 |
| `SessionStreamIndicator.vue` | 侧栏流式指示 | streaming 时 spinner；后台回合完成未读蓝点；打开该会话清除 |
| `ConfirmDialog.vue` | 通用确认弹窗 | 基于 BaseDialog；`dlgConfirm` 经 `storeToRefs` 从 `useUiStore` 取；渲染 `dlgConfirm.message`，「删除」emit `confirm`（App.vue 按 `actionType` 分派） |
| `Snackbar.vue` | Toast 提示 | `snackbar` 经 `storeToRefs` 从 `useUiStore` 取；渲染 `snackbar`（message + 显隐），「知道了」关闭 |

### Composables `src/composables/`（纯逻辑，DOM 操作受约束 8 限制）

| 文件 | 负责 | 关键逻辑 / 导出 |
|------|------|-----------------|
| `useChat.js` | 发送消息 | `sendMessage()` 先乐观 UI（清输入、用户气泡、ThinkingBubble），再 `encodeFiles` / draft 时 `promoteDraftToSession` / `runChatTurn()`；失败时 `abortOptimisticSend`。流式结束后 `applyTurnOutcome` + `normalizeFailedTurns`；`outcome === 'ok'` 且当前未聚焦该会话时 `POST /ui/attention`（托盘脉冲 / webview 任务栏闪烁）。**刻意**：SSE `type:'reasoning'`（thinking + tool markers）**不分新气泡**——一轮 user → 至多一个主 assistant。`regenerateAssistantMessage` / `resendFailedMessage`。气泡文案经 `stripTransferMarkers`。滚动委托 `useScroll` |
| `useSSE.js` | SSE 解析 | `async function* readSSE(reader)`：TextDecoder 累积 → `\r\n`→`\n` → 逐行取 `data:` → `[DONE]` 结束 / `JSON.parse` / 非 JSON 降级为 `{type:'text'}` |
| `useSession.js` | 会话切换 | `selectSession(id)`：保存旧会话 messages+inputs（含 files）→ 切 id → `restoreSessionView`（无 live AbortController 时清 stale `streaming`）→ 从 `/history` 或 localStorage 加载消息时对 **user/assistant** 均 `stripTransferMarkers`（防 `[RECV:]` 路径泄露）→ 同步 selectedAiId → `saveActiveState` → 滚底 + 关移动侧栏。App.vue 与 Sidebar 共用 |
| `useScroll.js` | 滚动控制 | 模块级单例容器：`registerScrollContainer`（由 ChatArea 注册）；`onContainerScroll`（距底 >60px 视为手动上滚，置 `userHasScrolledUp`）；`scrollToBottomIfLocked`（未锁定则 `nextTick` 滚底） |
| `useKeyboard.js` | 移动端视口适配 | `onMounted` 挂 `visualViewport` resize/scroll + window.resize 监听，同步 `#input-wrapper`/`#mobile-topbar`/`#messages`/`#sidebar`/`.mobile-overlay` 的键盘偏移内联样式；>768px 清空所有内联样式；textarea focus 时延迟滚底。移动端视口逻辑的唯一归属地（约束 11） |
| `useTheme.js` | 主题切换 | `useTheme()` 函数体内部调用 `useUiStore()`（不在模块顶层）；用 VueUse `useColorMode` 管理主题（`modes: { light: 'light-mode', dark: '' }` 增删 `<html>` class，`storageKey: 'gw-theme'`）；显式配置 `disableTransition: false` / `emitAuto: false` / 自建 `storageRef`（`writeDefaults: false`）保持既有行为；`toggleTheme()` 切换 `mode`，`ui.isLightMode` 经 `watch` 同步 |

### 样式 `src/styles/`（分层不越界，见约束 10）

| 文件 | 负责 | 内容 |
|------|------|------|
| `tokens.css` | MD3 设计 token | `:root`(暗)/`:root.light-mode`(亮) 双主题 CSS variables：`--md-*` 颜色、`--md-elevation-1/2/3`、`--md-shape-*`、`--md-state-*`；全局 box-sizing reset。调色板采用 Neural Expressive 风格（品牌蓝 primary、中性 surface 分层、圆润 shape），另有 `--g-grad-hello`（问候语渐变）、`--g-spark`（四色 spark）、`--g-pill-radius` 三个 `--g-*` 专用变量 |
| `components.css` | MD3 组件基类 | `body` 基础样式；`.md-icon-btn`/`.md-filled-btn` 等按钮基类 + 弹窗共用的 `.field` 表单字段。dialog 外壳已由 BaseDialog 承担 |
| `layout.css` | app-shell 布局 | 仅 `#app`/`#root-layout`/`#chat`、`.sidebar-toggle-btn`/`.theme-toggle-btn`、`#mobile-topbar`、`.mobile-overlay`、`.drop-overlay`（含各自移动端 `@media`）。组件专属样式一律在各组件 `<style scoped>` |
| `highlight.css` | 代码高亮主题 | highlight.js 语法着色的双主题实现：`--hl-*` 语法配色变量（深色 `:root` / 浅色 `:root.light-mode`）映射到 `.hljs-*` token class。不引用 hljs 自带主题（硬编码色、不随明暗切换）；调色/新增语言只改此文件的变量 |

## 构建配置

`vite.config.js`:
- `base: '/spa/'` — 匹配 Gateway 的 `/spa/` 静态路由
- `@` alias → `/src`
- 输出目录 `dist/`，assets 放 `dist/assets/`

`index.html`:
- `<meta viewport interactive-widget=resizes-visual>` — iOS 键盘弹出时缩小 visual viewport 而非 overlay
- `<link rel="icon" href="/favicon.ico">` — favicon 由 Gateway 在 `--icon` 设置时服务（即托盘/icon 图标）；未设置时该请求 404
- `<title>控制台</title>`
- 初始 `#app` 内含加载 spinner，Vue mount 后被替换

## 根组件 `App.vue` 架构

App.vue 是**编排层**：负责跨组件事件处理、弹窗控制、drag-drop 上传、主题/侧栏切换，以及 `onMounted` 启动流程。sidebar 会话列表业务逻辑在 `Sidebar.vue`，输入/发送业务逻辑在 `InputBar.vue`。

```
#root-layout
├── .page-loader          (v-if loadingEnv — 全屏 spinner)
├── .mobile-overlay       (v-if 移动端抽屉打开 — 半透明遮罩)
├── <Sidebar>             (@new-session → handleNewSession → startDraftChat)
├── #chat
│   ├── NoWorkspaceView / NoSessionView / ChatArea+InputBar（由 useMainView 切换）
├── <AiDialog>
├── <ConfirmDialog>
└── <Snackbar>
```

**注意**：`#app` 在 `index.html` 中已经存在，`App.vue` 模板使用 `#root-layout` 作为内部根元素，避免 duplicate `#app`。

## 状态管理 — `src/stores/`（4 个 Pinia setup-store）

按领域拆分为 4 个 store，均用 `defineStore(name, () => {...})` 的 setup 写法（内部用 `ref`，`return` 暴露给外部）。组件里用 `storeToRefs(store)` 取 state（保响应式），action 直接从 store 实例调用（如 `ui.showAlert(...)`）。

### `stores/ai.js` — `useAiStore`

| 字段 | 类型 | 用途 |
|------|------|------|
| `ais` | `array` | 服务端 AI 列表（唯一数据源） |
| `selectedAiId` | `string/null` | 当前选中的 AI |
| `aiForm` | `object` | AiDialog 弹窗表单数据 |
| `fetchedModels` | `array` | 从 provider API 获取的模型列表 |
| `loadingModels` | `bool` | 模型列表加载中 |
| `modelPanelOpen` | `bool` | 模型浮层开关 |

### `stores/session.js` — `useSessionStore`

| 字段 | 类型 | 用途 |
|------|------|------|
| `sessions` | `array` | 服务端 Session 列表（唯一数据源） |
| `selectedSessionId` | `string/null` | 当前选中的**已落库**会话 |
| `draftSession` | `object/null` | client-only 草稿 `{ draftId, workspace, aiId }`；首条 `sendMessage` 时 promote |
| `sessionTitles` | `object` | sessionId → AI 生成的标题 |
| `sessionMessages` | `object` | sessionId → messages（切换时保留状态；后台流式仍写入此缓存） |
| `sessionInputs` | `object` | sessionId → {text, files}（切换时保留输入框及已选文件，`files` 为数组） |
| `sessionStreaming` | `object` | sessionId → bool（各会话独立 SSE 进行中标记；切换侧栏不 abort 后台流） |
| `sessionAbortControllers` | `object` | sessionId → AbortController（内存 only；停止钮仅作用于当前可见 session） |
| `sessForm` | `object` | （遗留字段，SessDialog 已废弃） |
| `editingSessionId`, `editingWorkspaceText` | `string` | 会话标题编辑状态 |
| `sessionSearchText` | `string` | 侧栏会话搜索框文本（`sessionList.js` 的 `buildVisibleSessions` 据此过滤） |
| `pinnedSessionIds` | `string[]` | 置顶会话 id 列表；在 store 的 setup 内经 `loadPinnedSessionIds(window.localStorage)` 从 `gw-pinned-session-ids` 初始化，见 `sessionList.js` |

### `stores/chat.js` — `useChatStore`

| 字段 | 类型 | 用途 |
|------|------|------|
| `messages` | `array` | 当前会话消息列表 |
| `inputText` | `string` | textarea v-model |
| `streaming` | `bool` | 是否正在 SSE 接收中 |
| `abortController` | `AbortController/null` | 当前流式请求的中止控制器（`stopMessage` 用来中断生成） |
| `selectedFiles` | `File[]` | 已选中的上传文件列表（多文件；拖放也追加至此） |
| `userHasScrolledUp` | `bool` | 手动上滚时暂停自动滚动 |
| `uploadResetToken` | `number` | 递增以通知 InputBar 清空 file input |

### `stores/ui.js` — `useUiStore`

| 字段/action | 类型 | 用途 |
|------|------|------|
| `loadingEnv` | `bool` | 初始加载遮罩 |
| `isLightMode` | `bool` | 主题（默认亮色） |
| `isDragging` | `bool` | 拖放文件至 #chat 时的遮罩状态 |
| `dlgAI`, `dlgSess` | `bool` | 弹窗开关 |
| `snackbar`, `dlgConfirm` | `object` | 提示/确认弹窗状态 |
| `isSidebarCollapsed`, `isMobileSidebarOpen` | `bool` | 侧栏状态 |
| `showAlert(message)` | `action` | 显示 snackbar 提示，4 秒后自动隐藏（若消息未被新提示覆盖） |
| `toggleSidebar(isMobile)` | `action` | 按 `isMobile` 切换移动端抽屉或桌面折叠状态 |
| `closeMobileSidebar()` | `action` | 关闭移动端抽屉 |

## Markdown 渲染流程 (`utils.js:renderMd`)

```
原始 text
  → regex 提取 $$...$$ / $...$ → 替换为 \x00MATH{i}\x00 占位
  → marked.parse() 转 HTML（code renderer 已被 highlight.js 覆盖）
  → katex.renderToString() 替换回占位符
  → 返回 HTML
```

**关键细节**：
- KaTeX 设置 `throwOnError: false`，语法错误时 fallback 到 `<code>` 标签
- `marked.parse()` 失败时降级为 `htmlEscape()` 纯文本
- **代码块语法高亮**：`marked` 的 `code` renderer 被覆盖，用 `highlight.js/lib/common`（仅常用语言，控制体积）产出带 `hljs language-xxx` class 的 `<pre><code>`；语言标注无效或高亮抛错时回退纯转义文本，绝不丢内容。着色由 `styles/highlight.css` 承担（双主题，`--hl-*` 变量映射 hljs token class，调色只改该文件）。内联代码不高亮，仅由 MessageBubble 的 `<style scoped>` 加浅背景片区分
- **GFM 表格**：`utils.js` 已开 `gfm: true`，并在 `renderMd` 前做表格规范化（去掉表格块内空行、解开误包在 code fence 里的纯表格）；自定义 `table` renderer 包一层 `.md-table-card` 工具栏（复制 TSV / 下载 `.xlsx`，逻辑在 `mdTable.js`，点击由 `MessageBubble` 事件委托处理）；表格 `width:100%` + `table-layout:fixed`，单元格自动换行
- **助手气泡排版**：`MessageBubble` 内增大行高/字距，并拉开标题与段落、列表间距（参考常见 Agent 正文节奏），用户气泡仍 `pre-wrap`

## SSE 流式解析 (`useSSE.js:readSSE`)

```
ReadableStream reader
  → TextDecoder 累积块
  → 统一 \r\n → \n
  → 逐行切割 "data:" 前缀行
  → [DONE] 结束 / JSON.parse 解析
  → 非 JSON 非 {}[] 开头 → yield {type:'text', text: p} 纯文本降级
```

**注意**：这是一个 `async function*` generator，`useChat.js` 中通过 `for await (const chunk of readSSE(reader))` 消费。

## 文件预览 (`FilePreview.vue`)

MessageBubble 中点击附件 → 触发 `FilePreview` 组件（Teleport 到 `body` 的抽屉式面板）。组件按文件扩展名动态 import 对应预览库（懒加载，不进主 bundle）：

| 扩展名 | 预览方式 |
|--------|----------|
| `.md` / `.markdown` | `renderMd`（文档样式） |
| `.html` / `.htm` | 沙箱 iframe + blob URL（只展示，不跑脚本） |
| `.js/.ts/.py/.java` 等代码文件 | `codemirror` |
| `.pdf` | `pdfjs-dist` |
| `.docx` | `docx-preview` |
| `.pptx` | `pptx-preview` |
| `.xlsx/.xls` | `xlsx` |
| `.csv` | `papaparse` |

预览只消费 SSE blob（name + base64），**不**按 `[SEND:]`/`[RECV:]` 路径回读磁盘。bubble/history 文本用 `stripTransferMarkers` 去掉残留标记（防绝对路径泄露）。blob 读失败若已有正文/附件，不当整轮失败。

props 含预览目标（文件名 + 数据），emit `close` 关闭抽屉。

## localhost 持久化策略

**原则**：服务端是唯一数据源（AI/Session 列表从远端 GET），localStorage 仅存 UI 状态和对话缓存。

| Key | 内容 | 来源 |
|-----|------|------|
| `gw-active-ids` | `{aiId, sessId}` | 选中的 AI/Session ID |
| `gw-hist-<id>` | `[{role, text, files}]` | 对话历史（文件 blob 合并服务端文本） |
| `gw-sidebar-state` | `'expanded'/'collapsed'` | 侧栏折叠状态（由 `App.vue` 经 VueUse `useStorage` 读写） |
| `gw-theme` | `'light'/'dark'` | 主题偏好 |
| `gw-pinned-session-ids` | `string[]` | 置顶会话 id 列表（由 `sessionList.js` 的 `loadPinnedSessionIds`/`savePinnedSessionIds` 读写） |
| `gw-user-name` | `string` | 用户称呼（欢迎语、消息气泡显示名、Hub 头像首字母 fallback） |
| `gw-user-avatar` | `string` | 用户头像 data URL（Hub「我的资料」上传；右上头像与消息气泡同步） |

Session 标题由服务端 `/titles` 维护，**不在** localStorage 存储。

## App.vue 核心业务流程

### 启动流程 (`onMounted` — `App.vue`)
```
1. GET /titles → session store 的 sessionTitles
2. GET /ais + GET /sessions → ai store 的 ais / session store 的 sessions
3. 仍无 AI → ensureDefaultAi()（SPA POST /ais → 远程免费默认）
4. loadActiveState → 恢复选中 AI/Session ID
5. selectSession (useSession.js) → 从 /history + localStorage 加载消息
6. loadingEnv = false → 打开 Hub 模型池
```
打开即用：远程 `https://haitun.addchess.cn`（常量见 `bootstrapAi.js`）；启动展示模型池；「使用免费模型」仅关闭面板（不传参）。Hub/高级配置可覆盖。

### 发送消息 (`sendMessage` — `composables/useChat.js`)
```
1. 提取 inputText + selectedFiles（数组，含拖放文件）
2. 乐观 UI：清空输入/附件 → 用户气泡 (addMessage + htmlEscape) → streaming=true → 空 assistant（ThinkingBubble）
3. encodeFiles → FileReader.readAsDataURL → base64 写入用户气泡
4. draft 会话 → promoteDraftToSession（POST /sessions，~数秒）→ 消息列表迁移到新 sid
5. ensureSessionSidebarTitle（fire-and-forget）→ runChatTurn → streamChat() POST /sessions/{id}/chat
6. for await (readSSE(reader)):
   - text chunk → asst.text += chunk.text → renderMd → 更新 v-html
   - blob chunk → asst.files.push({name, data})
   - scrollChatAreaIfLocked → 自动滚底（unless userHasScrolledUp）
7. streaming=false → saveHistory → 仍为占位标题 → generateTitle → POST `/titles/generate`
8. promote/encode/SSE 失败 → abortOptimisticSend（清 streaming、标 user failed、移除空 assistant）
```

**新特性**：
- `<input type="file" multiple>` 支持一次选多文件，`onFileSelected` 追加至 chat store 的 `selectedFiles`
- 拖放文件至 `#chat` 区域（`App.vue` 用 VueUse `useDropZone` 监听）→ 追加到 chat store 的 `selectedFiles`，`InputBar` 统一处理上传
- `#input-wrapper` 底部显示 `Enter 发送 · Shift+Enter 换行` 提示（`.input-hint`）

### 切换 AI (`selectAI` — `InputBar.vue` → 事件冒泡至 `App.vue`)
```
DELETE /sessions/{oldId} → POST /sessions {id:oldId, ai_id:newId, ...}
→ 同 ID 新 AI，消息清空
```

### 编辑标题 (`saveSessionWorkspace` — `Sidebar.vue`)
```
DELETE + POST /sessions (同 id, 原 workspace) → POST /titles
→ sidebar 刷新
```

### 新对话与首条发送
```
Sidebar「+」/「新对话」或快捷键 → startDraftChat（不 POST）
→ mainView=chat，InputBar 可用，sidebar 显示 draft 行「新对话」
→ sendMessage → 立即显示用户气泡 + ThinkingBubble → promoteDraftToSession（POST /sessions）→ SSE
→ 未发送就切走 → discardDraft，后端无记录
```

### 会话切换 (`selectSession` — `composables/useSession.js`)
```
消息以 sessionMessages[id] 为唯一数据源（后台 SSE 持续写入同一数组）
→ 保存旧会话 sessionInputs + sessionStreaming + sessionAbortControllers
→ 切换 selectedSessionId → mirrorSessionMessages 恢复 chat.messages → 恢复 streaming/abortController（**不 abort 后台 SSE**）
→ 无缓存则从 /history 或 localStorage 加载
→ 滚底 + 关闭移动端侧栏

切换会话或工作区时会 discardDraft；promote 后走 selectSession 等价路径恢复消息缓存。
```

## 移动端适配 (`useKeyboard.js`)

### visualViewport 键盘同步
用 VueUse `useEventListener` 监听 `resize`、`scroll`、`window.resize` 三种事件（组件卸载自动清理），覆盖键盘弹出、滚动偏移、横竖屏切换。

| 元素 | 动态调整 |
|------|----------|
| `#input-wrapper` | `bottom` = 键盘高度 |
| `#mobile-topbar` | `top` = 视口偏移 |
| `#messages` | `top` + `padding-bottom` |
| `#sidebar` | `top` |
| `.mobile-overlay` | `top` |

桌面端（>768px）清空所有动态内联样式。

### 响应用户手动上滚
`#messages` `scroll` 事件（VueUse `useEventListener`）检测：距底部 > 60px → `userHasScrolledUp=true`，暂停自动滚动。回到底部时自动恢复。

## MD3 主题系统 (`tokens.css` + `useTheme.js`)

双主题通过 `:root`/`:root.light-mode` CSS variables 切换：

- **颜色**：`--md-bg`, `--md-surface`, `--md-primary`, `--md-outline` 等 20+ token
- **Elevation**：`--md-elevation-1/2/3` 三层阴影
- **Shape**：`--md-shape-extra-small` ~ `--md-shape-full`
- **State layer**：`--md-state-hover/press/focus` opacity 值

切换逻辑：`useTheme.js` 用 VueUse `useColorMode` 管理（`modes: { light: 'light-mode', dark: '' }` 给 `<html>` 增删 `light-mode` class，`storageKey: 'gw-theme'`）。为保持既有行为显式配置：`disableTransition: false`（保留切换过渡动画）、`emitAuto: false` + 默认 `'light'`（不跟随系统偏好）、自建 `storageRef`（`writeDefaults: false`，用户不切换则从不写 localStorage）。`ui.isLightMode` 经 `watch(mode)` 同步（非 `'dark'` 即亮色）。

## 关键设计经验

1. **`addMessage` 必须返回 reactive proxy** — `useChat.js` 的 `addMessage` 内 `return chat.messages[chat.messages.length-1]`（`chat` 为 `useChatStore()` 实例），不能返回原始 object。否则 SSE 流式期间修改不触发 Vue 重渲染。

2. **`nextTick` 必须 await** — Vue 批处理未 flush 时 DOM 不会更新。`scrollChatAreaIfLocked`、auto-scroll 都依赖 `await nextTick()`。

3. **`white-space: normal` 在 `.msg .bubble p`** — 消息气泡最后一个 `<p>` 如果用 `pre-wrap` 会在末尾产生空白行，用 `normal` 避免。bubble 本身用 `pre-wrap` 保留 AI 回复中的换行。

4. **Blob URL 缓存** — `MessageBubble.fileUrl()` 将 base64 数据转为 `URL.createObjectURL` 后缓存到 `f._url`，避免重复创建。

5. **100dvh 替代 100vh** — iOS Safari 地址栏收缩时 `100vh` 不准确，`dvh` 动态跟随 viewport 高度。

6. **`overscroll-behavior: none`** — 禁止 iOS Safari 橡皮筋弹性滚动。

7. **`interactive-widget=resizes-visual`** — 虚拟键盘弹出时缩小 visual viewport 而非覆盖页面。

8. **provider 响应格式兼容** — `fetchAvailableModels` 同时处理 `{data: [{id}]}` (OpenAI) 和 `{models: [{name/id}]}` (Anthropic) 格式。

9. **自定义 model 下拉替代原生 datalist** — `<datalist>` 在不同浏览器中行为不一致（样式不可控、键盘导航不稳定）。`AiDialog.vue` 实现纯 Vue 下拉：keyboard nav（↑↓⊞ Esc）+ 输入过滤 + `@mousedown.prevent` 防止 blur 抢先。

10. **非流式 `/titles/generate`** — 标题生成先 POST 到 server 端，server 端再同步调用 AI socket 获取结果后返回 JSON，前端不直接处理标题生成的 SSE。

## 构建与集成

### 开发
```bash
cd src/psi_agent/gateway/spa
npm run dev        # Vite dev server on :5173
```

Gateway 另开终端运行，Vite 代理或前端直连 Gateway API。

### 测试
```bash
npm test           # Vitest 跑 src/**/*.test.js（纯逻辑/store 单测）
```

### 生产
```bash
npm run build      # → dist/
```

Gateway `server.py` 通过 `app.router.add_static('/spa/', str(spa_dist), show_index=False)` 服务。`dist/` 目录需在 Nuitka/PyInstaller 构建前生成。

### CI
Nuitka/PyInstaller 工作流中先执行 `npm ci && npm run build`，然后通过 `--include-data-dir`/`--add-data` 将 `dist/` 打包进二进制。
