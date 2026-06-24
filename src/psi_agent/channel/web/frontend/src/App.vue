<script setup>
import { reactive, ref, computed, watch } from 'vue'
import { MODULES, DEFAULT_STATE } from './lib/modules.js'
import ChatPanel from './components/ChatPanel.vue'
import ControlPanel from './components/ControlPanel.vue'
import EvolutionDrawer from './components/EvolutionDrawer.vue'
import { restartSession } from './lib/chat.js'
import dolphinLogo from './assets/dolphin.jpg'

const theme = ref('light')
function setTheme(t) {
  theme.value = t
  document.documentElement.dataset.theme = t
}

// Self-evolution drawer (embeds the demo module via the /demo/ proxy).
const drawerOpen = ref(false)
function openDrawer() { drawerOpen.value = true }
function closeDrawer() { drawerOpen.value = false }

// Shared switch state. The master switch reflects three states:
//   all off  → Hermes mode (gold theme, knob left)
//   all on   → full dolphin (blue theme, knob right)
//   partial  → mixed (grey, knob centered)
const modules = reactive({ ...DEFAULT_STATE })

const allOff = computed(() => MODULES.every((m) => !modules[m.key]))
const allOn = computed(() => MODULES.every((m) => modules[m.key]))
// master switch position: 'off' | 'on' | 'mid'
const masterState = computed(() => (allOff.value ? 'off' : allOn.value ? 'on' : 'mid'))

// Gold accent only when every module is off (bare Hermes).
watch(
  allOff,
  (on) => {
    if (on) document.documentElement.dataset.accent = 'hermes'
    else delete document.documentElement.dataset.accent
  },
  { immediate: true },
)

// Master switch: turn everything off (enter Hermes) or everything on (exit).
function setAll(on) {
  for (const m of MODULES) modules[m.key] = on
}

function toggleModule(key) {
  modules[key] = !modules[key]
}

const activeCount = computed(() => MODULES.filter((m) => modules[m.key]).length)
const chatKey = ref(0)
const restarting = ref(false)

function modulesPayload() {
  const obj = {}
  for (const key of Object.keys(modules)) obj[key] = modules[key]
  return obj
}

async function refreshCurrentSession() {
  if (restarting.value) return
  restarting.value = true
  try {
    await restartSession({ modules: modulesPayload(), compare: allOff.value })
    chatKey.value += 1
  } catch (e) {
    window.alert(String(e && e.message ? e.message : e))
  } finally {
    restarting.value = false
  }
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
      <ChatPanel :key="chatKey" :modules="modules" :compare="allOff" :active-count="activeCount" />
      <ControlPanel
        :modules="modules"
        :master-state="masterState"
        :restarting="restarting"
        @toggle-module="toggleModule"
        @set-all="setAll"
        @open-drawer="openDrawer"
        @restart-session="refreshCurrentSession"
      />
    </div>

    <EvolutionDrawer :open="drawerOpen" @close="closeDrawer" />
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
  display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 16px; padding: 16px;
  min-height: 0;
}
</style>
