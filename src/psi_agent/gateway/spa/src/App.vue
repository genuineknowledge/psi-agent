<template>
  <div id="root-layout">
    <div v-if="loadingEnv" class="page-loader">
      <div class="spinner"></div>
      <p>Initializing System Environment...</p>
    </div>

    <div class="mobile-overlay" :class="{ active: isMobileSidebarOpen }" @click="ui.closeMobileSidebar"></div>

    <Sidebar @new-session="openSessDialog" />

    <div
      id="chat"
      ref="chatDropRef"
    >
      <div v-if="isDragging" class="drop-overlay">
        <div class="drop-overlay-inner">
          <span class="material-symbols-outlined">upload_file</span>
          <span>拖放文件以上传</span>
        </div>
      </div>
      <div id="mobile-topbar">
        <div class="topbar-left">
          <button class="topbar-btn" @click="toggleSidebar" title="打开会话列表">
            <span class="material-symbols-outlined">menu</span>
          </button>
        </div>
        <div class="topbar-title">{{ currentSessionTitle }}</div>
        <div class="topbar-right">
          <button class="topbar-btn" @click="toggleTheme" :title="isLightMode ? '切换至暗色模式' : '切换至亮色模式'">
            <span class="material-symbols-outlined">{{ isLightMode ? 'dark_mode' : 'light_mode' }}</span>
          </button>
        </div>
      </div>

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
    </div>

    <AiDialog @create="createAI" @fetchModels="fetchAvailableModels" />
    <SessDialog @create="createSession" @browse="browseWorkspace" />
    <ConfirmDialog @confirm="executeConfirmedAction" />
    <Snackbar />
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useBreakpoints, useDropZone, useStorage } from '@vueuse/core'
import { useAiStore } from './stores/ai.js'
import { useSessionStore } from './stores/session.js'
import { useChatStore } from './stores/chat.js'
import { useUiStore } from './stores/ui.js'
import { api } from './api.js'
import {
  saveActiveState,
  loadActiveState,
  clearHistory,
} from './utils.js'
import { PROVIDERS } from './providers.js'
import { useTheme } from './composables/useTheme.js'
import { useKeyboard } from './composables/useKeyboard.js'
import { selectSession } from './composables/useSession.js'
import Sidebar from './components/Sidebar.vue'
import ChatArea from './components/ChatArea.vue'
import InputBar from './components/InputBar.vue'
import AiDialog from './components/AiDialog.vue'
import SessDialog from './components/SessDialog.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import Snackbar from './components/Snackbar.vue'

const LS_SIDEBAR = 'gw-sidebar-state'
const sidebarState = useStorage(LS_SIDEBAR, 'expanded')

const ai = useAiStore()
const { ais, selectedAiId, aiForm, fetchedModels, loadingModels } = storeToRefs(ai)

const session = useSessionStore()
const { sessions, selectedSessionId, sessionTitles, sessForm, browser } = storeToRefs(session)

const chat = useChatStore()
const { messages, selectedFiles } = storeToRefs(chat)
const showWelcome = computed(() => messages.value.length === 0)

const ui = useUiStore()
const { loadingEnv, isLightMode, isDragging, dlgAI, dlgSess, dlgConfirm, isSidebarCollapsed, isMobileSidebarOpen } = storeToRefs(ui)

const { toggleTheme } = useTheme()
useKeyboard()

const breakpoints = useBreakpoints({ mobile: 768 })
const isMobile = breakpoints.smallerOrEqual('mobile')

function toggleSidebar() {
  ui.toggleSidebar(isMobile.value)
}

function origin() {
  return window.location.origin.replace(/\/+$/, '')
}

const chatDropRef = ref(null)
const { isOverDropZone } = useDropZone(chatDropRef, {
  onDrop: (files) => {
    if (!selectedSessionId.value) return
    if (files && files.length) selectedFiles.value.push(...files)
  },
})
watch(isOverDropZone, (over) => {
  isDragging.value = over && !!selectedSessionId.value
})

async function refreshAIs() {
  try {
    ais.value = await api('GET', '/ais')
  } catch (e) {
    ais.value = []
  }
}

async function refreshSessions() {
  try {
    sessions.value = await api('GET', '/sessions')
  } catch (e) {
    sessions.value = []
  }
}

async function refreshAll() {
  await refreshAIs()
  await refreshSessions()
}

function confirmDeleteAI(id) {
  const a = ais.value.find(a => a.id === id)
  const name = a ? (a.model || a.id) : id
  dlgConfirm.value.message = `确认删除大模型「${name}」? 相关会话数据将保留，但该模型链接将无法使用。`
  dlgConfirm.value.actionType = 'ai'
  dlgConfirm.value.actionArgs = id
  dlgConfirm.value.show = true
}

async function deleteAI(id) {
  await api('DELETE', '/ais/' + id).catch(() => {})
  if (selectedAiId.value === id) {
    selectedAiId.value = null
    saveActiveState(null, selectedSessionId.value)
  }
  await refreshAll()
}


async function executeConfirmedAction() {
  dlgConfirm.value.show = false

  const id = dlgConfirm.value.actionArgs
  if (!id) return

  if (dlgConfirm.value.actionType === 'ai') {
    await deleteAI(id)
    return
  }

  await api('DELETE', '/sessions/' + id).catch(() => {})
  clearHistory(id)
  if (id === selectedSessionId.value) {
    selectedSessionId.value = null
    messages.value.splice(0)
  }
  saveActiveState(selectedAiId.value, selectedSessionId.value)
  await refreshAll()
}

function handleProviderChange() {
  const match = PROVIDERS.find(p => p.v === aiForm.value.provider)
  if (match) aiForm.value.base_url = match.base
}

const currentSessionTitle = computed(() => {
  if (!selectedSessionId.value) return 'psi-agent'
  const sess = sessions.value.find(s => s.id === selectedSessionId.value)
  if (!sess) return 'psi-agent'
  return sessionTitles.value[selectedSessionId.value] || sess.workspace || '新会话'
})


async function fetchAvailableModels() {
  if (!aiForm.value.api_key || !aiForm.value.base_url) {
    fetchedModels.value = []
    return
  }
  loadingModels.value = true
  try {
    const headers = { Authorization: `Bearer ${aiForm.value.api_key}` }
    if (aiForm.value.provider === 'anthropic') headers['x-api-key'] = aiForm.value.api_key
    const url = `${aiForm.value.base_url.replace(/\/+$/, '')}/models`
    const res = await fetch(url, { method: 'GET', headers }).then(r => r.json())
    if (res && Array.isArray(res.data)) fetchedModels.value = res.data.map(m => m.id)
    else if (res && Array.isArray(res.models)) fetchedModels.value = res.models.map(m => m.name || m.id)
    else fetchedModels.value = []
  } catch (e) {
    fetchedModels.value = []
  } finally {
    loadingModels.value = false
  }
}

function openAiDialog() {
  aiForm.value = { provider: 'deepseek', base_url: 'https://api.deepseek.com/v1', api_key: '', model: '' }
  fetchedModels.value = []
  dlgAI.value = true
}

function openSessDialog() {
  if (!ais.value.length) {
    ui.showAlert('请先配置大模型')
    openAiDialog()
    return
  }
  sessForm.value = { workspace: '' }
  browser.value = { path: undefined, parent: '', entries: [] }
  dlgSess.value = true
}

async function browseWorkspace(p) {
  if (p === undefined && browser.value.path !== undefined) {
    browser.value = { path: undefined, parent: '', entries: [] }
    return
  }
  const r = await fetch(origin() + '/workspace/browse?path=' + encodeURIComponent(p || sessForm.value.workspace || ''))
  if (r.ok) browser.value = await r.json()
}

async function selectAI(id) {
  if (id === selectedAiId.value) return
  selectedAiId.value = id
  if (selectedSessionId.value && sessions.value.find(s => s.id === selectedSessionId.value)) {
    const s = sessions.value.find(s => s.id === selectedSessionId.value)
    await api('DELETE', '/sessions/' + selectedSessionId.value).catch(() => {})
    await api('POST', '/sessions', { id: selectedSessionId.value, ai_id: id, workspace: s.workspace })
  } else {
    selectedSessionId.value = null
  }
  saveActiveState(selectedAiId.value, selectedSessionId.value)
  await refreshAll()
}

async function createAI() {
  if (!aiForm.value.model) {
    ui.showAlert('请选择或输入模型名称')
    return
  }
  try {
    const info = await api('POST', '/ais', {
      provider: aiForm.value.provider,
      model: aiForm.value.model,
      api_key: aiForm.value.api_key,
      base_url: aiForm.value.base_url,
    })
    selectedAiId.value = info.id
    dlgAI.value = false
    await refreshAll()
    loadingEnv.value = false
    saveActiveState(selectedAiId.value, selectedSessionId.value)
    if (sessions.value.length === 0) openSessDialog()
  } catch (e) {
    ui.showAlert(e.message)
  }
}

async function createSession() {
  if (!selectedAiId.value) {
    ui.showAlert('请先选择一个大模型代理')
    return
  }
  try {
    const info = await api('POST', '/sessions', { ai_id: selectedAiId.value, workspace: sessForm.value.workspace })
    selectedSessionId.value = info.id
    dlgSess.value = false
    messages.value.splice(0)
    saveActiveState(selectedAiId.value, selectedSessionId.value)
    await refreshAll()
  } catch (e) {
    ui.showAlert(e.message)
  }
}

if (sidebarState.value === 'collapsed') isSidebarCollapsed.value = true

watch(
  isSidebarCollapsed,
  (v) => {
    sidebarState.value = v ? 'collapsed' : 'expanded'
  }
)

onMounted(async () => {
  sessionTitles.value = await api('GET', '/titles').catch(() => ({}))

  try {
    await refreshAll()

    if (ais.value.length === 0) {
      openAiDialog()
      loadingEnv.value = false
      return
    }

    const activeState = loadActiveState()
    if (activeState.aiId && ais.value.some(a => a.id === activeState.aiId))
      selectedAiId.value = activeState.aiId
    if (activeState.sessId && sessions.value.some(s => s.id === activeState.sessId))
      selectSession(activeState.sessId)
    if (!selectedAiId.value && ais.value.length) selectedAiId.value = ais.value[0].id
    if (!selectedSessionId.value && sessions.value.length) selectSession(sessions.value[0].id)
    loadingEnv.value = false
  } catch (err) {
    loadingEnv.value = false
  }
})
</script>

<style scoped>
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
#chat-main.welcome :deep(#input-wrapper) { padding-bottom: 0; width: 100%; }
@media (max-width: 768px) {
  .welcome-greeting { font-size: 34px; }
}
</style>
