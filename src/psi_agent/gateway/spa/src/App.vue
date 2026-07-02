<template>
  <div id="root-layout">
    <div v-if="store.loadingEnv" class="page-loader">
      <div class="spinner"></div>
      <p>Initializing System Environment...</p>
    </div>

    <div class="mobile-overlay" :class="{ active: store.isMobileSidebarOpen }" @click="closeMobileSidebar"></div>

    <Sidebar @new-session="openSessDialog" />

    <div
      id="chat"
      @dragenter.prevent="onChatDragOver"
      @dragover.prevent="onChatDragOver"
      @dragleave="onChatDragLeave"
      @drop="onChatDrop"
    >
      <div v-if="store.isDragging" class="drop-overlay">
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
          <button class="topbar-btn" @click="toggleTheme" :title="store.isLightMode ? '切换至暗色模式' : '切换至亮色模式'">
            <span class="material-symbols-outlined">{{ store.isLightMode ? 'dark_mode' : 'light_mode' }}</span>
          </button>
        </div>
      </div>

      <button class="sidebar-toggle-btn" @click="toggleSidebar" :title="store.isSidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'">
        <span class="material-symbols-outlined">{{ (store.isSidebarCollapsed && !store.isMobileSidebarOpen) ? 'menu_open' : 'menu' }}</span>
      </button>

      <button class="theme-toggle-btn" @click="toggleTheme" :title="store.isLightMode ? '切换至暗色模式' : '切换至亮色模式'">
        <span class="material-symbols-outlined">{{ store.isLightMode ? 'dark_mode' : 'light_mode' }}</span>
      </button>

      <ChatArea />

      <InputBar
        @select-ai="selectAI"
        @delete-ai="confirmDeleteAI"
        @new-ai="openAiDialog"
      />
    </div>

    <AiDialog @create="createAI" @fetchModels="fetchAvailableModels" />
    <SessDialog @create="createSession" @browse="browseWorkspace" />
    <ConfirmDialog @confirm="executeConfirmedAction" />
    <Snackbar />
  </div>
</template>

<script setup>
import { computed, onMounted, watch } from 'vue'
import { store } from './store.js'
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
import { undoFrom } from './composables/useChat.js'
import Sidebar from './components/Sidebar.vue'
import ChatArea from './components/ChatArea.vue'
import InputBar from './components/InputBar.vue'
import AiDialog from './components/AiDialog.vue'
import SessDialog from './components/SessDialog.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import Snackbar from './components/Snackbar.vue'

const LS_SIDEBAR = 'gw-sidebar-state'

const { toggleTheme } = useTheme()
useKeyboard()

function origin() {
  return window.location.origin.replace(/\/+$/, '')
}

function showAlert(msg) {
  store.snackbar.message = msg
  store.snackbar.show = true
  setTimeout(() => {
    if (store.snackbar.message === msg) store.snackbar.show = false
  }, 4000)
}

function toggleSidebar() {
  const isMobile = window.innerWidth <= 768
  if (isMobile) {
    store.isMobileSidebarOpen = !store.isMobileSidebarOpen
  } else {
    store.isSidebarCollapsed = !store.isSidebarCollapsed
  }
}

function closeMobileSidebar() {
  store.isMobileSidebarOpen = false
}

function onChatDragOver(e) {
  if (!e.dataTransfer || !Array.from(e.dataTransfer.types).includes('Files')) return
  e.preventDefault()
  if (store.selectedSessionId) store.isDragging = true
}

function onChatDragLeave(e) {
  // Only clear when the pointer actually leaves the #chat element,
  // not when moving between its children.
  if (e.currentTarget.contains(e.relatedTarget)) return
  store.isDragging = false
}

function onChatDrop(e) {
  if (!e.dataTransfer || !Array.from(e.dataTransfer.types).includes('Files')) return
  e.preventDefault()
  store.isDragging = false
  if (!store.selectedSessionId) return
  const files = Array.from(e.dataTransfer.files || [])
  if (files.length) store.selectedFiles.push(...files)
}

async function refreshAIs() {
  try {
    store.ais = await api('GET', '/ais')
  } catch (e) {
    store.ais = []
  }
}

async function refreshSessions() {
  try {
    store.sessions = await api('GET', '/sessions')
  } catch (e) {
    store.sessions = []
  }
}

async function refreshAll() {
  await refreshAIs()
  await refreshSessions()
}

function confirmDeleteAI(id) {
  const ai = store.ais.find(a => a.id === id)
  const name = ai ? (ai.model || ai.id) : id
  store.dlgConfirm.message = `确认删除大模型「${name}」? 相关会话数据将保留，但该模型链接将无法使用。`
  store.dlgConfirm.actionType = 'ai'
  store.dlgConfirm.actionArgs = id
  store.dlgConfirm.show = true
}

async function deleteAI(id) {
  await api('DELETE', '/ais/' + id).catch(() => {})
  if (store.selectedAiId === id) {
    store.selectedAiId = null
    saveActiveState(null, store.selectedSessionId)
  }
  await refreshAll()
}


async function executeConfirmedAction() {
  store.dlgConfirm.show = false

  // 撤回：actionArgs 是消息索引（可能为 0），需在 !id 判空之前处理
  if (store.dlgConfirm.actionType === 'undo') {
    undoFrom(store.dlgConfirm.actionArgs)
    return
  }

  const id = store.dlgConfirm.actionArgs
  if (!id) return

  if (store.dlgConfirm.actionType === 'ai') {
    await deleteAI(id)
    return
  }

  await api('DELETE', '/sessions/' + id).catch(() => {})
  clearHistory(id)
  if (id === store.selectedSessionId) {
    store.selectedSessionId = null
    store.messages.splice(0)
  }
  saveActiveState(store.selectedAiId, store.selectedSessionId)
  await refreshAll()
}

function handleProviderChange() {
  const match = PROVIDERS.find(p => p.v === store.aiForm.provider)
  if (match) store.aiForm.base_url = match.base
}

const currentSessionTitle = computed(() => {
  if (!store.selectedSessionId) return 'psi-agent'
  const sess = store.sessions.find(s => s.id === store.selectedSessionId)
  if (!sess) return 'psi-agent'
  return store.sessionTitles[store.selectedSessionId] || sess.workspace || '新会话'
})


async function fetchAvailableModels() {
  if (!store.aiForm.api_key || !store.aiForm.base_url) {
    store.fetchedModels = []
    return
  }
  store.loadingModels = true
  try {
    const headers = { Authorization: `Bearer ${store.aiForm.api_key}` }
    if (store.aiForm.provider === 'anthropic') headers['x-api-key'] = store.aiForm.api_key
    const url = `${store.aiForm.base_url.replace(/\/+$/, '')}/models`
    const res = await fetch(url, { method: 'GET', headers }).then(r => r.json())
    if (res && Array.isArray(res.data)) store.fetchedModels = res.data.map(m => m.id)
    else if (res && Array.isArray(res.models)) store.fetchedModels = res.models.map(m => m.name || m.id)
    else store.fetchedModels = []
  } catch (e) {
    store.fetchedModels = []
  } finally {
    store.loadingModels = false
  }
}

function openAiDialog() {
  store.aiForm = { provider: 'deepseek', base_url: 'https://api.deepseek.com/v1', api_key: '', model: '' }
  store.fetchedModels = []
  store.dlgAI = true
}

function openSessDialog() {
  if (!store.ais.length) {
    showAlert('请先配置大模型')
    openAiDialog()
    return
  }
  store.sessForm = { workspace: '' }
  store.browser = { path: undefined, parent: '', entries: [] }
  store.dlgSess = true
}

async function browseWorkspace(p) {
  if (p === undefined && store.browser.path !== undefined) {
    store.browser = { path: undefined, parent: '', entries: [] }
    return
  }
  const r = await fetch(origin() + '/workspace/browse?path=' + encodeURIComponent(p || store.sessForm.workspace || ''))
  if (r.ok) store.browser = await r.json()
}

async function selectAI(id) {
  if (id === store.selectedAiId) return
  store.selectedAiId = id
  if (store.selectedSessionId && store.sessions.find(s => s.id === store.selectedSessionId)) {
    const s = store.sessions.find(s => s.id === store.selectedSessionId)
    await api('DELETE', '/sessions/' + store.selectedSessionId).catch(() => {})
    await api('POST', '/sessions', { id: store.selectedSessionId, ai_id: id, workspace: s.workspace })
  } else {
    store.selectedSessionId = null
  }
  saveActiveState(store.selectedAiId, store.selectedSessionId)
  await refreshAll()
}

async function createAI() {
  if (!store.aiForm.model) {
    showAlert('请选择或输入模型名称')
    return
  }
  try {
    const info = await api('POST', '/ais', {
      provider: store.aiForm.provider,
      model: store.aiForm.model,
      api_key: store.aiForm.api_key,
      base_url: store.aiForm.base_url,
    })
    store.selectedAiId = info.id
    store.dlgAI = false
    await refreshAll()
    store.loadingEnv = false
    saveActiveState(store.selectedAiId, store.selectedSessionId)
    if (store.sessions.length === 0) openSessDialog()
  } catch (e) {
    showAlert(e.message)
  }
}

async function createSession() {
  if (!store.selectedAiId) {
    showAlert('请先选择一个大模型代理')
    return
  }
  try {
    const info = await api('POST', '/sessions', { ai_id: store.selectedAiId, workspace: store.sessForm.workspace })
    store.selectedSessionId = info.id
    store.dlgSess = false
    store.messages.splice(0)
    saveActiveState(store.selectedAiId, store.selectedSessionId)
    await refreshAll()
  } catch (e) {
    showAlert(e.message)
  }
}

watch(
  () => store.isSidebarCollapsed,
  (v) => {
    localStorage.setItem(LS_SIDEBAR, v ? 'collapsed' : 'expanded')
  }
)

onMounted(async () => {
  store.sessionTitles = await api('GET', '/titles').catch(() => ({}))
  const savedSidebar = localStorage.getItem(LS_SIDEBAR)
  if (savedSidebar === 'collapsed') store.isSidebarCollapsed = true

  try {
    await refreshAll()

    if (store.ais.length === 0) {
      openAiDialog()
      store.loadingEnv = false
      return
    }

    const activeState = loadActiveState()
    if (activeState.aiId && store.ais.some(a => a.id === activeState.aiId))
      store.selectedAiId = activeState.aiId
    if (activeState.sessId && store.sessions.some(s => s.id === activeState.sessId))
      selectSession(activeState.sessId)
    if (!store.selectedAiId && store.ais.length) store.selectedAiId = store.ais[0].id
    if (!store.selectedSessionId && store.sessions.length) selectSession(store.sessions[0].id)
    store.loadingEnv = false
  } catch (err) {
    store.loadingEnv = false
  }
})
</script>
