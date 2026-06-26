<script setup>
import { ref } from 'vue'
import { DEFAULT_WORKSPACE } from './lib/workspaces.js'
import ChatPanel from './components/ChatPanel.vue'
import ControlPanel from './components/ControlPanel.vue'
import dolphinLogo from './assets/dolphin.jpg'

const theme = ref('light')
function setTheme(t) {
  theme.value = t
  document.documentElement.dataset.theme = t
}

const workspace = ref(DEFAULT_WORKSPACE)
const chatKey = ref(0)
const restarting = ref(false)

// Remounting ChatPanel (via :key) tears down its session (DELETE /sessions/{id}
// on unmount) and creates a fresh one on mount, which clears the conversation.
async function refreshCurrentSession() {
  if (restarting.value) return
  restarting.value = true
  try {
    chatKey.value += 1
  } finally {
    restarting.value = false
  }
}

// Switching workspace rebinds the session to a different agent socket, so we
// remount ChatPanel to start a clean conversation on the new workspace.
function selectWorkspace(name) {
  if (name === workspace.value) return
  workspace.value = name
  chatKey.value += 1
}
</script>

<template>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <div class="mark">
          <img :src="dolphinLogo" alt="dolphin" />
        </div>
        <div>
          <div class="brand-name">dolphin-agent</div>
          <div class="brand-sub">composable agent framework</div>
        </div>
      </div>
      <div class="top-right">
        <div class="status"><i></i><span>就绪</span></div>
        <div class="theme-toggle">
          <button :class="{ on: theme === 'light' }" @click="setTheme('light')">浅色</button>
          <button :class="{ on: theme === 'dark' }" @click="setTheme('dark')">深色</button>
        </div>
      </div>
    </header>

    <div class="body">
      <ControlPanel
        :workspace="workspace"
        :restarting="restarting"
        @select-workspace="selectWorkspace"
        @restart-session="refreshCurrentSession"
      />
      <ChatPanel :key="chatKey" :workspace="workspace" />
    </div>
  </div>
</template>

<style scoped>
.app { height: 100vh; display: grid; grid-template-rows: 64px 1fr; }
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 24px; background: var(--surface); border-bottom: 1px solid var(--line);
}
.brand { display: flex; align-items: center; gap: 12px; }
.mark {
  width: 34px; height: 34px; border-radius: var(--r-lg); overflow: hidden;
  display: grid; place-items: center; background: #fff;
}
.mark img { width: 100%; height: 100%; object-fit: cover; display: block; }
.brand-name { font-weight: 700; font-size: 16px; }
.brand-sub { font-size: 11px; color: var(--muted); }
.top-right { display: flex; align-items: center; gap: 14px; }
.status {
  display: flex; align-items: center; gap: 7px; padding: 6px 12px;
  border-radius: var(--r-full); background: var(--surface-2); font-size: 12px; color: var(--muted);
}
.status i { width: 7px; height: 7px; border-radius: var(--r-full); background: var(--accent); }
.theme-toggle {
  display: flex; align-items: center; border-radius: var(--r-full);
  background: var(--surface-2); border: 1px solid var(--line); overflow: hidden;
}
.theme-toggle button {
  border: 0; background: transparent; color: var(--muted); font: inherit; font-size: 12px;
  padding: 6px 12px; cursor: pointer; border-radius: var(--r-full);
}
.theme-toggle button.on { background: var(--accent); color: var(--on-accent); font-weight: 600; }

.body {
  display: grid; grid-template-columns: 332px minmax(0, 1fr); gap: 16px; padding: 16px;
  min-height: 0;
}
</style>
