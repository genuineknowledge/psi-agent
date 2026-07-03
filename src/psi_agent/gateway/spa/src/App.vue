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
      @dragenter.prevent="onChatDragOver"
      @dragover.prevent="onChatDragOver"
      @dragleave="onChatDragLeave"
      @drop="onChatDrop"
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

      <button class="sidebar-toggle-btn" @click="toggleSidebar" :title="isSidebarCollapsed ? '展开侧边栏' : '折叠侧边栏'">
        <span class="material-symbols-outlined">{{ (isSidebarCollapsed && !isMobileSidebarOpen) ? 'menu' : 'menu_open' }}</span>
      </button>

      <button class="theme-toggle-btn" @click="toggleTheme" :title="isLightMode ? '切换至暗色模式' : '切换至亮色模式'">
        <span class="material-symbols-outlined">{{ isLightMode ? 'dark_mode' : 'light_mode' }}</span>
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
import { storeToRefs } from 'pinia'
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
import { undoFrom } from './composables/useChat.js'
import Sidebar from './components/Sidebar.vue'
import ChatArea from './components/ChatArea.vue'
import InputBar from './components/InputBar.vue'
import AiDialog from './components/AiDialog.vue'
import SessDialog from './components/SessDialog.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import Snackbar from './components/Snackbar.vue'

const LS_SIDEBAR = 'gw-sidebar-state'

const ai = useAiStore()
const { ais, selectedAiId, aiForm, fetchedModels, loadingModels } = storeToRefs(ai)

const session = useSessionStore()
const { sessions, selectedSessionId, sessionTitles, sessForm, browser } = storeToRefs(session)

const chat = useChatStore()
const { messages, selectedFiles } = storeToRefs(chat)

const ui = useUiStore()
const { loadingEnv, isLightMode, isDragging, dlgAI, dlgSess, dlgConfirm, isSidebarCollapsed, isMobileSidebarOpen } = storeToRefs(ui)

const { toggleTheme } = useTheme()
useKeyboard()

function toggleSidebar() {
  ui.toggleSidebar(window.innerWidth <= 768)
}

function origin() {
  return window.location.origin.replace(/\/+$/, '')
}

function onChatDragOver(e) {
  if (!e.dataTransfer || !Array.from(e.dataTransfer.types).includes('Files')) return
  e.preventDefault()
  if (selectedSessionId.value) isDragging.value = true
}

function onChatDragLeave(e) {
  // Only clear when the pointer actually leaves the #chat element,
  // not when moving between its children.
  if (e.currentTarget.contains(e.relatedTarget)) return
  isDragging.value = false
}

function onChatDrop(e) {
  if (!e.dataTransfer || !Array.from(e.dataTransfer.types).includes('Files')) return
  e.preventDefault()
  isDragging.value = false
  if (!selectedSessionId.value) return
  const files = Array.from(e.dataTransfer.files || [])
  if (files.length) selectedFiles.value.push(...files)
}

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

  // 撤回：actionArgs 是消息索引（可能为 0），需在 !id 判空之前处理
  if (dlgConfirm.value.actionType === 'undo') {
    undoFrom(dlgConfirm.value.actionArgs)
    return
  }

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

watch(
  isSidebarCollapsed,
  (v) => {
    localStorage.setItem(LS_SIDEBAR, v ? 'collapsed' : 'expanded')
  }
)

onMounted(async () => {
  sessionTitles.value = await api('GET', '/titles').catch(() => ({}))
  const savedSidebar = localStorage.getItem(LS_SIDEBAR)
  if (savedSidebar === 'collapsed') isSidebarCollapsed.value = true

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
