# 侧栏 Gemini 式置顶头部 + 渐变遮罩 + 快捷键 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把网关 SPA 侧栏的品牌/发起新对话/搜索会话固定置顶（只滚会话列表），加上下渐变遮罩、hover 才显示的滚动条、真实生效并悬停显示的快捷键，并给聊天胶囊上方补一条向上渐隐遮罩。

**Architecture:** 全部前端改动。侧栏 `Sidebar.vue` 拆成不滚动的 `.sb-top` 与可滚动的 `.sb-scroll` 两段；渐变用 `mask-image` 实现。快捷键把「keydown → 动作」的判定抽成纯函数 `matchSidebarShortcut(event)`（可单测），`App.vue` 用 VueUse `useEventListener` 绑全局 keydown 后调用它；聚焦搜索走 UI store 的一个递增 token 信号（不跨组件直接操作 DOM）。胶囊渐变作用在 `ChatArea.vue` 的 `#messages` 滚动容器底部。

**Tech Stack:** Vue 3 `<script setup>`, Pinia, @vueuse/core, Vite；本次引入 **Vitest** 对纯逻辑（快捷键判定、store 信号）做单测，CSS/遮罩仍靠 `npm run build` + 手动核对。

## Global Constraints

- 本仓 SPA 已迁 Pinia + @vueuse/core；跨组件通信走 store，不直接跨组件操作 DOM。
- 不修改全局滚动条样式（`src/styles/layout.css:20-22`），滚动条改动作用域限定 `.sb-scroll`。
- 提交信息不带 Claude 署名 / Co-Authored-By，用用户本人 authorship。
- 测试策略：可抽成纯函数/store 的逻辑用 Vitest 单测（TDD：先写失败测试）；CSS/布局/遮罩等视觉改动以 `npm run build` 通过 + 明确手动核对为验收（无法单测）。
- 当前分支 `docs/spa-agents-vueuse-constraint`，工作目录 `d:/File/FuClaw/psi-agent`；SPA 根 `src/psi_agent/gateway/spa`。

---

### Task 0: 引入 Vitest 测试基建

**Files:**
- Modify: `src/psi_agent/gateway/spa/package.json`（devDependency + `test` 脚本）
- Modify: `src/psi_agent/gateway/spa/vite.config.js`（加 `test` 配置）
- Create: `src/psi_agent/gateway/spa/src/__tests__/smoke.test.js`（验证 runner 可用，随后删除）

**Interfaces:**
- Produces: `npm test`（vitest run，非 watch）可用；`import { describe, it, expect } from 'vitest'` 生效。后续 Task 3 依赖它。

- [ ] **Step 1: 安装 vitest**

Run: `cd src/psi_agent/gateway/spa && npm install -D vitest@^3`
Expected: `package.json` devDependencies 出现 `vitest`，无安装错误。

- [ ] **Step 2: 加 `test` 脚本**

在 [package.json](src/psi_agent/gateway/spa/package.json) 的 `scripts` 中，`"preview": "vite preview"` 后加一行：

```json
    "test": "vitest run"
```

- [ ] **Step 3: 在 vite.config.js 加 test 配置**

vitest 复用 vite 配置。把 [vite.config.js](src/psi_agent/gateway/spa/vite.config.js) 顶部注释 + 配置改为带 `test` 段（`environment: 'node'`，逻辑单测不需要 DOM）：

在 `defineConfig({ ... })` 内、`resolve` 之后加：

```javascript
  test: {
    environment: 'node',
    include: ['src/**/*.test.js'],
  },
```

- [ ] **Step 4: 写冒烟测试确认 runner 工作**

Create `src/psi_agent/gateway/spa/src/__tests__/smoke.test.js`:

```javascript
import { describe, it, expect } from 'vitest'

describe('vitest smoke', () => {
  it('runs', () => {
    expect(1 + 1).toBe(2)
  })
})
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd src/psi_agent/gateway/spa && npm test`
Expected: PASS，1 passed。

- [ ] **Step 6: 删除冒烟测试并提交基建**

```bash
rm src/psi_agent/gateway/spa/src/__tests__/smoke.test.js
git add src/psi_agent/gateway/spa/package.json src/psi_agent/gateway/spa/package-lock.json src/psi_agent/gateway/spa/vite.config.js
git commit -m "test(spa): 引入 vitest 测试基建"
```

（注：`node_modules` 由 `.gitignore` 排除，只提交 package.json / lock / config。）

---

### Task 1: 侧栏结构拆分 — 头部置顶 + 列表滚动 + 渐变遮罩 + hover 滚动条

**Files:**
- Modify: `src/psi_agent/gateway/spa/src/components/Sidebar.vue`（template 结构 + `<style scoped>`）

**Interfaces:**
- Consumes: 现有 `sessions/selectedSessionId/sessionSearchText` 等 store refs（不变）。
- Produces: DOM 中出现 `.sb-top`（不滚动）与 `.sb-scroll`（`overflow-y:auto`）两个容器，供后续任务的快捷键提示与聚焦复用。

- [ ] **Step 1: 重排 template — 引入 `.sb-top` 和 `.sb-scroll`**

在 [Sidebar.vue](src/psi_agent/gateway/spa/src/components/Sidebar.vue) 中，把 `.col` 内部包成两段。`sb-header`、`new-chat`、`session-search` 放进 `.sb-top`；`recent-label`、会话 `v-for`、`session-empty` 放进 `.sb-scroll`。替换现有 `<div class="col">…</div>` 内部：

```html
    <div class="col">
      <div class="sb-top">
        <div class="sb-header">
          <div class="sb-brand">
            <div class="sb-logo"></div>
            <span class="sb-brand-name">Dolphin</span>
          </div>
        </div>
        <button class="new-chat" @click="$emit('new-session')">
          <span class="material-symbols-outlined">edit_square</span>
          <span class="label">发起新对话</span>
        </button>
        <div class="session-search">
          <span class="material-symbols-outlined">search</span>
          <input
            v-model="sessionSearchText"
            type="search"
            placeholder="搜索会话"
            aria-label="搜索会话"
          >
          <button
            v-if="sessionSearchText"
            class="clear-search"
            type="button"
            title="清空搜索"
            @click="sessionSearchText = ''"
          >
            <span class="material-symbols-outlined">close</span>
          </button>
        </div>
      </div>
      <div class="sb-scroll">
        <div class="recent-label">最近</div>
        <div
          v-for="s in visibleSessions"
          :key="s.id"
          class="item"
          :class="{ selected: s.id === selectedSessionId }"
          @click="selectSession(s.id)"
        >
          <button
            class="pin"
            :class="{ pinned: isSessionPinned(s.id) }"
            @click.stop="toggleSessionPin(s.id)"
            :title="isSessionPinned(s.id) ? '取消置顶' : '置顶会话'"
          >
            <span class="material-symbols-outlined">keep</span>
          </button>
          <span class="info">
            <input
              v-if="editingSessionId === s.id"
              v-model="editingWorkspaceText"
              class="edit-input"
              v-focus
              @blur="saveSessionWorkspace(s)"
              @keydown.enter="saveSessionWorkspace(s)"
              @click.stop
            >
            <div
              v-else
              class="name"
              :title="displaySessionName(s)"
              @dblclick.stop="startEditWorkspace(s)"
            >
              {{ displaySessionName(s) }}
            </div>
          </span>
          <button class="del" @click.stop="confirmDeleteSession(s.id)">
            <span class="material-symbols-outlined">delete</span>
          </button>
        </div>
        <div v-if="sessions.length && visibleSessions.length === 0" class="session-empty">
          没有匹配的会话
        </div>
      </div>
    </div>
```

（注意 `发起新对话` 文字外层包了 `<span class="label">`，为 Task 2 的快捷键提示对齐留位。）

- [ ] **Step 2: 改 `.col` 为不滚动，新增 `.sb-top` / `.sb-scroll` 样式**

在 `<style scoped>` 中，把 `.col` 的 `overflow-y: auto;` 改为 `overflow: hidden;`，并新增两个容器规则。找到：

```css
#sidebar .col {
  width: 280px;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow-y: auto;
}
```

替换为：

```css
#sidebar .col {
  width: 280px;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}
.sb-top { flex-shrink: 0; }
.sb-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  -webkit-mask-image: linear-gradient(to bottom,
    transparent 0, #000 12px, #000 calc(100% - 12px), transparent 100%);
          mask-image: linear-gradient(to bottom,
    transparent 0, #000 12px, #000 calc(100% - 12px), transparent 100%);
}
```

- [ ] **Step 3: 给 `.sb-scroll` 加 hover 才显示的滚动条**

在 `.sb-scroll` 规则后追加（作用域限定，不动全局 `layout.css`）：

```css
.sb-scroll {
  scrollbar-width: thin;
  scrollbar-color: transparent transparent;
}
.sb-scroll:hover { scrollbar-color: var(--md-outline-variant) transparent; }
.sb-scroll::-webkit-scrollbar { width: 6px; }
.sb-scroll::-webkit-scrollbar-track { background: transparent; }
.sb-scroll::-webkit-scrollbar-thumb { background: transparent; border-radius: 10px; transition: background 0.2s; }
.sb-scroll:hover::-webkit-scrollbar-thumb { background: var(--md-outline-variant); }
```

（把 `scrollbar-width/color` 两行并入 Step 2 已建的 `.sb-scroll` 块亦可；分开写便于审阅。）

- [ ] **Step 4: 构建验证**

Run: `cd src/psi_agent/gateway/spa && npm run build`
Expected: 构建成功，无报错。

- [ ] **Step 5: 手动核对**

`npm run dev` 打开侧栏，确认：
1. 会话多到出现滚动时，「发起新对话」和「搜索会话」保持置顶不消失。
2. 会话列表顶/底出现约 12px 的渐隐带。
3. 鼠标移入列表区才浮现滚动条，移出隐藏。
4. 折叠侧栏（collapsed）与移动端（≤768px）不破版。

- [ ] **Step 6: Commit**

```bash
git add src/psi_agent/gateway/spa/src/components/Sidebar.vue
git commit -m "feat(spa): 侧栏头部置顶，列表独立滚动+渐变遮罩+hover滚动条"
```

---

### Task 2: 快捷键悬停提示文字

**Files:**
- Modify: `src/psi_agent/gateway/spa/src/components/Sidebar.vue`（template + `<style scoped>`）

**Interfaces:**
- Consumes: Task 1 的 `.new-chat`、`.session-search` 结构与 `sessionSearchText` ref。
- Produces: 两处 `.shortcut` 提示元素，hover 行时淡入显示 `Ctrl+Shift+O` / `Ctrl+Shift+K`。

- [ ] **Step 1: 在 new-chat 行加快捷键提示**

在 `.new-chat` 按钮内、`<span class="label">发起新对话</span>` 之后加：

```html
          <span class="shortcut">Ctrl+Shift+O</span>
```

改造后的按钮：

```html
        <button class="new-chat" @click="$emit('new-session')">
          <span class="material-symbols-outlined">edit_square</span>
          <span class="label">发起新对话</span>
          <span class="shortcut">Ctrl+Shift+O</span>
        </button>
```

- [ ] **Step 2: 在搜索行加快捷键提示**

在 `.session-search` 内，把提示放在 input 之后、clear 按钮之前，输入非空时隐藏以免与清空按钮重叠：

```html
          <span v-if="!sessionSearchText" class="shortcut search-shortcut">Ctrl+Shift+K</span>
```

即结构为 `<span search icon> <input> <span shortcut v-if 空> <button clear v-if 非空>`。

- [ ] **Step 3: 加提示样式（默认隐藏，hover 行淡入）**

在 `<style scoped>` 末尾（`@media` 之前）追加：

```css
.new-chat { position: relative; }
.shortcut {
  margin-left: auto;
  font-size: 12px;
  color: var(--md-text-secondary);
  opacity: 0;
  transition: opacity 0.15s;
  white-space: nowrap;
  flex-shrink: 0;
}
.new-chat:hover .shortcut,
.session-search:hover .shortcut,
.session-search:focus-within .shortcut { opacity: 1; }
.search-shortcut { margin-left: 4px; }
```

（`.new-chat` 已是 `display:flex`，`margin-left:auto` 会把提示推到最右，与截图一致。）

- [ ] **Step 4: 构建验证**

Run: `cd src/psi_agent/gateway/spa && npm run build`
Expected: 构建成功。

- [ ] **Step 5: 手动核对**

鼠标移到「发起新对话」行 → 右侧淡入 `Ctrl+Shift+O`；移到搜索行 → 淡入 `Ctrl+Shift+K`；在搜索框输入文字后该提示消失、清空按钮出现。

- [ ] **Step 6: Commit**

```bash
git add src/psi_agent/gateway/spa/src/components/Sidebar.vue
git commit -m "feat(spa): 侧栏悬停显示新建/搜索快捷键提示"
```

---

### Task 3: 快捷键判定纯函数（TDD）+ store 聚焦信号 + 绑定/聚焦

**Files:**
- Create: `src/psi_agent/gateway/spa/src/shortcuts.js`（纯函数 `matchSidebarShortcut`）
- Create: `src/psi_agent/gateway/spa/src/shortcuts.test.js`
- Create: `src/psi_agent/gateway/spa/src/stores/ui.test.js`
- Modify: `src/psi_agent/gateway/spa/src/stores/ui.js`（新增聚焦信号 token + 方法）
- Modify: `src/psi_agent/gateway/spa/src/App.vue`（全局 keydown 绑定，调用纯函数）
- Modify: `src/psi_agent/gateway/spa/src/components/Sidebar.vue`（input ref + watch 聚焦）

**Interfaces:**
- Consumes: `openSessDialog()`（App.vue 已有，行 260），`isSidebarCollapsed` / `isMobileSidebarOpen`（App.vue 行 125 已解构），`isMobile`（行 138），Task 0 的 vitest。
- Produces:
  - `matchSidebarShortcut(event) → 'new-session' | 'focus-search' | null`（纯函数）。
  - UI store 新增 `sessionSearchFocusToken`(ref number) 与 `focusSessionSearch()`（每次调用递增 token）。
  - Sidebar `watch` 该 token 后聚焦搜索框。

- [ ] **Step 1: 写快捷键判定的失败测试**

Create `src/psi_agent/gateway/spa/src/shortcuts.test.js`:

```javascript
import { describe, it, expect } from 'vitest'
import { matchSidebarShortcut } from './shortcuts.js'

const ev = (o) => ({ ctrlKey: false, metaKey: false, shiftKey: false, code: '', ...o })

describe('matchSidebarShortcut', () => {
  it('Ctrl+Shift+O → new-session', () => {
    expect(matchSidebarShortcut(ev({ ctrlKey: true, shiftKey: true, code: 'KeyO' }))).toBe('new-session')
  })
  it('Cmd+Shift+O → new-session', () => {
    expect(matchSidebarShortcut(ev({ metaKey: true, shiftKey: true, code: 'KeyO' }))).toBe('new-session')
  })
  it('Ctrl+Shift+K → focus-search', () => {
    expect(matchSidebarShortcut(ev({ ctrlKey: true, shiftKey: true, code: 'KeyK' }))).toBe('focus-search')
  })
  it('无 shift → null', () => {
    expect(matchSidebarShortcut(ev({ ctrlKey: true, code: 'KeyO' }))).toBe(null)
  })
  it('无修饰键 → null', () => {
    expect(matchSidebarShortcut(ev({ shiftKey: true, code: 'KeyO' }))).toBe(null)
  })
  it('其它按键 → null', () => {
    expect(matchSidebarShortcut(ev({ ctrlKey: true, shiftKey: true, code: 'KeyJ' }))).toBe(null)
  })
})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd src/psi_agent/gateway/spa && npm test`
Expected: FAIL（`shortcuts.js` 不存在 / `matchSidebarShortcut is not a function`）。

- [ ] **Step 3: 实现 `matchSidebarShortcut`**

Create `src/psi_agent/gateway/spa/src/shortcuts.js`:

```javascript
// 侧栏全局快捷键判定：keydown 事件 → 动作字符串（无匹配返回 null）。
// 用 event.code 而非 key，规避 Shift 组合下的大小写/输入法差异；同时兼容 Cmd。
export function matchSidebarShortcut(e) {
  const mod = e.ctrlKey || e.metaKey
  if (!mod || !e.shiftKey) return null
  if (e.code === 'KeyO') return 'new-session'
  if (e.code === 'KeyK') return 'focus-search'
  return null
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd src/psi_agent/gateway/spa && npm test`
Expected: PASS（shortcuts 6 项全过）。

- [ ] **Step 5: 写 store 聚焦信号的失败测试**

Create `src/psi_agent/gateway/spa/src/stores/ui.test.js`:

```javascript
import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useUiStore } from './ui.js'

describe('ui store focusSessionSearch', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('focusSessionSearch 递增 token', () => {
    const ui = useUiStore()
    expect(ui.sessionSearchFocusToken).toBe(0)
    ui.focusSessionSearch()
    expect(ui.sessionSearchFocusToken).toBe(1)
    ui.focusSessionSearch()
    expect(ui.sessionSearchFocusToken).toBe(2)
  })
})
```

- [ ] **Step 6: 运行测试确认失败**

Run: `cd src/psi_agent/gateway/spa && npm test`
Expected: FAIL（`focusSessionSearch is not a function` / token undefined）。

- [ ] **Step 7: UI store 增加聚焦信号**

在 [ui.js](src/psi_agent/gateway/spa/src/stores/ui.js) 中，`const dlgSess = ref(false)` 之后加 ref：

```javascript
  const sessionSearchFocusToken = ref(0)
```

`closeMobileSidebar` 之后加方法：

```javascript
  function focusSessionSearch() {
    sessionSearchFocusToken.value++
  }
```

在 `return { … }` 中加入 `sessionSearchFocusToken,` 和 `focusSessionSearch,`。

- [ ] **Step 8: 运行测试确认全部通过**

Run: `cd src/psi_agent/gateway/spa && npm test`
Expected: PASS（shortcuts + ui store 全过）。

- [ ] **Step 9: App.vue 绑定全局快捷键（调用纯函数）**

在 [App.vue](src/psi_agent/gateway/spa/src/App.vue) 的 import 行补 `useEventListener`：

找到：
```javascript
import { useBreakpoints, useDropZone, useStorage } from '@vueuse/core'
```
改为：
```javascript
import { useBreakpoints, useDropZone, useStorage, useEventListener } from '@vueuse/core'
```

在既有 import 区加入纯函数（放在 `import { selectSession } ...` 附近）：

```javascript
import { matchSidebarShortcut } from './shortcuts.js'
```

在 `useKeyboard()` 调用之后加绑定：

```javascript
useEventListener(window, 'keydown', (e) => {
  const action = matchSidebarShortcut(e)
  if (!action) return
  e.preventDefault()
  if (action === 'new-session') {
    openSessDialog()
  } else if (action === 'focus-search') {
    if (isMobile.value) {
      isMobileSidebarOpen.value = true
    } else {
      isSidebarCollapsed.value = false
    }
    ui.focusSessionSearch()
  }
})
```

（`isSidebarCollapsed` / `isMobileSidebarOpen` 已在行 125 从 store 解构；`isMobile` 见行 138；`openSessDialog` 见行 260；`ui` 见行 124。）

- [ ] **Step 10: Sidebar 给搜索框加 ref 并响应聚焦信号**

在 [Sidebar.vue](src/psi_agent/gateway/spa/src/components/Sidebar.vue) 的搜索 `<input>` 上加 `ref="searchInputRef"`：

```html
          <input
            ref="searchInputRef"
            v-model="sessionSearchText"
            type="search"
            placeholder="搜索会话"
            aria-label="搜索会话"
          >
```

`import { computed, watch }` 补 `ref, nextTick`：

```javascript
import { computed, watch, ref, nextTick } from 'vue'
```

从 ui store 解构追加 `sessionSearchFocusToken`：

```javascript
const { isSidebarCollapsed, isMobileSidebarOpen, dlgConfirm, sessionSearchFocusToken } = storeToRefs(ui)
```

在 `defineEmits` 之后声明 ref 与 watch：

```javascript
const searchInputRef = ref(null)
watch(sessionSearchFocusToken, () => {
  nextTick(() => searchInputRef.value?.focus())
})
```

- [ ] **Step 11: 构建 + 测试验证**

Run: `cd src/psi_agent/gateway/spa && npm test && npm run build`
Expected: 测试全过 + 构建成功。

- [ ] **Step 12: 手动核对**

`npm run dev`：
1. 按 `Ctrl+Shift+O`（Mac `Cmd+Shift+O`）→ 弹出新建会话对话框。
2. 按 `Ctrl+Shift+K` → 若侧栏折叠先展开，搜索框获得焦点、可直接输入。
3. 移动端（≤768px）按 `Ctrl+Shift+K` → 移动侧栏打开且搜索聚焦。

- [ ] **Step 13: Commit**

```bash
git add src/psi_agent/gateway/spa/src/shortcuts.js src/psi_agent/gateway/spa/src/shortcuts.test.js src/psi_agent/gateway/spa/src/stores/ui.js src/psi_agent/gateway/spa/src/stores/ui.test.js src/psi_agent/gateway/spa/src/App.vue src/psi_agent/gateway/spa/src/components/Sidebar.vue
git commit -m "feat(spa): 绑定 Ctrl+Shift+O 新建 / Ctrl+Shift+K 聚焦搜索（含单测）"
```

---

### Task 4: 聊天胶囊上方向上渐隐遮罩

**Files:**
- Modify: `src/psi_agent/gateway/spa/src/components/ChatArea.vue`（`#messages` 底部 `mask-image`）

**Interfaces:**
- Consumes: `#messages` 滚动容器（ChatArea.vue 行 2/34）。
- Produces: 消息滚到底部靠近胶囊处向上渐隐；欢迎屏（无 `#messages`）不受影响。

- [ ] **Step 1: 给 `#messages` 桌面态加底部渐隐**

在 [ChatArea.vue](src/psi_agent/gateway/spa/src/components/ChatArea.vue) 的 `#messages` 规则中加 `mask-image`。找到：

```css
#messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
}
```

替换为：

```css
#messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  -webkit-mask-image: linear-gradient(to bottom, #000 calc(100% - 24px), transparent 100%);
          mask-image: linear-gradient(to bottom, #000 calc(100% - 24px), transparent 100%);
}
```

（只在底部约 24px 渐隐，向上淡出到胶囊；顶部不做以免遮挡首条消息。）

- [ ] **Step 2: 构建验证**

Run: `cd src/psi_agent/gateway/spa && npm run build`
Expected: 构建成功。

- [ ] **Step 3: 手动核对**

有多条消息、可滚动时，底部靠近输入胶囊处消息呈向上渐隐；欢迎屏（新会话）无异常；移动端消息底部（`padding-bottom:80px`）不被裁掉关键内容。

- [ ] **Step 4: Commit**

```bash
git add src/psi_agent/gateway/spa/src/components/ChatArea.vue
git commit -m "feat(spa): 聊天消息区底部向上渐隐遮罩，与侧栏统一"
```

---

## Self-Review

**Spec coverage:**
- vitest 基建 → Task 0 ✅
- 目标1 头部置顶 → Task 1 ✅
- 目标2 上下渐变遮罩 → Task 1 (`.sb-scroll` mask) ✅
- 目标3 快捷键绑定+悬停显示 → Task 2（提示）+ Task 3（判定纯函数+单测+绑定/聚焦）✅
- 目标4 hover 滚动条 → Task 1 Step 3 ✅
- 目标5 胶囊向上渐隐 → Task 4 ✅

**测试覆盖：** Task 3 用 Vitest 单测 `matchSidebarShortcut`（6 例：Ctrl/Cmd+Shift+O、+K、缺 shift、缺修饰键、其它键）与 store `focusSessionSearch`（token 递增）。CSS/遮罩/hover 滚动条/悬停提示为纯视觉，靠 `npm run build` + 手动核对（无法单测）。

**Placeholder scan:** 无 TBD/TODO，所有步骤含具体代码与命令。

**Type consistency:** `matchSidebarShortcut` 返回 `'new-session'|'focus-search'|null`（shortcuts.js 定义、test 与 App.vue 消费一致）；`focusSessionSearch` / `sessionSearchFocusToken`（ui.js 定义、ui.test 与 App.vue 调用、Sidebar.vue 消费）四处一致；`searchInputRef` 仅 Sidebar 内部；`openSessDialog` 复用 App.vue 既有函数。
