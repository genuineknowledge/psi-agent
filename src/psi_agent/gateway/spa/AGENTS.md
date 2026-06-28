# SPA 前端设计文档

## 概述

Web 控制台是一个**零 CDN 依赖**的 Vue 3 单页应用（SPA），由 Vite 构建为静态文件，通过 Gateway 的 `/spa/` 路由服务。

## 技术栈

| 领域 | 技术 | 版本 | 选择理由 |
|------|------|------|----------|
| 框架 | Vue 3 Composition API `<script setup>` | `^3.4.21` | SFC 组织清晰，`reactive()` 替代 Pinia |
| 构建 | Vite | `^6.0.0` | 快 HMR + 静态打包 |
| Markdown | marked | `^11.1.1` | 体积最小（22KB），AI 回复非用户输入无需安全 sanitize |
| LaTeX | KaTeX | `^0.16.9` | 同步 `renderToString()` 比 MathJax 快 5-10x，SSE 逐帧重渲染关键 |
| 图标 | Material Symbols | `^0.14.0` | Google MD3 官方当前图标集，CSS `font-variation-settings` 支持 weight/fill 可调 |
| Vue 插件 | `@vitejs/plugin-vue` | `^5.0.0` | Vite SFC 编译 |

**未引入**：
- **Vue Router** — 单页无路由
- **Pinia** — `reactive()` 单一 store 足够
- **TypeScript** — 保持轻量
- **CSS 预处理器** — 手写 CSS variables（MD3 token 系统）

## 目录结构

```
spa/
├── package.json                     # 依赖声明
├── vite.config.js                   # base=/spa/, @ alias
├── index.html                       # #app mount + favicon + <title>haitun agent</title>
├── public/
│   └── dolphin.jpg                  # favicon 海豚图标（Vite 复制到 dist/ 根，引用 /dolphin.jpg）
├── src/
│   ├── main.js                      # createApp + import CSS + v-focus directive
│   ├── App.vue                      # 根组件：layout + 弹窗 + 遮罩 + 业务逻辑
│   ├── store.js                     # reactive() 单一 store，provide/inject
│   ├── utils.js                     # renderMd, htmlEscape, mimeType, localStorage
│   ├── api.js                       # fetch 封装, streamChat, parseSSELine
│   ├── providers.js                 # 预置 provider 配置 (deepseek/openai/anthropic/gemini)
│   ├── components/
│   │   ├── Sidebar.vue              # 会话列表：新建/双击改名/删除
│   │   ├── ChatArea.vue             # 消息列表容器 + 自动滚动 + 空状态
│   │   ├── MessageBubble.vue        # 单条消息：Markdown + 复制 + 文件附件
│   │   ├── ThinkingBubble.vue       # 等待首 token 的三个脉冲圆点
│   │   ├── InputBar.vue             # textarea + 文件上传 + 发送
│   │   ├── ModelPanel.vue           # 模型管理浮层（自定义下拉替代原生 datalist）
│   │   ├── AiDialog.vue             # 链接大模型弹窗
│   │   ├── SessDialog.vue           # 创建会话弹窗 + FileBrowser
│   │   ├── FileBrowser.vue          # 目录浏览
│   │   ├── ConfirmDialog.vue        # 通用确认弹窗
│   │   └── Snackbar.vue             # MD3 toast 提示
│   ├── composables/
│   │   ├── useSSE.js                # ReadableStream SSE 逐行解析 async generator
│   │   ├── useKeyboard.js           # visualViewport 键盘适配 + 手动上滚检测
│   │   └── useTheme.js              # 暗色/亮色切换 + localStorage + <html> class
│   └── styles/
│       ├── tokens.css               # MD3 颜色/elevation/shape token（双主题）
│       ├── components.css           # MD3 组件基类（button, dialog, field, spinner）
│       └── layout.css               # 页面布局 + 响应式 + 消息气泡 + 动画
└── dist/                            # `vite build` 输出 (gitignore)
```

## 构建配置

`vite.config.js`:
- `base: '/spa/'` — 匹配 Gateway 的 `/spa/` 静态路由
- `@` alias → `/src`
- 输出目录 `dist/`，assets 放 `dist/assets/`

`index.html`:
- `<title>haitun agent</title>` — 页面标题
- `<link rel="icon" href="/dolphin.jpg">` — favicon 来自 `public/dolphin.jpg`，Vite 构建时按 `base` 重写为 `/spa/dolphin.jpg`
- `<meta viewport interactive-widget=resizes-visual>` — iOS 键盘弹出时缩小 visual viewport 而非 overlay
- 初始 `#app` 内含加载 spinner，Vue mount 后被替换

## 根组件 `App.vue` 架构

```
#root-layout
├── .page-loader          (v-if loadingEnv — 全屏 spinner)
├── .mobile-overlay       (v-if 移动端抽屉打开 — 半透明遮罩)
├── #sidebar              (collapsed / mobile-open)
│   └── .col > .col-header + item (v-for sessions)
├── #chat
│   ├── #mobile-topbar    (≤768px: 汉堡菜单 + 标题 + 主题切换)
│   ├── .sidebar-toggle-btn / .theme-toggle-btn  (>768px 悬浮按钮)
│   ├── ChatArea          (#messages → MessageBubble v-for)
│   └── #input-wrapper    (文件预览 + textarea + ModelPanel + 发送按钮)
├── AiDialog      (@create, @fetchModels)
├── SessDialog    (@create, @browse)
├── ConfirmDialog (@confirm)
└── Snackbar
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
| `sessionInputs` | `object` | sessionId → {text, file}（切换时保留输入框） |
| `streaming` | `bool` | 是否正在 SSE 接收中 |
| `selectedFile` | `File/null` | 已选中的上传文件 |
| `inputText` | `string` | textarea v-model |
| `userHasScrolledUp` | `bool` | 手动上滚时暂停自动滚动 |
| `isLightMode` | `bool` | 主题（默认亮色） |
| `isSidebarCollapsed`, `isMobileSidebarOpen` | `bool` | 侧栏状态 |
| `modelPanelOpen` | `bool` | 模型浮层开关 |
| `dlgAI`, `dlgSess` | `bool` | 弹窗开关 |
| `snackbar`, `dlgConfirm` | `object` | 提示/确认弹窗状态 |
| `aiForm`, `sessForm` | `object` | 弹窗表单数据 |
| `browser` | `object` | 目录浏览 {path, parent, entries} |
| `fetchedModels`, `loadingModels` | `array/bool` | 从 provider API 获取的模型列表 |
| `editingSessionId`, `editingWorkspaceText` | `string` | 会话标题编辑状态 |

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

**注意**：这是一个 `async function*` generator，App.vue 中通过 `for await (const chunk of readSSE(reader))` 消费。

## localhost 持久化策略

**原则**：服务端是唯一数据源（AI/Session 列表从远端 GET），localStorage 仅存 UI 状态和对话缓存。

| Key | 内容 | 来源 |
|-----|------|------|
| `gw-active-ids` | `{aiId, sessId}` | 选中的 AI/Session ID |
| `gw-hist-<id>` | `[{role, text, files}]` | 对话历史（文件 blob 合并服务端文本） |
| `gw-sidebar-state` | `'expanded'/'collapsed'` | 侧栏折叠状态 |
| `gw-theme` | `'light'/'dark'` | 主题偏好 |

Session 标题由服务端 `/titles` 维护，**不在** localStorage 存储。

## App.vue 核心业务流程

### 启动流程 (`onMounted`)
```
1. GET /titles → store.sessionTitles
2. GET /ais + GET /sessions → store.ais/sessions
3. 无 AI → 弹窗 AiDialog（不可关闭，至少需要 1 个）
4. loadActiveState → 恢复选中 AI/Session ID
5. selectSession → 从 /history + localStorage 加载消息
6. loadingEnv = false
```

### 发送消息 (`sendMessage`)
```
1. 提取 inputText + selectedFile
2. File → FileReader.readAsDataURL → base64
3. 用户消息立即显示 (addMessage + htmlEscape)
4. AI 消息气泡出现 (addMessage, streaming=true)
5. FormData + fetch POST /sessions/{id}/chat（multipart）
6. for await (readSSE(reader)):
   - text chunk → asst.text += chunk.text → renderMd → 更新 v-html
   - blob chunk → asst.files.push({name, data})
   - scrollChatAreaIfLocked → 自动滚底（unless userHasScrolledUp）
7. streaming=false
8. saveHistory → localStorage
9. 无标题 → generateTitle → POST /titles/generate
```

### 切换 AI (`selectAI`)
```
DELETE /sessions/{oldId} → POST /sessions {id:oldId, ai_id:newId, ...}
→ 同 ID 新 AI，消息清空
```

### 编辑标题 (`saveSessionWorkspace`)
```
DELETE + POST /sessions (同 id, 原 workspace) → POST /titles
→ sidebar 刷新
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
