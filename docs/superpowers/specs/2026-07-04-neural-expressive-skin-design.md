# Neural Expressive 皮肤重构 — 设计文档

**日期**: 2026-07-04
**范围**: `src/psi_agent/gateway/spa/`（Web 控制台 SPA 前端）
**目标**: 在不破坏任何现有功能的前提下，把 "Neural Expressive" 视觉语言套用到现有 Vue 3 SPA 上，并按目标交互重排布局。

---

## 1. 背景与约束

现有 SPA 是【会话列表侧栏 + 聊天区 + 底部输入栏】的两栏结构，Vue 3 + Pinia + Vite，MD3 令牌驱动双主题。本次改造遵守 `spa/AGENTS.md` 全部约束，尤其：

- **令牌哲学**：全局 MD3 token 在 `styles/tokens.css`，颜色/圆角/阴影用 `--md-*` CSS variable，禁止硬编码色值，双主题一致。
- **样式分层**：组件专属样式（含移动端 `@media`）写在该组件 `<style scoped>`。
- **无外链**：字体/图标/CSS 走本地包，禁止 `<script src=https://...>` / `<link href=https://...>`。
- **技术栈封闭**：不引入 Vue Router / TypeScript / CSS 预处理器 / 动效库；不新增第三方依赖。
- **服务端唯一数据源**：不改 store 数据模型、不改 REST 调用、不改 composable 业务逻辑。

**用户决策（brainstorming 已确认）**：
1. 改造范围 = 重构成新布局（侧栏导航化）。
2. 侧栏用新外观，内容是真实会话（不加后端不存在的 Library/Gems 导航）。
3. 输入框做动态胶囊：新对话时居中、发出消息后沉底。
4. 忠实度定位：尽量像素级还原参考网页端设计。
5. 实施路径 = A 令牌层复用（改 `--md-*` 值 + 新增渐变/四色变量，不改令牌名）。

**当前 git 分支**：`refactor/spa-remove-undo`。实施前需切到干净的功能分支（如 `feature/neural-expressive-skin`），不在此分支直接堆改动。

---

## 2. 页面结构（改造前 → 改造后）

改造前后**三大部分不变**：Sidebar + ChatArea + InputBar，外加浮层（顶部浮动按钮 + 弹窗）。

```
┌─ Sidebar (280px) ──┬─ #chat 主区 (纵向) ───────────────┐
│                    │  TopBar: 折叠 / 主题 / 头像        │
│  Header: logo+品牌  │  ┌─────────────────────────────┐ │
│  New chat 药丸      │  │ ChatArea (#messages)        │ │
│  搜索框             │  │  空: 居中渐变问候语 + 居中胶囊 │ │
│  "最近" 分区        │  │  有消息: 消息流(spark头像)    │ │
│  会话列表           │  └─────────────────────────────┘ │
│  (置顶/重命名/删除)  │  InputBar: 药丸(动态居中↔沉底)     │
└────────────────────┴──────────────────────────────────┘
       + 弹窗层(AiDialog/SessDialog/ConfirmDialog/Snackbar)
```

---

## 3. 令牌层（`styles/tokens.css`）

只改 `--md-*` 令牌值 + 新增 `--g-*` 变量，**不改令牌名**，使所有组件自动跟随、双主题保持。

### 浅色主题 (`:root.light-mode`)

| 令牌 | 新值 |
|---|---|
| `--md-bg` | `#FFFFFF` |
| `--md-surface` | `#FFFFFF` |
| `--md-surface-container` | `#F0F4F9`（侧栏 / 胶囊底） |
| `--md-surface-container-high` | `#E9EEF6`（hover） |
| `--md-primary` | `#0B57D0`（品牌蓝） |
| `--md-on-primary` | `#FFFFFF` |
| `--md-text-primary` | `#1F1F1F` |
| `--md-text-secondary` | `#575B5F` |
| `--md-outline-variant` | `#DDE3EA` |

### 深色主题 (`:root`)

| 令牌 | 新值 |
|---|---|
| `--md-bg` | `#1B1C1D` |
| `--md-surface` | `#282A2C` |
| `--md-surface-container` | `#1E1F20` |
| `--md-surface-container-high` | `#333537` |
| `--md-primary` | `#A8C7FA` |
| `--md-text-primary` | `#E3E3E3` |
| `--md-text-secondary` | `#C4C7C5` |
| `--md-outline-variant` | `#3C4043` |

### 新增变量

```css
--g-grad-hello: linear-gradient(90deg,#4285F4,#9B72CB,#D96570); /* 问候语渐变裁字 */
--g-spark: conic-gradient(from 0deg,#4285F4,#9B72CB,#D96570,#FBBC04,#4285F4); /* 四色 spark */
--g-pill-radius: 28px;
```

圆角整体调大贴合圆润：`--md-shape-large` 16→20。

**风险**：`--md-primary` 由深蓝变品牌蓝，需目测回归所有用到 primary 的组件（dialog / snackbar / model panel / send 按钮 / 选中态）。

---

## 4. 布局 shell + 侧栏（`App.vue` + `Sidebar.vue`）

### Sidebar.vue（DOM 重排，会话逻辑/事件/store 绑定全部保持）

```
Sidebar (--md-surface-container 底)
├─ Header: [四色 spark logo] Dolphin      [折叠按钮 left_panel_close]
├─ New chat 药丸: [edit_square] 发起新对话   ← 复用现有 @new-session
├─ 搜索框(现有 session-search，圆润化)
├─ "最近" 分区标题
└─ 会话列表(现有 v-for visibleSessions，不变)
     每条：药丸 hover、置顶/删除保留、selected 用 `--md-surface-container-high` 底高亮
```

- **保留**：`selectSession` / `toggleSessionPin` / 重命名 / `confirmDeleteSession` / 搜索过滤 / 折叠 / 移动端抽屉。
- **改**：外层视觉、加 Header（logo 用 `--g-spark` 纯 CSS 圆，不引外部图片）、"新建"改独立药丸、加"最近"分区标题。

### App.vue（编排层，极小改动）

- 顶部浮动 `sidebar-toggle-btn` / `theme-toggle-btn` → 移入主区 TopBar（左上折叠、右上主题切换 + 头像）。
- 主区结构：`TopBar` + `ChatArea` + `InputBar`。
- **保留**：drag-drop 上传、mobile-overlay、弹窗挂载、onMounted 启动流程。
- 不碰任何 store / composable / api 调用。

---

## 5. 交互核心：欢迎屏 + 动态胶囊（`ChatArea.vue` + `InputBar.vue`）

唯一涉及新交互逻辑的部分，仍不碰后端/store 数据，只加派生状态。

### 判据

```
showWelcome = (messages.length === 0)   // 纯派生 computed，不进 store
```

- `showWelcome === true`：主区中央显示渐变问候语 + 居中胶囊，消息区隐藏。
- `showWelcome === false`：胶囊沉底，上方消息流。

### 实现方式（关键决策）

胶囊输入框是**同一个 InputBar 组件**，通过 App.vue 主区 CSS + Vue `<transition>` 控制其位置（居中 ↔ 沉底），**不是两个组件**。发送逻辑（`sendMessage` / 文件上传 / streaming / stop）完全复用。

### ChatArea.vue 空状态

```
messages.length === 0：
  主区中央垂直居中
  [渐变问候语] Qihua，你说，我在听!   ← --g-grad-hello + -webkit-background-clip:text
  [InputBar 胶囊]
```

### InputBar.vue 胶囊化

- 外层药丸（`--g-pill-radius` 圆角、`--md-surface` 底、细描边）。
- 单行：`[+attach] [textarea] [ModelPanel 内置右侧] [mic/send]`。
- **ModelPanel 移进胶囊内右侧**（现有组件，面板向上弹出正合适）。
- **保留**：file-preview-bar 附件 chips、streaming→stop 切换、`selectedSessionId` 禁用逻辑、移动端固定底部 + 虚拟键盘适配（`useKeyboard.js`）。

**风险**：InputBar 从"恒底部"变成"能居中也能沉底"，定位方式改动最需小心；必须保证移动端固定底部 + `useKeyboard.js` 键盘适配不被破坏。

---

## 6. 消息气泡 + 动效（`MessageBubble.vue` + `ThinkingBubble.vue`）

### MessageBubble.vue（纯样式，DOM/逻辑不变）

- **用户消息**：右对齐，`--md-surface-container` 底、大圆角气泡。
- **助手消息**：去掉卡片外框（裸文字流），左侧配 spark 头像（`--g-spark` 四色圆 + `auto_awesome` 图标），替换现有 `.role` "Assistant" 文字标签。
- **保留**：markdown/KaTeX 渲染（`v-html="msg.html"`）、copy 按钮、文件 blob 预览、stopped 标记、ThinkingBubble。
- copy 等操作按钮改 hover 显现的圆形图标条。

### 动效（功能性，纯 CSS，无新依赖）

- ThinkingBubble → 渐变脉动思考态（现有组件调样式）。
- 问候语 fade-in、胶囊居中↔沉底用 Vue `<transition>`。
- hover 用现有 `--md-state-*` state layer。

### 字体

保持 Inter（现有本地包），不换 Google Sans（无外链约束，Inter 已足够接近）。

---

## 7. 测试与验证策略

该 SPA 无前端测试框架（与现状一致，不新建）。验证靠构建 + 人工走查：

1. **构建校验**：`npm run build` 必须通过。
2. **dev 目测回归**（`npm run dev`）功能不回归清单：
   - 建 AI / 选模型 / 建会话 / 发消息 / SSE 流式 / 停止生成
   - 文件上传 + 预览（各格式）/ 会话搜索 / 置顶 / 重命名 / 删除
   - 主题切换（浅↔深）/ 侧栏折叠 / 移动端（≤768px）抽屉 + 键盘
   - 所有弹窗（AiDialog / SessDialog / ConfirmDialog / Snackbar）配色正常
3. **双主题目测**：每个改动组件在 light/dark 下各看一遍。

---

## 8. 范围红线（不做）

- 不引入路由 / TypeScript / 预处理器 / 动效库 / 任何新依赖。
- 不改后端、不改 store 数据模型、不改 api 调用、不重写 composable 业务逻辑。
- 不加后端不存在的导航项（Library / Gems / Notebooks）。
- 不做无关重构。

---

## 9. 受影响文件清单

| 文件 | 改动类型 |
|---|---|
| `styles/tokens.css` | 改 `--md-*` 值 + 新增 `--g-*` 变量 |
| `styles/layout.css` | 主区 shell / TopBar 布局 |
| `App.vue` | 模板结构（TopBar 编排）+ scoped 样式；逻辑不变 |
| `Sidebar.vue` | DOM 重排 + scoped 样式；会话逻辑不变 |
| `ChatArea.vue` | 空状态欢迎屏 + `showWelcome` 派生 + scoped 样式 |
| `InputBar.vue` | 胶囊化 + 动态定位 + scoped 样式；发送逻辑不变 |
| `ModelPanel.vue` | 内置进胶囊的样式微调 |
| `MessageBubble.vue` | 助手 spark 头像 + 去卡片 + scoped 样式；逻辑不变 |
| `ThinkingBubble.vue` | 渐变脉动样式 |

后端、store、composable 业务逻辑、api、sessionList.js 等**不在改动范围**。
