# psi-agent Gateway Web Console SPA 设计文档

**日期**: 2026-06-26
**状态**: draft

## 1. 概述

Gateway Web Console 是 psi-agent Gateway 的 Web 管理界面，提供 AI 后端管理、对话会话管理、流式聊天交互的完整功能。它是一个 Vue 3 单页应用（SPA），由 Gateway 后端以静态文件方式服务。

用户通过浏览器访问 Gateway 地址即可直接使用，无需安装任何客户端。

## 2. 技术架构

### 2.1 技术栈

| 领域 | 技术 | 说明 |
|------|------|------|
| 框架 | Vue 3.4 Composition API | `<script setup>` SFC |
| 构建 | Vite 6 | 开发 HMR + 生产打包 |
| Markdown | marked | 消息内容渲染 |
| LaTeX | KaTeX | 数学公式渲染 |
| 图标 | Material Symbols | npm 包，非 Google Fonts |
| 状态 | Vue `reactive()` | 单 store 模式 |
| 包管理 | npm | Node.js >= 20 |

**不引入** Vue Router（单页无路由）、Pinia（store 足够简单）、TypeScript（保持轻量）。

### 2.2 与 Gateway 的通信

SPA 运行在浏览器中，通过 HTTP REST API 与 Gateway 后端通信。所有 API 端点均为同一 Gateway 进程提供，无跨域问题。

```
浏览器 SPA ── GET/POST/DELETE ──► Gateway HTTP Server
                                      │
                                      ├── AIManager
                                      ├── SessionManager
                                      ├── TitleManager
                                      ├── ChatManager
                                      ├── HistoryManager
                                      └── WorkspaceManager
```

### 2.3 API 端点一览

| Method | Endpoint | 用途 |
|--------|----------|------|
| GET | `/ais` | 获取所有 AI 后端 |
| POST | `/ais` | 创建新 AI 后端 |
| DELETE | `/ais/{id}` | 删除 AI 后端 |
| GET | `/sessions` | 获取所有会话 |
| POST | `/sessions` | 创建新会话 |
| DELETE | `/sessions/{id}` | 删除会话 |
| POST | `/sessions/{id}/chat` | 发送消息（SSE 流式返回） |
| GET | `/sessions/{id}/history` | 获取会话历史 |
| GET | `/titles` | 获取所有会话标题 |
| POST | `/titles` | 设置会话标题 |
| POST | `/titles/generate` | AI 自动生成标题 |
| GET | `/workspace/browse?path=` | 浏览服务器目录 |

## 3. 页面布局

页面采用桌面/移动端响应式双布局。

### 3.1 桌面端布局（>768px）

```
┌──────────────────────────────────────────────────────────────┐
│ ┌──────────┐ ┌─────────────────────────────────────────────┐│
│ │          │ │  ☰ (折叠侧栏)              🌓 (主题切换)      ││
│ │          │ │                                              ││
│ │ 会话列表  │ │              消息区域 (ChatArea)              ││
│ │          │ │  ┌──────────────────────────────────────┐   ││
│ │ + 新建   │ │  │ AI 消息气泡 (左对齐, 灰底)          │   ││
│ │          │ │  │ 支持 Markdown / LaTeX / 文件链接    │   ││
│ │ 会话 1  ✕│ │  └──────────────────────────────────────┘   ││
│ │ 会话 2  ✕│ │  ┌─────────────────────────┐                 ││
│ │          │ │  │     用户消息气泡 (右对齐, 蓝底)           ││
│ │          │ │  └─────────────────────────┘                 ││
│ │          │ │                                              ││
│ │          │ │  ┌───────────────────────────────────────┐  ││
│ │          │ │  │ 📎  [输入框................] 🤖模型 ▼ ▶│  ││
│ │          │ │  └───────────────────────────────────────┘  ││
│ └──────────┘ └─────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────┘
```

**关键布局规则**：
- 左侧会话栏：宽度 280px，可折叠（点击 ☰ 收起为 0px）
- 右侧聊天区：占满剩余空间
- 消息区：flex-1 自动撑满，溢出滚动
- 输入栏：固定在底部

### 3.2 移动端布局（≤768px）

```
┌─────────────────────────┐
│ ☰   会话标题   🌓       │ ← 固定顶部导航栏 (52px)
├─────────────────────────┤
│                         │
│      消息区域            │
│  (全宽, padding避开顶栏) │
│                         │
├─────────────────────────┤
│ 📎 [输入框] 🤖 ▶       │ ← 固定底部输入栏 (跟随键盘)
└─────────────────────────┘
```

**移动端特有规则**：
- 会话列表变为左侧滑出抽屉（`translateX` 动画）
- 桌面端的悬浮按钮（☰ 折叠、🌓 主题）隐藏，由顶部导航栏替代
- 输入栏通过 `visualViewport` API 监听键盘弹起，动态调整 `bottom` 值
- 消息气泡放宽至 90% 宽度
- 模型 Chip 缩小至 100px

### 3.3 状态/占位区域

| 状态 | 显示内容 |
|------|----------|
| 页面加载中 | 全屏 spinner + "Initializing System Environment..." |
| 无会话选中 | 消息区显示 "选择一个会话开始聊天" |
| 正在流式生成 | AI 气泡显示三个脉冲圆点 (Thinking Bubble) |
| 无 AI 可用 | 新会话按钮触发 AI 创建弹窗 |
| 消息为空 | 选中会话但无历史时，消息区保持空 |

## 4. 组件设计

### 4.1 App.vue（根组件）

三栏布局容器，管理顶层 UI 状态：

```
App.vue
├── 加载遮罩 (v-if loadingEnv)
├── 移动端遮罩层 (会话抽屉打开时)
├── Sidebar.vue
├── ChatArea.vue
│   ├── 移动端顶部栏 (仅 ≤768px)
│   ├── 桌面端侧栏切换按钮 + 主题切换按钮 (仅 >768px)
│   ├── 消息列表
│   ├── 输入栏 (InputBar)
│   └── 模型管理面板 (ModelPanel, 由 InputBar 内嵌)
├── 各种弹窗:
│   ├── AiDialog.vue
│   ├── SessDialog.vue
│   ├── ConfirmDialog.vue
└── Snackbar.vue
```

### 4.2 Sidebar.vue — 会话列表

**功能**：
- 列出所有会话，显示 AI 生成的标题（或 "新会话" 占位）
- 当前选中项高亮
- 双击会话名 → 切换为输入框，可编辑标题
- 每个会话项 hover 出现删除按钮（触摸设备始终显示）
- 顶部"新建"按钮 → 打开创建会话弹窗

**交互细节**：
- 桌面端：固定左栏，可通过 ☰ 按钮折叠
- 移动端：从左侧滑入的抽屉，点击遮罩层关闭
- 选中会话后移动端自动关闭抽屉

### 4.3 ChatArea.vue — 消息区域

**功能**：
- 渲染消息列表，自动滚动到底部
- 流式输出时，用户手动上滚则暂停自动滚动（`userHasScrolledUp`），滚回底部恢复
- 显示空状态提示

### 4.4 MessageBubble.vue — 单条消息

**Props**: `{ id, role, text, html, files, streaming }`

**功能**：
- 用户消息（role='user'）：蓝底右对齐气泡，显示 `You` 标签
- AI 消息（role='assistant'）：灰底左对齐气泡，显示 `Assistant` 标签
- 流式状态 + 无文本 → 显示 ThreeDots（脉冲动画）
- 有文本 → `v-html` 渲染 markdown/LaTeX
- 文件附件：显示在气泡下方，图标 + 可下载链接（Blob URL）
- 复制按钮：hover/触摸显示，点击复制纯文本，图标短暂变 ✓

**Markdown 渲染**：`renderMd(text)` 函数，先提取 `$$...$$` 和 `$...$` LaTeX 公式用 `\x00` 占位，`marked.parse()` 转 HTML，再用 `katex.renderToString()` 替换占位符。

**文件支持**：
- 接收的文件：后端返回 base64 → Blob URL → 可下载链接
- 发送的文件：前端 `FileReader` 转 base64 存储 → 同样显示为可下载链接

### 4.5 InputBar.vue — 输入栏

**功能**：
- 文件上传按钮（📎）+ `<input type="file">`
- 选择文件后，在上方显示文件预览 chip（文件名 + ✕ 移除）
- `<textarea>` 输入框，Enter 发送，Shift+Enter 换行
- 模型选择 Chip（右侧）：显示当前 AI 模型名，点击展开模型管理浮层
- 发送按钮（圆形蓝色，流式中禁用）

### 4.6 ModelPanel.vue — 模型管理浮层

**功能**：
- 从模型 Chip 向上弹出（`position: absolute; bottom: calc(100% + 8px)`）
- 显示所有已链接的 AI，当前选中项带 ✓
- 每个 AI 显示模型名 + provider
- hover 出现删除按钮（触摸设备始终显示）
- 顶部"链接新模型"按钮 → 打开 AiDialog
- 点击遮罩层关闭

### 4.7 AiDialog.vue — 链接大模型弹窗

**表单字段**：
- Provider 下拉（DeepSeek / OpenAI / Anthropic / Gemini），选择后自动填充 Base URL
- Base URL 输入框（填写后触发模型列表获取）
- API Key 密码输入框
- Model 输入框 + `<datalist>` 自动补全（从 provider `/models` API 获取）

**交互**：
- 填写 API Key + Base URL 后自动 `fetch /models` 获取可用模型列表
- 首次加载时若无 AI，弹窗不可关闭（`handleAiCancel` 拦截）
- 提交失败 → Snackbar 显示错误

### 4.8 SessDialog.vue — 创建会话弹窗

**字段**：
- Workspace 路径输入框 + 浏览按钮
- FileBrowser 内嵌（点浏览后展开）

**FileBrowser.vue**：
- 显示 "使用当前目录" 快捷选项
- ".." 返回上级目录
- 子目录列表（每项带"选择"按钮）
- 点文件夹图标 toggle 显示/隐藏
- 选中的路径自动填入 workspace 输入框

### 4.9 ConfirmDialog.vue — 确认弹窗

通用二次确认弹窗，支持两种操作类型：
- `actionType='session'`：删除会话确认
- `actionType='ai'`：删除模型确认

### 4.10 Snackbar.vue — 提示条

MD3 风格底部居中提示：
- 从下方滑入（`translateY` 动画 + `opacity`）
- 4 秒自动消失
- "知道了" 按钮可提前关闭
- 替代浏览器原生 `alert()`

## 5. 状态管理

使用单一 `reactive()` 对象作为全局 store，通过 Vue 3 `provide/inject` 在组件树中传递。不引入 Pinia（状态结构简单，无复杂派生/持久化需求）。

### 5.1 Store 结构

```
store
├── ais[]                    # AI 列表 (id, provider, model, socket)
├── sessions[]               # 会话列表 (id, ai_id, workspace, channel_socket)
├── selectedAiId             # 当前选中的 AI
├── selectedSessionId        # 当前选中的会话
├── sessionTitles{}          # sessionId → AI 生成的标题
├── messages[]               # 当前会话消息列表 ({id, role, text, html, files, _copied, _url})
├── sessionMessages{}        # sessionId → 该 session 消息列表（切换时保留状态）
├── sessionInputs{}          # sessionId → 输入栏文本状态
├── inputText                # 输入框文本 (v-model)
├── streaming                # 是否正在流式接收
├── selectedFile             # 已选中的文件 (File | null)
│
├── loadingEnv               # 初次加载遮罩
├── dlgAI                    # AI 创建弹窗开关
├── dlgSess                  # 会话创建弹窗开关
├── modelPanelOpen           # 模型管理浮层开关
├── isSidebarCollapsed       # 桌面端侧栏折叠
├── isMobileSidebarOpen      # 移动端抽屉展开
├── isLightMode              # 主题 (默认亮色)
├── userHasScrolledUp        # 用户是否手动上滚
│
├── aiForm {provider, base_url, api_key, model}  # AI 创建表单
├── sessForm {workspace}     # Session 创建表单
├── editingSessionId         # 正在编辑标题的 session ID
├── editingWorkspaceText     # 标题编辑中的文本
│
├── snackbar {show, message} # Snackbar 状态
├── dlgConfirm {show, message, actionArgs, actionType}  # 确认弹窗
├── fetchedModels[]          # 从 provider 获取的模型列表
├── loadingModels            # 模型获取中
└── browser {path, parent, entries}  # 目录浏览状态
```

### 5.2 localStorage 持久化

| Key | 内容 | 读 | 写 |
|-----|------|----|-----|
| `gw-active-ids` | `{aiId, sessId}` | mounted 恢复选中 | selectAI/selectSession |
| `gw-hist-<id>` | `[{role, text, files}]` | 文件 blob 数据 merge | sendMessage 完成后 |
| `gw-sidebar-state` | `'expanded'/'collapsed'` | mounted | toggleSidebar |
| `gw-theme` | `'light'/'dark'` | mounted | toggleTheme |

**不在 localStorage 的数据**：
- AI/Session 列表 → 每次从服务端 GET 获取（服务端是唯一数据源）
- 对话文本历史 → 优先从服务端 GET `/sessions/{id}/history`，localStorage 仅作文件 blob 缓存的补充
- 会话标题 → 服务端 `/titles` 维护，不在 localStorage 存储

## 6. 用户操作流程

### 6.1 首次使用

```
打开页面
  → 加载 loading 遮罩
  → GET /ais, GET /sessions, GET /titles
  → 无 AI → 强制弹出 AiDialog
  → 填写 provider/model/api_key/base_url → POST /ais
  → 创建成功 → 自动弹出 SessDialog
  → 填写 workspace (可选) → POST /sessions
  → 进入聊天界面
```

### 6.2 日常聊天

```
输入消息 (可选选文件) → 点击发送 / Enter
  → 用户气泡立即显示
  → AI 气泡显示 Thinking 脉冲动画
  → POST /sessions/{id}/chat (FormData)
  → SSE 流式读取
    → 每收到 text chunk：追加到 AI 气泡，重新渲染 markdown
    → 每收到 blob chunk：添加文件附件到 AI 消息
  → 完成后保存历史到 localStorage
  → 若该会话无标题 → 调用 /titles/generate
```

### 6.3 切换 AI

```
点击模型 Chip → ModelPanel 弹出 → 选择另一个 AI
  → DELETE /sessions/{oldId} (删除旧链接)
  → POST /sessions {id:oldId, ai_id:newId, workspace:原workspace}
  → 会话 ID 不变 (保持历史连续性)
  → 消息清空 (新 AI 不继承旧对话)
```

### 6.4 会话管理

```
双击会话名 → 输入新名称 → Enter 确认 / Esc 取消
  → DELETE + POST /sessions (同 ID，原 workspace)
  → POST /titles {id, title}
  → 列表刷新显示新标题

删除会话 → ConfirmDialog → DELETE /sessions/{id}
  → 清理 localStorage 历史
```

### 6.5 模型管理

```
点模型 Chip → ModelPanel → 点删除图标 → ConfirmDialog → DELETE /ais/{id}
  → 若删除的是当前选中 AI → 清空选中状态

点"链接新模型" → AiDialog → 填写信息 → POST /ais
  → 新建的 AI 自动选中
```

## 7. 视觉设计

### 7.1 Material Design 3 Token 系统

所有颜色、形状、阴影通过 CSS 自定义属性定义，支持亮色/暗色双主题。

**颜色 Token**：
```
--md-bg                 # 页面背景
--md-surface            # 侧边栏/卡片背景
--md-surface-variant    # 列表项 hover
--md-surface-container  # 输入栏背景
--md-surface-container-high  # 弹窗/对话框背景
--md-primary            # 主色 (蓝色系)
--md-on-primary         # 主色上文字
--md-primary-container  # 用户气泡/按钮背景
--md-outline            # 强边框
--md-outline-variant    # 弱边框 (半透明)
--md-text-primary       # 主要文字
--md-text-secondary     # 次要文字
--md-text-error         # 错误文字
```

**暗色模式特殊处理**：`--md-outline-variant: rgba(255,255,255,0.08)` — 用半透明白色替代实色，边框融入背景更自然。

**亮色模式特殊处理**：`--md-outline-variant: #c4c6d0` — 实色浅灰边框。

**Shape Token**：

| Token | 值 | 用途 |
|-------|-----|------|
| `--md-shape-extra-small` | 4px | 输入框 |
| `--md-shape-small` | 8px | 列表项 |
| `--md-shape-medium` | 12px | FAB, 消息气泡 |
| `--md-shape-large` | 16px | 卡片, 浮层 |
| `--md-shape-extra-large` | 28px | 弹窗, 对话框 |
| `--md-shape-full` | 9999px | 圆形按钮, chip |

**Elevation Token** (三层)：
- `elevation-1`：按钮、气泡 (轻微阴影)
- `elevation-2`：浮层、hover 按钮 (中等阴影)
- `elevation-3`：弹窗、对话框 (深度阴影)

亮色/暗色各自一套 elevation 值（暗色更深）。

### 7.2 组件样式

**按钮体系**（遵循 MD3）：
- Filled Button (`.md-filled-btn`)：蓝色实心 + elevation-1，用于主要操作（发送、确认）
- Outlined Button (`.md-outlined-btn`)：透明 + 边框，用于次要操作（取消、浏览）
- Tonal Button (`.md-tonal-btn`)：secondary-container 背景，用于浮层内选择按钮
- Icon Button (`.md-icon-btn`)：纯图标圆形按钮，用于复制、主题切换
- FAB (`.md-fab`)：小圆形 + elevation-2，用于输入栏发送按钮

**消息气泡**：
- 用户消息：`--md-primary-container` 背景，右下角 4px 圆角，其余 16px
- AI 消息：`--md-surface-container-high` 背景 + `--md-outline-variant` 边框，左下角 4px 圆角
- 文件 blob：气泡下方独立卡片，边框 + 图标 + 链接

**交互反馈（State Layer）**：
- hover：`rgba(128,128,128, 0.08)` 背景叠加（iOS/暗色通用中性色）
- 对于触摸设备 (`@media (hover: none)`)，删除/复制按钮始终可见

**Snackbar**：
- 固定底部居中，`translateY` + `opacity` 动画
- 左侧消息文字 + 右侧"知道了"按钮
- 4 秒自动关闭

**弹窗**：
- 居中 + `backdrop-filter: blur(2px)` 遮罩
- `elevation-3` 阴影
- 点击遮罩层关闭

### 7.3 响应式断点

| 断点 | 布局变化 |
|------|----------|
| >768px | 桌面端：固定侧栏 + 悬浮按钮 |
| ≤768px | 移动端：顶部导航栏 + 侧栏抽屉 + 固定输入栏 + `visualViewport` 键盘适配 |
| ≤400px | 消息气泡放宽至 95%，模型 Chip 缩小至 80px |

### 7.4 移动端键盘适配

通过 `window.visualViewport` API 监听键盘弹出/收起：

```javascript
visualViewport.addEventListener('resize', syncInputPosition)
visualViewport.addEventListener('scroll', syncInputPosition)
window.addEventListener('resize', syncInputPosition)  // 横竖屏切换
```

键盘弹起时：
- `input-wrapper` 的 `bottom` 设为键盘高度
- `topbar` 的 `top` 设为视口偏移
- `messages` 的 `top` 和 `padding-bottom` 动态调整
- 自动滚到底部

## 8. 项目文件结构

```
src/psi_agent/gateway/spa/
├── package.json
├── vite.config.js
├── index.html                 # Vite 入口
├── src/
│   ├── App.vue
│   ├── main.js                # createApp + mount
│   ├── store.js               # reactive store
│   ├── utils.js               # renderMd, htmlEscape, mimeType, localStorage
│   ├── api.js                 # fetch 封装
│   ├── providers.js           # PROVIDERS 配置
│   ├── components/
│   │   ├── Sidebar.vue
│   │   ├── ChatArea.vue
│   │   ├── MessageBubble.vue
│   │   ├── ThinkingBubble.vue
│   │   ├── InputBar.vue
│   │   ├── ModelPanel.vue
│   │   ├── AiDialog.vue
│   │   ├── SessDialog.vue
│   │   ├── FileBrowser.vue
│   │   ├── ConfirmDialog.vue
│   │   └── Snackbar.vue
│   ├── composables/
│   │   ├── useSSE.js
│   │   ├── useKeyboard.js
│   │   └── useTheme.js
│   └── styles/
│       ├── tokens.css
│       ├── components.css
│       └── layout.css
└── dist/                      # vite build 输出 (gitignore)
```

## 9. 构建与集成

### 9.1 开发

```bash
cd src/psi_agent/gateway/spa
npm install
npm run dev     # Vite dev server on localhost:5173
```

Gateway 无需感知 Vite dev server（手动开两个终端，Gateway 运行在 :8080，Vite dev 在 :5173）。

### 9.2 生产

```bash
npm run build   # 输出到 dist/
```

Gateway 添加静态文件服务：

```python
# server.py
app.router.add_static('/spa/', 'spa/dist/')
```

`GET /` 重定向到 `/spa/`。

### 9.3 CI / Nuitka

Nuitka 打包时包含 `dist/` 目录：

```yaml
--include-data-dir=src/psi_agent/gateway/spa/dist=spa/dist
```

CI 新增 `npm ci && npm run build` 步骤。

## 10. 迁移策略

从当前 1660 行 `console.html` 迁移到 SPA 项目，分 5 阶段：

1. **项目骨架**：`npm create vue@latest`，安装依赖，配置 Vite
2. **叶子组件**：先迁移无依赖的小组件（ThinkingBubble、Snackbar、ConfirmDialog、FileBrowser）
3. **容器组件**：MessageBubble → ChatArea → InputBar → ModelPanel → Sidebar → App
4. **状态和 composables**：提取 store.js、api.js、composables/
5. **Gateway 适配和清理**：加 static serving，更新 Nuitka CI，删除旧 console.html

每个阶段保持功能 1:1 等价。
