# 侧栏 Gemini 式置顶头部 + 渐变遮罩 + 快捷键 设计

日期: 2026-07-06
范围: 网关 SPA (`src/psi_agent/gateway/spa`)
主要文件: `src/components/Sidebar.vue`, `src/App.vue`, `src/components/InputBar.vue`

## 背景与问题

当前侧栏 [Sidebar.vue] 把整个 `.col` 设为 `overflow-y: auto`，导致「发起新对话」按钮和「搜索会话」框会随会话列表一起向上滚走。Gemini 的做法是把这两项与品牌区固定在顶部，只让「最近」会话列表滚动，并在置顶区与列表之间用渐变半透明遮罩做视觉过渡。

同时确认的现状：
- 快捷键（Ctrl+Shift+O / Ctrl+Shift+K）当前既无提示文字也未绑定。
- 全局滚动条为 6px 常显（`styles/layout.css:20-22`）。
- 聊天输入胶囊 `#input-wrapper` 为 `background: transparent`，未做任何向上渐隐遮罩。

## 目标

1. 头部置顶：品牌 + 发起新对话 + 搜索会话 固定，不随滚动消失。
2. 渐变遮罩：会话列表滚动区顶/底做 `mask-image` 淡出。
3. 快捷键：真实绑定生效 + 悬停显示提示（Ctrl+Shift+O 新建、Ctrl+Shift+K 聚焦搜索）。
4. 会话列表滚动条改为 hover 才显示（Gemini 风格）。
5. 胶囊上方一并加向上渐隐遮罩，与侧栏视觉统一。

非目标：不改会话数据模型、置顶/搜索/重命名等既有交互逻辑。

## 详细设计

### 1. Sidebar 结构重排

将 `.col` 改为不滚动的 flex 容器，拆成两段：

```
.col (flex column, height:100%, overflow:hidden)
 ├─ .sb-top (flex-shrink:0)   ← sb-header + new-chat + session-search
 └─ .sb-scroll (flex:1, overflow-y:auto)  ← recent-label + v-for items + empty
```

- 把 `overflow-y: auto` 从 `.col` 移到新的 `.sb-scroll`。
- `.col` 改为 `overflow: hidden`，保证内部裁剪与遮罩生效。
- 模板中把 `.recent-label`、会话 `v-for`、`.session-empty` 包进 `.sb-scroll`；`sb-header`/`new-chat`/`session-search` 包进 `.sb-top`。

### 2. 渐变半透明遮罩

对 `.sb-scroll` 应用垂直方向 `mask-image`（内容自身淡出，无需匹配背景色）：

```css
.sb-scroll {
  flex: 1;
  overflow-y: auto;
  -webkit-mask-image: linear-gradient(to bottom,
    transparent 0, #000 12px, #000 calc(100% - 12px), transparent 100%);
          mask-image: linear-gradient(to bottom,
    transparent 0, #000 12px, #000 calc(100% - 12px), transparent 100%);
}
```

顶/底各约 12px 渐隐带。这是 Gemini 置顶区与列表间那条渐变的等价实现。

### 3. 快捷键 —— 绑定 + 悬停显示

**提示文字**：在 new-chat 与 session-search 行右侧加 `<span class="shortcut">`，展示 `Ctrl+Shift+O` / `Ctrl+Shift+K`。默认 `opacity: 0`，父行 `:hover` 时 `opacity: 1`（0.15s 过渡），与截图一致。搜索行因含 input，提示放在 input 与 clear 按钮之间，输入非空时隐藏以免遮挡。

**真实绑定**：在 `App.vue` 注册全局 `keydown`（`onMounted` 加、`onBeforeUnmount` 移除，或用 VueUse `useEventListener`——本仓已引入 @vueuse/core）：
- `(ctrlKey||metaKey) && shiftKey && code==='KeyO'` → 触发新建会话（复用现有 new-session 路径）。
- `(ctrlKey||metaKey) && shiftKey && code==='KeyK'` → 聚焦搜索框。
- 两者均 `preventDefault()`。用 `code` 而非 `key`，规避 Shift 组合下字母大小写/输入法差异。

**聚焦搜索**：给 session-search 的 `<input>` 加 `ref`，通过 UI store 暴露一个 `focusSessionSearch` 触发信号（或用现有 store 状态），由 Sidebar `watch` 后调用 `input.focus()`。避免跨组件直接操作 DOM。

### 4. 滚动条 hover 才显示

在 `.sb-scroll` 局部覆盖全局滚动条样式（scoped 内用 `:deep` 或直接选择器）：
- 默认 `::-webkit-scrollbar-thumb` 透明；
- `.sb-scroll:hover ::-webkit-scrollbar-thumb` 显示 `--md-outline-variant`。
- Firefox 用 `scrollbar-width: thin` + `scrollbar-color`，hover 时切换（能力有限，降级为常细条可接受）。

不改全局 `layout.css`，避免影响其它滚动区。

### 5. 胶囊向上渐隐遮罩

给聊天消息滚动区底部（`#chat-main` 内消息容器）加向上 `mask-image` 淡出，或在 `#input-wrapper` 上方放一层 `pointer-events:none` 的 `linear-gradient` 遮罩（从聊天背景色到 transparent）。优先在消息滚动容器上用 `mask-image`，与侧栏方案一致；`welcome` 居中态不需要，仅在有消息滚动时生效。

## 影响与验证

- 改动集中在 `Sidebar.vue`（结构+样式）、`App.vue`（快捷键+聚焦信号）、`InputBar.vue`/消息区（胶囊遮罩）。
- 验证：`npm run build` 通过；手动核对——头部置顶不滚走、列表上下渐隐、hover 出滚动条、两个快捷键生效、hover 出快捷键提示、胶囊上方消息渐隐。
- 兼容：桌面与移动断点（768px）下头部置顶与遮罩均不破版。

## 遵循的约束

- 本仓 SPA 已迁 Pinia + @vueuse/core；跨组件通信走 store，不直接跨组件操作 DOM。
- 不改全局滚动条样式，作用域限定 `.sb-scroll`。
