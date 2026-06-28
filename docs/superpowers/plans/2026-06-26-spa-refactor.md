# SPA Refactoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor monolithic `console.html` into Vite + Vue 3 SFC + Composition API project.

**Architecture:** Vite builds `.vue` SFCs into `dist/` served by Gateway static handler. `reactive()` store with `provide/inject`. Zero CDN.

**Tech Stack:** Vue 3.4, Vite 6, marked 11, KaTeX 0.16, Material Symbols 0.14

---

### Task 1: Scaffold project

**Files:**
- Create: `src/psi_agent/gateway/spa/package.json`
- Create: `src/psi_agent/gateway/spa/vite.config.js`
- Create: `src/psi_agent/gateway/spa/index.html`

**Steps:** Create package.json with vue/marked/katex/material-symbols deps and @vitejs/plugin-vue + vite devDeps. Create vite.config.js with `base: '/spa/'` and `@` alias. Create minimal index.html with `<div id="app">` and `<script type="module" src="/src/main.js">`. Run `npm install`. Verify `npx vite build` succeeds (empty app). Commit.

### Task 2: CSS token system

**Files:**
- Create: `src/psi_agent/gateway/spa/src/styles/tokens.css`
- Create: `src/psi_agent/gateway/spa/src/styles/components.css`
- Create: `src/psi_agent/gateway/spa/src/styles/layout.css`

**Steps:** Extract `:root` and `:root.light-mode` blocks (lines 12-80) from console.html into tokens.css. Extract MD3 component base classes (lines 98-194) into components.css. Extract all remaining CSS (lines 82-97, 202-866) into layout.css. Verify build includes CSS. Commit.

### Task 3: Utilities and store

**Files:**
- Create: `src/psi_agent/gateway/spa/src/utils.js` — renderMd, htmlEscape, mimeType, localStorage helpers
- Create: `src/psi_agent/gateway/spa/src/api.js` — fetch wrapper, streamChat, parseSSELine
- Create: `src/psi_agent/gateway/spa/src/providers.js` — PROVIDERS array
- Create: `src/psi_agent/gateway/spa/src/store.js` — reactive store with all state
- Create: `src/psi_agent/gateway/spa/src/main.js` — createApp, import CSS, mount #app

**Steps:** Extract from console.html (lines 1105-1135 for utils, lines 1137-1142 for providers, lines 1144-1168 for data/state). Create api.js with G(), api(), streamChat(), parseSSELine(). Create store.js with reactive(). Create main.js entry. Verify build resolves all imports. Commit.

### Task 4: Leaf components

**Files:**
- Create: `src/psi_agent/gateway/spa/src/components/ThinkingBubble.vue` — pure CSS animation, no props
- Create: `src/psi_agent/gateway/spa/src/components/Snackbar.vue` — store.snackbar bindings
- Create: `src/psi_agent/gateway/spa/src/components/ConfirmDialog.vue` — emits 'confirm'
- Create: `src/psi_agent/gateway/spa/src/components/FileBrowser.vue` — emits 'browse'/'select'

**Steps:** Extract template fragments from console.html (lines 940-950, 1096-1099, 1085-1094, 1062-1077) into SFCs with `<script setup>`. Use `useStore()`. Commit.

### Task 5: MessageBubble and AiDialog

**Files:**
- Create: `src/psi_agent/gateway/spa/src/components/MessageBubble.vue`
- Create: `src/psi_agent/gateway/spa/src/components/AiDialog.vue`

**Steps:** MessageBubble receives `msg` prop, imports ThinkingBubble, handles copy + fileUrl. AiDialog binds store.aiForm, handles provider change + model fetching, emits 'create'. Commit.

### Task 6: ModelPanel and SessDialog

**Files:**
- Create: `src/psi_agent/gateway/spa/src/components/ModelPanel.vue`
- Create: `src/psi_agent/gateway/spa/src/components/SessDialog.vue`

**Steps:** ModelPanel emits 'select-ai'/'delete-ai'/'new-ai'. SessDialog binds store.sessForm + store.browser, imports FileBrowser, handles browseWorkspace + selectWorkspace. Commit.

### Task 7: InputBar

**Files:**
- Create: `src/psi_agent/gateway/spa/src/components/InputBar.vue`

**Steps:** Binds store.inputText (v-model), store.selectedFile, store.streaming. File upload input + preview chip. ModelPanel integration. Emits 'send'. Enter key handler. Commit.

### Task 8: Sidebar

**Files:**
- Create: `src/psi_agent/gateway/spa/src/components/Sidebar.vue`

**Steps:** Session list with v-for. Selected state. Double-click to edit title. Workspace path display via getSessionDisplayName. Delete button → ConfirmDialog. New session button. Commit.

### Task 9: ChatArea

**Files:**
- Create: `src/psi_agent/gateway/spa/src/components/ChatArea.vue`

**Steps:** Message list with v-for. Empty state display. Auto-scroll with userHasScrolledUp lock. Wraps MessageBubble. Commit.

### Task 10: App.vue and composables

**Files:**
- Create: `src/psi_agent/gateway/spa/src/App.vue`
- Create: `src/psi_agent/gateway/spa/src/composables/useSSE.js`
- Create: `src/psi_agent/gateway/spa/src/composables/useTheme.js`
- Create: `src/psi_agent/gateway/spa/src/composables/useKeyboard.js`

**Steps:** App.vue composes Sidebar + ChatArea + InputBar + all dialogs + Snackbar. Extracts mounted() logic from console.html (lines 1559-1653) to `<script setup>`. Creates composables for SSE streaming, theme toggle, and visualViewport keyboard sync. Full integration test: `npm run dev` + manual smoke test. Commit.

### Task 11: Gateway integration

**Files:**
- Modify: `src/psi_agent/gateway/server.py`
- Modify: `src/psi_agent/gateway/_console.py`

**Steps:** In server.py, add `app.router.add_static('/spa/', str(spa_dist_path))` where `spa_dist_path` is `Path(__file__).parent / 'spa' / 'dist'`. Add `GET /` redirect to `/spa/`. In _console.py, simplify to just redirect. Run `npx vite build` first. Commit.

### Task 12: CI and cleanup

**Files:**
- Modify: `.github/workflows/nuitka.yml`
- Modify: `.github/workflows/ci.yml`
- Delete: `src/psi_agent/gateway/console.html`

**Steps:** Add `npm ci && npm run build` step in ci.yml. Change Nuitka `--include-data-files=console.html` to `--include-data-dir=src/psi_agent/gateway/spa/dist=spa/dist`. Delete the 1660-line console.html. Run all gateway tests. Commit.
