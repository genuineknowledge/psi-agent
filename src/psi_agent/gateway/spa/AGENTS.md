# SPA 前端设计文档

## 概述

Web 控制台是一个本地打包、无外部 CDN 依赖的 Vue 3 单页应用（SPA），由 Vite 构建为静态文件，通过 Gateway 的 `/spa/` 路由服务。支持多格式文件预览（代码/PDF/Word/PPT/Excel/CSV），依赖已显著增加，不再是轻量前端。

## 前端设计约束

以下是开发本 SPA 时必须遵守的约定，与根 `AGENTS.md` 的「设计理念」同级——凡新增/改动组件、样式、状态，先对照本节：

1. **本地打包 / 无外链**：所有依赖经 npm 安装、由 Vite 打进 `dist/`。禁止在 `index.html` 或组件里引 `<script src="https://...">` / `<link href="https://...">` 外链——离线与二进制打包环境下外链会失效。字体、图标、CSS 一律走本地包（如 `material-symbols`）。

2. **重依赖必须懒加载**：文件预览类大库（codemirror / pdfjs-dist / docx-preview / pptx-preview / xlsx / papaparse）一律用动态 `import()` 按需加载，禁止顶层静态 import 拖进主 bundle。新增同类大库照此办理，并在「技术栈」的文件预览依赖表补一行「用途」。

3. **技术栈封闭**：默认不再引入 **Vue Router**（单页无路由）、**Pinia**（单一 `reactive()` store 足够）、**TypeScript**、**CSS 预处理器**。确需新增第三方库时，先评估体积与是否可 tree-shake，并在「技术栈」表补一行「选择理由」。

4. **单一数据源 store**：全局/跨组件状态一律进 `store.js` 的单个 `reactive()` 对象，经 `provide/inject` 下发。组件内 `ref` 只用于纯本地 UI 状态（如某个 input 的临时值）。跨组件副作用用 store 信号传递（如 `uploadResetToken` 递增通知 InputBar 清空 file input），不要组件间直接互相调方法。

5. **服务端是唯一数据源**：AI / Session 列表、标题从 Gateway REST GET 获取，`localStorage` 只存 UI 偏好与对话缓存（见「localhost 持久化策略」）。新增持久化项必须用 `gw-` 前缀 key，并同步更新持久化表。

6. **纯逻辑抽到无副作用模块**：会话过滤/置顶/排序等纯计算放 `sessionList.js` 这类无状态工具模块（纯函数、可单测、不碰 DOM 与全局 store），组件只负责调用与渲染。

7. **App.vue 只做编排**：`App.vue` 负责跨组件事件、弹窗控制、drag-drop、主题/侧栏切换与启动流程，**不写具体业务逻辑**。会话逻辑放 `Sidebar.vue` / `useSession.js`，输入发送逻辑放 `InputBar.vue` / `useChat.js`。新业务优先落到对应组件或 composable。

8. **composable 不碰 DOM**：`composables/` 里是纯逻辑（`useChat` 等），不直接操作 DOM。滚动委托 `useScroll`，需要 DOM 的行为通过 store 信号或回调交给组件层。DOM 操作前若依赖渲染结果，必须 `await nextTick()`。

9. **弹窗统一走 BaseDialog**：所有弹窗基于 `BaseDialog.vue`（overlay + dialog + actions 插槽），不各自手写遮罩/定位。新弹窗 = 新建一个基于 BaseDialog 的组件。

10. **样式分层不越界**：全局 MD3 token 在 `styles/tokens.css`，组件基类在 `styles/components.css`，app-shell 布局在 `styles/layout.css`；**组件专属样式（含其移动端 `@media`）一律写在该组件的 `<style scoped>`**。颜色/圆角/阴影/间距用 `--md-*` CSS variable，禁止硬编码色值，保证双主题一致。

11. **响应式与移动端**：断点统一用 `768px`。移动端键盘/视口适配集中在 `useKeyboard.js`，桌面端需清空其注入的内联样式，不要在别处再写第二套 viewport 逻辑。

12. **AI 回复渲染的边界**：`renderMd` 的输出来自可信 AI 后端、非用户输入，故未做 sanitize；若将来渲染用户可控内容，必须先引入 sanitize。用户输入一律经 `htmlEscape` 后显示。

## 技术栈

| 领域 | 技术 | 版本 | 选择理由 |
|------|------|------|----------|
| 框架 | Vue 3 Composition API `<script setup>` | `^3.4.21` | SFC 组织清晰，`reactive()` 替代 Pinia |
| 构建 | Vite | `^8.1.0` | 快 HMR + 静态打包 |
| Markdown | marked | `^18.0.5` | AI 回复非用户输入无需安全 sanitize |
| LaTeX | KaTeX | `^0.17.0` | 同步 `renderToString()` 比 MathJax 快 5-10x，SSE 逐帧重渲染关键 |
| 图标 | Material Symbols | `^0.45.4` | Google MD3 官方当前图标集，CSS `font-variation-settings` 支持 weight/fill 可调 |
| Vue 插件 | `@vitejs/plugin-vue` | `^6.0.7` | Vite SFC 编译 |

**文件预览依赖**（按需动态 import，懒加载，不进主 bundle）：

| 库 | 版本 | 用途 |
|----|------|------|
| `codemirror` | `^6.0.2` | 代码文件语法高亮预览 |
| `pdfjs-dist` | `^5.6.205` | PDF 预览 |
| `docx-preview` | `^0.3.7` | Word（.docx）预览 |
| `pptx-preview` | `^1.0.7` | PowerPoint（.pptx）预览 |
| `xlsx` | `^0.18.5` | Excel 表格预览 |
| `papaparse` | `^5.5.4` | CSV 解析预览 |

**未引入**：
- **Vue Router** — 单页无路由
- **Pinia** — `reactive()` 单一 store 足够
- **TypeScript** — 未引入
- **CSS 预处理器** — 手写 CSS variables（MD3 token 系统）

## 目录结构

```
spa/
├── package.json                     # 依赖声明
├── vite.config.js                   # base=/spa/, @ alias
├── index.html                       # #app mount + interactive-widget=resizes-visual
├── src/
│   ├── main.js                      # createApp + import CSS + v-focus directive
│   ├── App.vue                      # 根组件：编排层 — 跨组件 handler + 弹窗 + drag-drop；sidebar/input 业务逻辑已移入各组件
│   ├── store.js                     # reactive() 单一 store，provide/inject
│   ├── utils.js                     # renderMd, htmlEscape, mimeType, localStorage
│   ├── api.js                       # fetch 封装(api) + chat 流请求(streamChat)
│   ├── providers.js                 # 预置 provider 配置 (deepseek/openai/anthropic/gemini)
│   ├── sessionList.js               # 会话列表纯逻辑工具：置顶(pin)持久化、搜索过滤、显示名、标题 payload 构造。导出：PINNED_SESSIONS_KEY、getSessionDisplayName、loadPinnedSessionIds、savePinnedSessionIds、togglePinnedSessionId、buildSessionTitlePayload、buildVisibleSessions
│   ├── components/
│   │   ├── Sidebar.vue              # 会话列表：新建/双击改名/删除/置顶(pin)/搜索过滤（置顶与搜索逻辑在 sessionList.js）
│   │   ├── ChatArea.vue             # 消息列表容器 + 自动滚动 + 空状态
│   │   ├── MessageBubble.vue        # 单条消息：Markdown + 复制 + 文件附件
│   │   ├── ThinkingBubble.vue       # 等待首 token 的三个脉冲圆点
│   │   ├── InputBar.vue             # 输入 UI：textarea 自适应高度 + 文件选择 + 发送按钮；发送逻辑委托给 useChat.js；内嵌 ModelPanel
│   │   ├── ModelPanel.vue           # 模型管理浮层（自定义下拉替代原生 datalist）；由 InputBar 嵌入
│   │   ├── BaseDialog.vue           # 弹窗外壳(overlay + dialog + actions，插槽:title/默认/actions)
│   │   ├── AiDialog.vue             # 链接大模型弹窗（基于 BaseDialog）
│   │   ├── SessDialog.vue           # 创建会话弹窗 + FileBrowser（基于 BaseDialog）
│   │   ├── FileBrowser.vue          # 目录浏览
│   │   ├── ConfirmDialog.vue        # 通用确认弹窗（基于 BaseDialog）
│   │   ├── FilePreview.vue          # Teleport 到 body 的抽屉式文件预览组件，按扩展名动态分派到 codemirror/pdfjs/docx-preview/pptx-preview/xlsx/papaparse；被 MessageBubble.vue 使用；props 含预览目标，emit close
│   │   └── Snackbar.vue             # MD3 toast 提示
│   ├── composables/
│   │   ├── useSSE.js                # ReadableStream SSE 逐行解析 async generator
│   │   ├── useKeyboard.js           # visualViewport 键盘适配 + 手动上滚检测
│   │   ├── useTheme.js              # 暗色/亮色切换 + localStorage + <html> class
│   │   ├── useSession.js            # selectSession：会话切换 + 草稿/消息缓存（App.vue + Sidebar 共用）
│   │   ├── useScroll.js             # 消息容器滚动控制（注册容器 + 未锁定则滚底 + 上滚检测）
│   │   └── useChat.js               # sendMessage 及其内部辅助（addMessage, encodeFiles, generateTitle）；不含 DOM 操作，滚动委托 useScroll、清空 file input 经 store.uploadResetToken 信号
│   └── styles/
│       ├── tokens.css               # MD3 颜色/elevation/shape token（双主题）
│       ├── components.css           # MD3 组件基类（button, spinner 等 + 弹窗共用的 .field 表单字段；dialog 外壳已由 BaseDialog.vue 承担）
│       └── layout.css               # 仅 app-shell：#app, #root-layout, #chat, mobile-topbar, .mobile-overlay, .drop-overlay（含各自的移动端 @media）。dialog 外壳已移入 BaseDialog.vue scoped。组件专属样式（含其移动端 @media 规则）均在各组件 <style scoped>
└── dist/                            # `vite build` 输出 (gitignore)
```

## 构建配置

`vite.config.js`:
- `base: '/spa/'` — 匹配 Gateway 的 `/spa/` 静态路由
- `@` alias → `/src`
- 输出目录 `dist/`，assets 放 `dist/assets/`

`index.html`:
- `<meta viewport interactive-widget=resizes-visual>` — iOS 键盘弹出时缩小 visual viewport 而非 overlay
- `<link rel="icon" href="/favicon.ico">` — favicon 由 Gateway 在 `--tray` 设置时服务（即托盘图标）；未设置时该请求 404
- `<title>控制台</title>`
- 初始 `#app` 内含加载 spinner，Vue mount 后被替换

## 根组件 `App.vue` 架构

App.vue 是**编排层**：负责跨组件事件处理、弹窗控制、drag-drop 上传、主题/侧栏切换，以及 `onMounted` 启动流程。sidebar 会话列表业务逻辑在 `Sidebar.vue`，输入/发送业务逻辑在 `InputBar.vue`。

```
#root-layout
├── .page-loader          (v-if loadingEnv — 全屏 spinner)
├── .mobile-overlay       (v-if 移动端抽屉打开 — 半透明遮罩)
├── <Sidebar>             (@new-session → openSessDialog)
├── #chat                 (drag-drop 事件：dragenter/over/leave/drop → store.isDragging + store.selectedFiles)
│   ├── .drop-overlay     (v-if store.isDragging — 拖放提示遮罩)
│   ├── #mobile-topbar    (≤768px: 汉堡菜单 + 标题 + 主题切换)
│   ├── .sidebar-toggle-btn / .theme-toggle-btn  (>768px 悬浮按钮)
│   ├── <ChatArea>        (#messages → MessageBubble v-for)
│   └── <InputBar>        (@select-ai, @delete-ai, @new-ai；内嵌 <ModelPanel>)
├── <AiDialog>    (@create, @fetchModels)
├── <SessDialog>  (@create, @browse)
├── <ConfirmDialog> (@confirm)
└── <Snackbar>
```

**注意**：`#app` 在 `index.html` 中已经存在，`App.vue` 模板使用 `#root-layout` 作为内部根元素，避免 duplicate `#app`。

## 状态管理 — `store.js`

单一 `reactive()` 对象，结构如下：

| 字段 | 类型 | 用途 |
|------|------|------|
| `loadingEnv` | `bool` | 初始加载遮罩 |
| `ais`, `sessions` | `array` | 服务端 AI/Session 列表（唯一数据源） |
| `selectedAiId`, `selectedSessionId` | `string/null` | 当前选中项 |
| `sessionTitles` | `object` | sessionId → AI 生成的标题 |
| `messages` | `array` | 当前会话消息列表 |
| `sessionMessages` | `object` | sessionId → messages（切换时保留状态） |
| `sessionInputs` | `object` | sessionId → {text, files}（切换时保留输入框及已选文件，`files` 为数组） |
| `streaming` | `bool` | 是否正在 SSE 接收中 |
| `selectedFiles` | `File[]` | 已选中的上传文件列表（多文件；拖放也追加至此） |
| `isDragging` | `bool` | 拖放文件至 #chat 时的遮罩状态 |
| `inputText` | `string` | textarea v-model |
| `userHasScrolledUp` | `bool` | 手动上滚时暂停自动滚动 |
| `uploadResetToken` | `number` | 递增以通知 InputBar 清空 file input |
| `isLightMode` | `bool` | 主题（默认亮色） |
| `isSidebarCollapsed`, `isMobileSidebarOpen` | `bool` | 侧栏状态 |
| `modelPanelOpen` | `bool` | 模型浮层开关 |
| `dlgAI`, `dlgSess` | `bool` | 弹窗开关 |
| `snackbar`, `dlgConfirm` | `object` | 提示/确认弹窗状态 |
| `aiForm`, `sessForm` | `object` | 弹窗表单数据 |
| `browser` | `object` | 目录浏览 {path, parent, entries} |
| `fetchedModels`, `loadingModels` | `array/bool` | 从 provider API 获取的模型列表 |
| `editingSessionId`, `editingWorkspaceText` | `string` | 会话标题编辑状态 |
| `pinnedSessionIds` | `string[]` | 置顶会话 id 列表（启动时从 `gw-pinned-session-ids` 读入，见 `sessionList.js`） |
| `sessionSearchText` | `string` | 侧栏会话搜索框文本（`sessionList.js` 的 `buildVisibleSessions` 据此过滤） |

## Markdown 渲染流程 (`utils.js:renderMd`)

```
原始 text
  → regex 提取 $$...$$ / $...$ → 替换为 \x00MATH{i}\x00 占位
  → marked.parse() 转 HTML
  → katex.renderToString() 替换回占位符
  → 返回 HTML
```

**关键细节**：
- KaTeX 设置 `throwOnError: false`，语法错误时 fallback 到 `<code>` 标签
- `marked.parse()` 失败时降级为 `htmlEscape()` 纯文本

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

| 扩展名 | 预览库 |
|--------|--------|
| `.js/.ts/.py/.java` 等代码文件 | `codemirror ^6.0.2` |
| `.pdf` | `pdfjs-dist ^5.6.205` |
| `.docx` | `docx-preview ^0.3.7` |
| `.pptx` | `pptx-preview ^1.0.7` |
| `.xlsx/.xls` | `xlsx ^0.18.5` |
| `.csv` | `papaparse ^5.5.4` |

props 含预览目标（文件名 + 数据），emit `close` 关闭抽屉。

## localhost 持久化策略

**原则**：服务端是唯一数据源（AI/Session 列表从远端 GET），localStorage 仅存 UI 状态和对话缓存。

| Key | 内容 | 来源 |
|-----|------|------|
| `gw-active-ids` | `{aiId, sessId}` | 选中的 AI/Session ID |
| `gw-hist-<id>` | `[{role, text, files}]` | 对话历史（文件 blob 合并服务端文本） |
| `gw-sidebar-state` | `'expanded'/'collapsed'` | 侧栏折叠状态 |
| `gw-theme` | `'light'/'dark'` | 主题偏好 |
| `gw-pinned-session-ids` | `string[]` | 置顶会话 id 列表（由 `sessionList.js` 的 `loadPinnedSessionIds`/`savePinnedSessionIds` 读写） |

Session 标题由服务端 `/titles` 维护，**不在** localStorage 存储。

## App.vue 核心业务流程

### 启动流程 (`onMounted` — `App.vue`)
```
1. GET /titles → store.sessionTitles
2. GET /ais + GET /sessions → store.ais/sessions
3. 无 AI → 弹窗 AiDialog（不可关闭，至少需要 1 个）
4. loadActiveState → 恢复选中 AI/Session ID
5. selectSession (useSession.js) → 从 /history + localStorage 加载消息
6. loadingEnv = false
```

### 发送消息 (`sendMessage` — `composables/useChat.js`)
```
1. 提取 inputText + selectedFiles（数组，含拖放文件）
2. Files → FileReader.readAsDataURL → base64（encodeFiles）
3. 用户消息立即显示 (addMessage + htmlEscape)
4. AI 消息气泡出现 (addMessage, streaming=true)
5. FormData + streamChat() POST /sessions/{id}/chat（multipart，多文件 append）
6. for await (readSSE(reader)):
   - text chunk → asst.text += chunk.text → renderMd → 更新 v-html
   - blob chunk → asst.files.push({name, data})
   - scrollChatAreaIfLocked → 自动滚底（unless userHasScrolledUp）
7. streaming=false
8. saveHistory → localStorage
9. 无标题 → generateTitle → POST /titles/generate
```

**新特性**：
- `<input type="file" multiple>` 支持一次选多文件，`onFileSelected` 追加至 `store.selectedFiles`
- 拖放文件至 `#chat` 区域（由 `App.vue` 处理 drag 事件）→ 追加到 `store.selectedFiles`，`InputBar` 统一处理上传
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

### 会话切换 (`selectSession` — `composables/useSession.js`)
```
保存旧会话 sessionMessages + sessionInputs（含 files 数组）
→ 切换 selectedSessionId
→ 恢复新会话 inputText + selectedFiles
→ 从 /history 或 localStorage 加载消息
→ 滚底 + 关闭移动端侧栏
```

## 移动端适配 (`useKeyboard.js`)

### visualViewport 键盘同步
监听 `resize`、`scroll`、`window.resize` 三种事件，覆盖键盘弹出、滚动偏移、横竖屏切换。

| 元素 | 动态调整 |
|------|----------|
| `#input-wrapper` | `bottom` = 键盘高度 |
| `#mobile-topbar` | `top` = 视口偏移 |
| `#messages` | `top` + `padding-bottom` |
| `#sidebar` | `top` |
| `.mobile-overlay` | `top` |

桌面端（>768px）清空所有动态内联样式。

### 响应用户手动上滚
`#messages` `scroll` 事件检测：距底部 > 60px → `userHasScrolledUp=true`，暂停自动滚动。回到底部时自动恢复。

## MD3 主题系统 (`tokens.css` + `useTheme.js`)

双主题通过 `:root`/`:root.light-mode` CSS variables 切换：

- **颜色**：`--md-bg`, `--md-surface`, `--md-primary`, `--md-outline` 等 20+ token
- **Elevation**：`--md-elevation-1/2/3` 三层阴影
- **Shape**：`--md-shape-extra-small` ~ `--md-shape-full`
- **State layer**：`--md-state-hover/press/focus` opacity 值

切换逻辑：`document.documentElement.classList.toggle('light-mode')` + localStorage。

## 关键设计经验

1. **`addMessage` 必须返回 reactive proxy** — `return this.messages[this.messages.length-1]`，不能返回原始 object。否则 SSE 流式期间修改不触发 Vue 重渲染。

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

### 生产
```bash
npm run build      # → dist/
```

Gateway `server.py` 通过 `app.router.add_static('/spa/', str(spa_dist), show_index=False)` 服务。`dist/` 目录需在 Nuitka/PyInstaller 构建前生成。

### CI
Nuitka/PyInstaller 工作流中先执行 `npm ci && npm run build`，然后通过 `--include-data-dir`/`--add-data` 将 `dist/` 打包进二进制。
