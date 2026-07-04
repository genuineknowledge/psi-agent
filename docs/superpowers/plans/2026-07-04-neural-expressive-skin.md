# Neural Expressive 皮肤重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏现有功能的前提下，把 "Neural Expressive" 视觉语言套用到 gateway SPA，并按目标交互重排布局（侧栏导航化 + 动态胶囊）。

**Architecture:** 路径 A 令牌层复用——改 `styles/tokens.css` 的 `--md-*` 值 + 新增 `--g-*` 渐变/四色变量，令所有组件自动跟随双主题；再逐组件调整 DOM 结构与 scoped 样式。业务逻辑（store / composable / api）零改动。

**Tech Stack:** Vue 3 `<script setup>` + Pinia + Vite；Material Symbols 本地图标；纯 CSS 动效（无新依赖）。

## Global Constraints

- 禁止硬编码色值，颜色/圆角/阴影/间距一律用 `--md-*` 或新增 `--g-*` CSS variable（AGENTS.md 第 10 条）。
- 组件专属样式（含移动端 `@media`）写在该组件 `<style scoped>`；全局 token 只在 `styles/tokens.css`。
- 无外链：字体/图标/CSS 走本地包，禁止 `<script src=https://...>` / `<link href=https://...>`。
- 不引入 Vue Router / TypeScript / CSS 预处理器 / 动效库 / 任何新 npm 依赖。
- 不改 store 数据模型、不改 REST/api 调用、不重写 composable 业务逻辑、不改后端。
- 不加后端不存在的导航项（Library / Gems / Notebooks）。
- 断点统一 `768px`；移动端键盘适配集中在 `useKeyboard.js`，不另写第二套。
- 分支：`feature/neural-expressive-skin`（已建）。所有产出物、分支名、代码注释、UI 文案中**不得出现参考产品的品牌名**（只用 "Neural Expressive" / "Dolphin" / 中性表述）。
- 每个 Task 的验证 = `npm run build` 通过 + dev 目测；无自动化测试框架，不新建。
- 工作目录：`src/psi_agent/gateway/spa/`。

---

### Task 1: 令牌层——注入 Neural Expressive 调色板

**Files:**
- Modify: `src/psi_agent/gateway/spa/src/styles/tokens.css`

**Interfaces:**
- Produces: 后续所有 Task 依赖的 `--md-*`（改值后）+ 新增 `--g-grad-hello` / `--g-spark` / `--g-pill-radius` 变量。变量名与值必须与本 Task 完全一致。

- [ ] **Step 1: 改暗色主题块 `:root`**

在 `src/styles/tokens.css` 的 `:root { ... }`（暗色）中替换以下值：

```css
:root {
  --md-bg: #1B1C1D;
  --md-surface: #282A2C;
  --md-surface-variant: #2D2F31;
  --md-surface-container: #1E1F20;
  --md-surface-container-low: #1B1C1D;
  --md-surface-container-high: #333537;
  --md-primary: #A8C7FA;
  --md-on-primary: #062E6F;
  --md-primary-container: #1C4487;
  --md-on-primary-container: #D3E3FD;
  --md-secondary-container: #333537;
  --md-on-secondary-container: #E3E3E3;
  --md-outline: #444746;
  --md-outline-variant: #3C4043;
  --md-text-primary: #E3E3E3;
  --md-text-secondary: #C4C7C5;
  --md-text-error: #ffb4ab;
  --md-error-container: #93000a;
  /* elevation / state 保持原值不动 */
  /* shape：整体调圆 */
  --md-shape-extra-small: 4px;
  --md-shape-small: 8px;
  --md-shape-medium: 12px;
  --md-shape-large: 20px;
  --md-shape-extra-large: 28px;
  --md-shape-full: 9999px;
  --md-state-hover: .08;
  --md-state-press: .12;
  --md-state-focus: .12;
  /* Neural Expressive 新增 */
  --g-grad-hello: linear-gradient(90deg,#4285F4,#9B72CB,#D96570);
  --g-spark: conic-gradient(from 0deg,#4285F4,#9B72CB,#D96570,#FBBC04,#4285F4);
  --g-pill-radius: 28px;
}
```

保留 `:root` 中原有的 `--md-elevation-*` 三行不动。

- [ ] **Step 2: 改亮色主题块 `:root.light-mode`**

```css
:root.light-mode {
  --md-bg: #FFFFFF;
  --md-surface: #FFFFFF;
  --md-surface-variant: #F0F4F9;
  --md-surface-container: #F0F4F9;
  --md-surface-container-low: #F7F9FC;
  --md-surface-container-high: #E9EEF6;
  --md-primary: #0B57D0;
  --md-on-primary: #FFFFFF;
  --md-primary-container: #D3E3FD;
  --md-on-primary-container: #041E49;
  --md-secondary-container: #E9EEF6;
  --md-on-secondary-container: #1F1F1F;
  --md-outline: #747775;
  --md-outline-variant: #DDE3EA;
  --md-text-primary: #1F1F1F;
  --md-text-secondary: #575B5F;
  --md-text-error: #ba1a1a;
  --md-error-container: #ffdad6;
  /* 保留原有 --md-elevation-* 三行不动 */
}
```

- [ ] **Step 3: 构建验证**

Run: `cd src/psi_agent/gateway/spa && npm run build`
Expected: 构建成功，无 CSS 报错。

- [ ] **Step 4: dev 目测**

Run: `npm run dev`，浏览器打开。确认：整体配色变为白底/品牌蓝，浅色深色切换正常，现有功能视觉无崩坏（按钮、弹窗、侧栏可见）。

- [ ] **Step 5: 提交**

```bash
git add src/psi_agent/gateway/spa/src/styles/tokens.css
git commit -m "style(spa): 令牌层注入 Neural Expressive 调色板 + 新增渐变变量"
```

---

### Task 2: 主区 shell + TopBar（App.vue + layout.css）

**Files:**
- Modify: `src/psi_agent/gateway/spa/src/App.vue`（模板 TopBar 编排 + scoped 样式；脚本逻辑不动）
- Modify: `src/psi_agent/gateway/spa/src/styles/layout.css`

**Interfaces:**
- Consumes: Task 1 的 `--md-*` / `--g-*`。
- Produces: 主区顶栏 `#topbar`（含折叠按钮、主题切换、头像），供视觉基线；`toggleSidebar` / `toggleTheme` 事件绑定不变（复用现有 script）。

- [ ] **Step 1: App.vue 模板——用 TopBar 替换浮动按钮**

在 `App.vue` 的 `<div id="chat">` 内，把现有的 `.sidebar-toggle-btn` 和 `.theme-toggle-btn` 两个浮动 `<button>` 删除，替换为顶栏（保留 `@click="toggleSidebar"` 和 `@click="toggleTheme"` 绑定，函数已存在于 script）：

```html
<div id="topbar">
  <button class="tb-btn" @click="toggleSidebar" :title="isSidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'">
    <span class="material-symbols-outlined">{{ (isSidebarCollapsed && !isMobileSidebarOpen) ? 'menu' : 'left_panel_close' }}</span>
  </button>
  <div class="tb-spacer"></div>
  <button class="tb-btn" @click="toggleTheme" :title="isLightMode ? '切换至暗色模式' : '切换至亮色模式'">
    <span class="material-symbols-outlined">{{ isLightMode ? 'dark_mode' : 'light_mode' }}</span>
  </button>
  <div class="tb-avatar">Q</div>
</div>
```

`#mobile-topbar`（移动端顶栏）保持不动。

- [ ] **Step 2: App.vue scoped 样式——新增 #topbar**

在 `App.vue` `<style scoped>` 加（删除原 `.sidebar-toggle-btn` / `.theme-toggle-btn` 规则若在此文件；若在 layout.css 则 Step 3 处理）：

```css
#topbar {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 16px; flex-shrink: 0;
}
#topbar .tb-spacer { flex: 1; }
#topbar .tb-btn {
  width: 40px; height: 40px; border: none; background: transparent;
  color: var(--md-text-secondary); border-radius: var(--md-shape-full);
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  transition: background 0.2s;
}
#topbar .tb-btn:hover { background: var(--md-surface-container-high); }
#topbar .tb-avatar {
  width: 32px; height: 32px; border-radius: var(--md-shape-full);
  background: var(--md-primary); color: var(--md-on-primary);
  display: flex; align-items: center; justify-content: center;
  font-size: 15px; font-weight: 500;
}
@media (max-width: 768px) { #topbar { display: none; } }
```

- [ ] **Step 3: layout.css——删除旧浮动按钮规则**

在 `src/styles/layout.css` 删除 `.sidebar-toggle-btn { ... }`、`.sidebar-toggle-btn:hover { ... }`、`.theme-toggle-btn { ... }`、`.theme-toggle-btn:hover { ... }` 四条规则（它们被 `#topbar` 取代）。`#chat`、`#mobile-topbar`、`.mobile-overlay`、`.drop-overlay`、`.page-loader`、scrollbar 规则全部保留。

- [ ] **Step 4: 构建验证**

Run: `npm run build`
Expected: 成功。

- [ ] **Step 5: dev 目测**

确认桌面端：主区顶部出现横条（左折叠按钮 / 右主题切换 + 头像 Q）；点折叠能收起侧栏、点主题能切换明暗；移动端（≤768px）仍是原 `#mobile-topbar`，无重复顶栏。

- [ ] **Step 6: 提交**

```bash
git add src/psi_agent/gateway/spa/src/App.vue src/psi_agent/gateway/spa/src/styles/layout.css
git commit -m "feat(spa): 主区 TopBar 替换浮动按钮"
```

---

### Task 3: 侧栏导航化（Sidebar.vue）

**Files:**
- Modify: `src/psi_agent/gateway/spa/src/components/Sidebar.vue`（DOM 重排 + scoped 样式；会话逻辑/事件/store 绑定全部不动）

**Interfaces:**
- Consumes: Task 1 的 `--md-*` / `--g-spark`。
- Produces: 新侧栏结构（Header / New chat 药丸 / 搜索 / "最近" 分区 / 会话列表）。所有现有绑定保留：`$emit('new-session')`、`selectSession`、`toggleSessionPin`、`saveSessionWorkspace`、`confirmDeleteSession`、`visibleSessions`、`sessionSearchText`。

- [ ] **Step 1: 模板——加 Header + 新建药丸 + 最近分区**

把 `Sidebar.vue` `<template>` 里 `.col` 内的 `.col-header`（含"会话"标题和"新建"按钮）替换为：

```html
<div class="sb-header">
  <div class="sb-brand">
    <div class="sb-logo"></div>
    <span class="sb-brand-name">Dolphin</span>
  </div>
</div>
<button class="new-chat" @click="$emit('new-session')">
  <span class="material-symbols-outlined">edit_square</span>
  <span>发起新对话</span>
</button>
```

`.session-search`（搜索框）保持在其下不动。在搜索框之后、`v-for` 会话列表之前插入分区标题：

```html
<div class="recent-label">最近</div>
```

会话列表 `v-for="s in visibleSessions"` 及其内部 `.item`（置顶/名称/删除）**完全保持不动**。

- [ ] **Step 2: scoped 样式——Header / 药丸 / logo / 分区**

在 `Sidebar.vue` `<style scoped>` 顶部新增（删除旧 `.col-header` 规则）：

```css
.sb-header { display: flex; align-items: center; padding: 12px 12px 8px; }
.sb-brand { display: flex; align-items: center; gap: 10px; }
.sb-logo {
  width: 26px; height: 26px; border-radius: var(--md-shape-full);
  background: var(--g-spark);
}
.sb-brand-name { font-size: 20px; font-weight: 500; color: var(--md-text-primary); }
.new-chat {
  display: inline-flex; align-items: center; gap: 12px;
  margin: 4px 12px 8px; padding: 10px 16px;
  background: var(--md-surface-container-high); color: var(--md-text-secondary);
  border: none; border-radius: var(--md-shape-full); cursor: pointer;
  font-size: 14px; align-self: flex-start; transition: background 0.2s;
}
.new-chat:hover { background: var(--md-surface-variant); }
.new-chat .material-symbols-outlined { font-size: 20px; }
.recent-label {
  padding: 12px 16px 6px; font-size: 13px; color: var(--md-text-secondary);
}
```

- [ ] **Step 3: scoped 样式——会话项圆润化**

调整现有 `.item` 规则（保留其结构与选择器名，仅改视觉）为药丸风格：

```css
.item {
  border-radius: var(--md-shape-full);
  margin: 0 8px;
}
.item.selected { background: var(--md-surface-container-high); }
.item:hover { background: var(--md-surface-container-high); }
```

其余 `.item .pin` / `.item .info` / `.item .del` 等规则保持不动。

- [ ] **Step 4: 构建验证**

Run: `npm run build`
Expected: 成功。

- [ ] **Step 5: dev 目测 + 功能回归**

确认：侧栏顶部有四色 logo + "Dolphin"；"发起新对话"药丸可点开建会话弹窗；"最近"标题在搜索框下方；会话列表**搜索、置顶、双击重命名、删除、选中高亮**全部正常工作。

- [ ] **Step 6: 提交**

```bash
git add src/psi_agent/gateway/spa/src/components/Sidebar.vue
git commit -m "feat(spa): 侧栏导航化(Header+新建药丸+最近分区)"
```

---

### Task 4: 输入框胶囊化（InputBar.vue + ModelPanel.vue）

**Files:**
- Modify: `src/psi_agent/gateway/spa/src/components/InputBar.vue`（模板结构 + scoped 样式；发送逻辑不动）
- Modify: `src/psi_agent/gateway/spa/src/components/ModelPanel.vue`（chip 样式微调）

**Interfaces:**
- Consumes: Task 1 的 `--md-*` / `--g-pill-radius`。
- Produces: 药丸形 `#input-area`，ModelPanel 内置右侧。保留：`sendMessage` / `stopMessage` / `onFileSelected` / `autoResizeInput` / `streaming` / `selectedSessionId` / `selectedFiles` / `uploadResetToken`。

- [ ] **Step 1: InputBar 模板——药丸单行布局**

把 `#input-area` 内元素顺序保持为 `label(attach) → textarea → ModelPanel → send`，不增删元素（保留 `@select-ai`/`@delete-ai`/`@new-ai` 透传）。仅调整外层为药丸（样式在 Step 2）。textarea 的 `placeholder` 改为 `问问 Dolphin`。

- [ ] **Step 2: InputBar scoped 样式——药丸化**

替换 `#input-wrapper` / `#input-area` / `textarea` 规则：

```css
#input-wrapper {
  background: transparent; border-top: none;
  display: flex; flex-direction: column;
  padding: 0 16px 16px; align-items: center;
}
#input-area {
  width: 100%; max-width: 820px;
  display: flex; gap: 8px; align-items: center;
  background: var(--md-surface-container);
  border: 1px solid var(--md-outline-variant);
  border-radius: var(--g-pill-radius);
  padding: 8px 10px 8px 14px;
}
#input-area textarea {
  flex: 1; background: transparent; border: none; outline: none;
  color: var(--md-text-primary); font-size: 16px; font-family: inherit;
  resize: none; min-height: 28px; max-height: 160px; padding: 6px 4px;
}
#input-area textarea:focus { border: none; }
#file-preview-bar { width: 100%; max-width: 820px; padding: 0 0 8px; }
```

`label.btn` / `button.send` / `button.send.stop` 规则保留（圆形按钮已符合），仅确认圆角用 `var(--md-shape-full)`。

- [ ] **Step 3: ModelPanel chip 微调**

在 `ModelPanel.vue` 的 `.model-chip` 规则中，把 `border` 改为 `border: none;`、`background` 改为 `transparent`，使其融入胶囊（面板 `.model-panel` 向上弹出规则不动）：

```css
.model-chip {
  display: flex; align-items: center; gap: 6px;
  height: 36px; padding: 0 10px;
  background: transparent; border: none;
  border-radius: var(--md-shape-full); cursor: pointer;
  transition: all 0.2s; max-width: 160px;
  color: var(--md-text-primary); font-size: 13px; font-weight: 500;
  white-space: nowrap; overflow: hidden;
}
.model-chip:hover { background: var(--md-surface-container-high); }
```

- [ ] **Step 4: 构建验证**

Run: `npm run build`
Expected: 成功。

- [ ] **Step 5: dev 目测 + 功能回归**

确认：输入区是药丸单行；附件按钮/输入/模型选择器（点开面板选模型、增删模型）/发送/停止（流式中）全部正常；文件上传 chip 正常；移动端底部固定 + 键盘弹出不遮挡。

- [ ] **Step 6: 提交**

```bash
git add src/psi_agent/gateway/spa/src/components/InputBar.vue src/psi_agent/gateway/spa/src/components/ModelPanel.vue
git commit -m "feat(spa): 输入框胶囊化 + ModelPanel 内置"
```

---

### Task 5: 空状态欢迎屏 + 动态胶囊定位（App.vue + ChatArea.vue）

**Files:**
- Modify: `src/psi_agent/gateway/spa/src/App.vue`（主区结构 + `showWelcome` 派生 + `<transition>`）
- Modify: `src/psi_agent/gateway/spa/src/components/ChatArea.vue`（空状态欢迎屏）

**Interfaces:**
- Consumes: `messages`（来自 `useChatStore`，App.vue 已 storeToRefs 引入 `messages`）；Task 1 的 `--g-grad-hello`。
- Produces: `showWelcome = computed(() => messages.value.length === 0)`；欢迎屏容器与胶囊定位切换。

- [ ] **Step 1: App.vue script——加 showWelcome 派生**

在 `App.vue` `<script setup>` 已有 `const { messages, ... } = storeToRefs(chat)` 之后新增（`computed` 已 import）：

```js
const showWelcome = computed(() => messages.value.length === 0)
```

- [ ] **Step 2: App.vue 模板——欢迎屏容器 + 胶囊定位**

将 `<ChatArea />` 与 `<InputBar .../>` 包进一个条件布局。当 `showWelcome` 为真时，中央显示问候语并让 InputBar 居中；否则 ChatArea 占满、InputBar 沉底。用 class 切换：

```html
<div id="chat-main" :class="{ welcome: showWelcome }">
  <div v-if="showWelcome" class="welcome-hero">
    <div class="welcome-greeting">Qihua，你说，我在听！</div>
  </div>
  <ChatArea v-else />
  <InputBar
    @select-ai="selectAI"
    @delete-ai="confirmDeleteAI"
    @new-ai="openAiDialog"
  />
</div>
```

（替换原来直接并列的 `<ChatArea />` + `<InputBar />`。）

- [ ] **Step 3: App.vue scoped 样式——欢迎屏布局**

```css
#chat-main { flex: 1; display: flex; flex-direction: column; min-height: 0; }
#chat-main.welcome {
  justify-content: center; align-items: center; gap: 40px;
}
#chat-main.welcome .welcome-hero { display: flex; justify-content: center; }
.welcome-greeting {
  font-size: 52px; font-weight: 500; letter-spacing: -1px;
  background: var(--g-grad-hello);
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent; color: transparent;
}
#chat-main.welcome :deep(#input-wrapper) { padding-bottom: 0; }
@media (max-width: 768px) {
  .welcome-greeting { font-size: 34px; }
}
```

- [ ] **Step 4: ChatArea.vue——移除旧空状态文字**

`ChatArea.vue` 现在只在有消息时渲染（App.vue 用 `v-else` 控制）。把模板里的 `<div v-if="messages.length === 0" class="empty">选择一个会话开始聊天</div>` 删除（空状态改由 App.vue 欢迎屏承担）。`#messages` 容器和 `MessageBubble` 循环保持不动。删除 `.empty` scoped 规则。

- [ ] **Step 5: 构建验证**

Run: `npm run build`
Expected: 成功。

- [ ] **Step 6: dev 目测 + 交互回归**

确认：**无消息时**——中央显示渐变问候语，胶囊在其正下方居中；**发一条消息后**——问候语消失，消息流出现，胶囊沉到底部；切换会话时两态正确切换；移动端布局正常。

- [ ] **Step 7: 提交**

```bash
git add src/psi_agent/gateway/spa/src/App.vue src/psi_agent/gateway/spa/src/components/ChatArea.vue
git commit -m "feat(spa): 空状态渐变欢迎屏 + 动态胶囊定位"
```

---

### Task 6: 消息气泡 + spark 头像 + thinking 动效（MessageBubble.vue + ThinkingBubble.vue）

**Files:**
- Modify: `src/psi_agent/gateway/spa/src/components/MessageBubble.vue`（助手 spark 头像 + 去卡片 + scoped 样式；逻辑不动）
- Modify: `src/psi_agent/gateway/spa/src/components/ThinkingBubble.vue`（渐变脉动样式）

**Interfaces:**
- Consumes: Task 1 的 `--g-spark`；现有 `msg.role` / `msg.html` / `streaming`。
- Produces: 助手消息左侧 spark 头像；用户消息右对齐气泡。不改 `copyMessage` / `openPreview` / props。

- [ ] **Step 1: MessageBubble 模板——role 标签换 spark 头像**

把模板里的 `<div class="role">{{ msg.role === 'user' ? 'You' : 'Assistant' }}</div>` 替换为：

```html
<div v-if="msg.role !== 'user'" class="spark">
  <span class="material-symbols-outlined">auto_awesome</span>
</div>
```

（用户消息不显示头像，靠右对齐区分。）`.bubble-wrap`、copy 按钮、文件 blob、FilePreview 全部不动。

- [ ] **Step 2: MessageBubble scoped 样式——去卡片 + 布局**

```css
.msg { display: flex; flex-direction: column; gap: 6px; max-width: 820px; width: 100%; margin: 0 auto 24px; }
.msg.user { align-items: flex-end; }
.msg.assistant { align-items: flex-start; }
.spark {
  width: 30px; height: 30px; border-radius: var(--md-shape-full);
  background: var(--g-spark); display: flex; align-items: center; justify-content: center;
}
.spark .material-symbols-outlined { font-size: 16px; color: #fff; }
.msg.user .bubble {
  background: var(--md-surface-container); border-radius: 22px;
  padding: 12px 18px;
}
.msg.assistant .bubble { background: transparent; padding: 0; }
```

其余 `.bubble-content` / `.copy-btn` / `.blob` / `.stopped-tag` 规则保留；若原 `.bubble` 有卡片边框/阴影，在 assistant 下已置空，user 下用气泡样式覆盖即可。

- [ ] **Step 3: ThinkingBubble 渐变脉动**

在 `ThinkingBubble.vue` scoped 样式，把思考指示器改为品牌渐变脉动（保留其 DOM 与 props）：

```css
.thinking-dot, .thinking-indicator {
  background: var(--g-spark);
  animation: g-pulse 1.4s ease-in-out infinite;
}
@keyframes g-pulse { 0%,100% { opacity: .5; transform: scale(.9);} 50% { opacity: 1; transform: scale(1);} }
```

（选择器名以 ThinkingBubble.vue 实际类名为准；实现时先读该文件确认类名再套用。）

- [ ] **Step 4: 构建验证**

Run: `npm run build`
Expected: 成功。

- [ ] **Step 5: dev 目测 + 回归**

确认：助手回复左侧有四色 spark 头像、无卡片框、markdown/代码/公式渲染正常、copy 按钮 hover 出现、文件预览可点开；用户消息右对齐气泡；流式中 thinking 态是渐变脉动；停止标记正常。

- [ ] **Step 6: 提交**

```bash
git add src/psi_agent/gateway/spa/src/components/MessageBubble.vue src/psi_agent/gateway/spa/src/components/ThinkingBubble.vue
git commit -m "feat(spa): 助手 spark 头像 + 去卡片消息流 + 渐变 thinking"
```

---

### Task 7: 全面回归 + 双主题走查

**Files:**
- 无代码改动（除非发现回归 bug 才修）

**Interfaces:**
- Consumes: Task 1–6 全部产出。

- [ ] **Step 1: 构建**

Run: `npm run build`
Expected: 成功，无警告级错误。

- [ ] **Step 2: 功能回归清单（dev，逐项勾）**

- [ ] 建 AI / 选模型 / 删模型
- [ ] 建会话 / 选会话 / 删会话 / 搜索 / 置顶 / 双击重命名
- [ ] 发消息 / SSE 流式渲染 / 停止生成
- [ ] 文件上传（拖放 + 按钮）/ 各格式预览
- [ ] 主题切换（浅↔深）各组件配色正常
- [ ] 侧栏折叠 / 展开
- [ ] 移动端（≤768px）：抽屉侧栏、`#mobile-topbar`、底部输入、虚拟键盘不遮挡
- [ ] 所有弹窗（AiDialog / SessDialog / ConfirmDialog / Snackbar）配色与交互正常

- [ ] **Step 3: 双主题目测**

light 和 dark 下各把上面全部界面看一遍，无低对比度/不可读/色值突兀处。

- [ ] **Step 4: 若有回归 bug，修复并单独提交**

```bash
git add <改动文件>
git commit -m "fix(spa): <具体回归修复>"
```

- [ ] **Step 5: 最终确认**

无回归则完成。分支 `feature/neural-expressive-skin` 待用户 review 后决定合并/推送。

---

## 备注

- `dist/` 构建产物是否随源码提交，遵循仓库既有约定（`.gitignore` 决定；不在本计划强制）。
- 若某组件实际类名/结构与本计划描述不符，以实际文件为准，先读后改，保持"逻辑不动、仅调 DOM/样式"原则。
