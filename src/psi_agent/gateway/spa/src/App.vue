<template>
  <div id="root-layout">
    <div v-if="store.loadingEnv" class="page-loader">
      <div class="spinner"></div>
      <p>Initializing System Environment...</p>
    </div>

    <div class="mobile-overlay" :class="{ active: store.isMobileSidebarOpen }" @click="closeMobileSidebar"></div>

    <div id="sidebar" :class="{ collapsed: store.isSidebarCollapsed, 'mobile-open': store.isMobileSidebarOpen }">
      <div class="col">
        <div class="col-header">
          会话
          <button @click="openSessDialog()">
            <span class="material-symbols-outlined">add</span>
            新建
          </button>
        </div>
        <div
          v-for="s in store.sessions"
          :key="s.id"
          class="item"
          :class="{ selected: s.id === store.selectedSessionId }"
          @click="selectSession(s.id)"
        >
          <span class="info">
            <input
              v-if="store.editingSessionId === s.id"
              v-model="store.editingWorkspaceText"
              class="edit-input"
              v-focus
              @blur="saveSessionWorkspace(s)"
              @keydown.enter="saveSessionWorkspace(s)"
              @click.stop
            >
            <div
              v-else
              class="name"
              :title="getSessionDisplayName(s)"
              @dblclick.stop="startEditWorkspace(s)"
            >
              {{ getSessionDisplayName(s) }}
            </div>
          </span>
          <button class="del" @click.stop="confirmDeleteSession(s.id)">
            <span class="material-symbols-outlined">delete</span>
          </button>
        </div>
      </div>
    </div>

    <div id="chat">
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

      <div id="input-wrapper" v-show="store.selectedSessionId">
        <div id="file-preview-bar" v-if="store.selectedFile">
          <div class="preview-chip">
            <span class="material-symbols-outlined" style="font-size:16px;">attach_file</span>
            <span>{{ store.selectedFile.name }}</span>
            <button class="close-btn" @click="clearSelectedFile" title="移除附件">
              <span class="material-symbols-outlined" style="font-size:16px;">close</span>
            </button>
          </div>
        </div>

        <div id="input-area">
          <label class="btn" for="file-upload"><span class="material-symbols-outlined">attach_file</span></label>
          <input type="file" id="file-upload" @change="onFileSelected">

          <textarea
            v-model="store.inputText"
            rows="1"
            placeholder="发送消息..."
            @keydown.enter.exact.prevent="sendMessage"
          ></textarea>

          <div class="model-zone">
            <div v-if="store.modelPanelOpen" class="model-panel-backdrop" @click="store.modelPanelOpen = false"></div>

            <div class="model-chip" :class="{ open: store.modelPanelOpen }" @click="store.modelPanelOpen = !store.modelPanelOpen" :title="currentModelLabel">
              <span class="material-symbols-outlined chip-icon">smart_toy</span>
              <span class="chip-label">{{ currentModelLabel }}</span>
              <span class="material-symbols-outlined chip-arrow">expand_more</span>
            </div>

            <div v-if="store.modelPanelOpen" class="model-panel">
              <div class="model-panel-header">
                <span>大模型</span>
                <button @click="store.modelPanelOpen = false; openAiDialog()">
                  <span class="material-symbols-outlined">add</span>链接新模型
                </button>
              </div>
              <div class="model-panel-list">
                <div v-if="store.ais.length === 0" class="model-panel-empty">暂无模型，请点击「链接新模型」</div>
                <div
                  v-for="a in store.ais"
                  :key="a.id"
                  class="model-panel-item"
                  :class="{ active: a.id === store.selectedAiId }"
                  @click="selectAI(a.id); store.modelPanelOpen = false"
                >
                  <span class="material-symbols-outlined mpi-icon">smart_toy</span>
                  <div class="mpi-info">
                    <div class="mpi-name" :title="a.model || a.id">{{ a.model || a.id }}</div>
                    <div class="mpi-provider">{{ a.provider }}</div>
                  </div>
                  <span v-if="a.id === store.selectedAiId" class="material-symbols-outlined mpi-check">check_circle</span>
                  <button class="mpi-del" @click.stop="store.modelPanelOpen = false; confirmDeleteAI(a.id)" title="删除此模型">
                    <span class="material-symbols-outlined">delete</span>
                  </button>
                </div>
              </div>
            </div>
          </div>

          <button class="send" :disabled="store.streaming" @click="sendMessage">
            <span class="material-symbols-outlined">send</span>
          </button>
        </div>
      </div>
    </div>

    <AiDialog @create="createAI" @fetchModels="fetchAvailableModels" />
    <SessDialog @create="createSession" @browse="browseWorkspace" />
    <ConfirmDialog @confirm="executeConfirmedAction" />
    <Snackbar />
  </div>
</template>

<script setup>
import { computed, onMounted, watch, nextTick } from 'vue'
import { store } from './store.js'
import { api } from './api.js'
import {
  renderMd,
  htmlEscape,
  saveActiveState,
  loadActiveState,
  loadHistory,
  saveHistory,
  clearHistory,
} from './utils.js'
import { PROVIDERS } from './providers.js'
import { useTheme } from './composables/useTheme.js'
import { useKeyboard } from './composables/useKeyboard.js'
import { readSSE } from './composables/useSSE.js'
import ChatArea from './components/ChatArea.vue'
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

function confirmDeleteSession(id) {
  store.dlgConfirm.message = `确认删除会话 ${id}? 删除后将无法恢复。`
  store.dlgConfirm.actionType = 'session'
  store.dlgConfirm.actionArgs = id
  store.dlgConfirm.show = true
}

async function executeConfirmedAction() {
  store.dlgConfirm.show = false
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

const currentModelLabel = computed(() => {
  if (!store.selectedAiId) return '选择模型'
  const ai = store.ais.find(a => a.id === store.selectedAiId)
  return ai ? (ai.model || ai.id) : '选择模型'
})

const currentSessionTitle = computed(() => {
  if (!store.selectedSessionId) return 'psi-agent'
  const sess = store.sessions.find(s => s.id === store.selectedSessionId)
  if (!sess) return 'psi-agent'
  return store.sessionTitles[store.selectedSessionId] || sess.workspace || '新会话'
})

function getSessionDisplayName(session) {
  if (store.sessionTitles && store.sessionTitles[session.id]) {
    return store.sessionTitles[session.id]
  }
  return session.workspace || '新会话'
}

function startEditWorkspace(session) {
  store.editingSessionId = session.id
  store.editingWorkspaceText = getSessionDisplayName(session)
}

async function saveSessionWorkspace(session) {
  if (store.editingSessionId === null) return
  const targetText = store.editingWorkspaceText.trim()
  const oldText = getSessionDisplayName(session)
  store.editingSessionId = null

  if (!targetText || targetText === oldText) {
    return
  }
  try {
    await api('DELETE', '/sessions/' + session.id).catch(() => {})
    await api('POST', '/sessions', {
      id: session.id,
      ai_id: session.ai_id,
      workspace: session.workspace,
    })
    store.sessionTitles[session.id] = targetText
    api('POST', '/titles', { id: session.id, title: targetText }).catch(() => {})
    await refreshSessions()
  } catch (e) {
    showAlert('更新会话名称失败: ' + e.message)
  }
}

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

async function selectSession(id) {
  if (store.editingSessionId === id) return
  const oldId = store.selectedSessionId
  if (oldId) {
    store.sessionMessages[oldId] = [...store.messages]
    store.sessionInputs[oldId] = { text: store.inputText, file: store.selectedFile }
  }
  store.selectedSessionId = id

  const saved = store.sessionInputs[id]
  store.inputText = saved ? saved.text : ''
  store.selectedFile = saved ? saved.file : null

  if (store.sessionMessages[id]) {
    store.messages.splice(0, store.messages.length, ...store.sessionMessages[id])
  } else {
    store.messages.splice(0)
    const localHist = loadHistory(id)
    try {
      const r = await fetch(origin() + '/sessions/' + id + '/history')
      if (r.ok) {
        const serverMsgs = await r.json()
        serverMsgs.forEach((h, i) => {
          const local = i < localHist.length ? localHist[i] : null
          store.messages.push({
            id: '', role: h.role, text: h.text,
            html: h.role === 'user' ? htmlEscape(h.text) : renderMd(h.text),
            files: local ? local.files || [] : [],
          })
        })
      } else { throw new Error() }
    } catch (e) {
      localHist.forEach(h => {
        store.messages.push({ id: '', role: h.role, text: h.text, html: h.role === 'user' ? htmlEscape(h.text) : renderMd(h.text), files: h.files || [] })
      })
    }
  }

  const currentSess = store.sessions.find(s => s.id === id)
  if (currentSess) store.selectedAiId = currentSess.ai_id
  saveActiveState(store.selectedAiId, store.selectedSessionId)

  store.userHasScrolledUp = false
  store.isMobileSidebarOpen = false
  nextTick(() => {
    const el = document.getElementById('messages')
    if (el) el.scrollTop = el.scrollHeight
  })
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

function onFileSelected(e) {
  store.selectedFile = e.target.files[0] || null
}

function clearSelectedFile() {
  store.selectedFile = null
  document.getElementById('file-upload').value = ''
}

function addMessage(role, id) {
  const m = { id, role, text: '', html: '', files: [] }
  store.messages.push(m)
  scrollChatAreaIfLocked()
  return store.messages[store.messages.length - 1]
}

function scrollChatAreaIfLocked() {
  nextTick(() => {
    const el = document.getElementById('messages')
    if (!el) return
    const distanceFromBottom = el.scrollHeight - el.clientHeight - el.scrollTop
    if (store.streaming) {
      if (store.userHasScrolledUp && distanceFromBottom > 60) return
      if (distanceFromBottom <= 60) store.userHasScrolledUp = false
    }
    el.scrollTop = el.scrollHeight
  })
}

async function sendMessage() {
  if (store.streaming || !store.selectedSessionId) return
  const text = store.inputText.trim()
  const file = store.selectedFile
  if (!text && !file) return

  store.streaming = true
  store.inputText = ''
  store.selectedFile = null
  document.getElementById('file-upload').value = ''
  store.userHasScrolledUp = false

  let clientBase64 = ''
  if (file) {
    try {
      clientBase64 = await new Promise((resolve, reject) => {
        const r = new FileReader()
        r.onload = () => resolve(r.result.split(',')[1])
        r.onerror = e => reject(e)
        r.readAsDataURL(file)
      })
    } catch (fe) {}
  }

  if (text) {
    const um = addMessage('user', `u-${Date.now()}`)
    um.text = text
    um.html = htmlEscape(text)
    if (file && clientBase64) um.files.push({ name: file.name, data: clientBase64 })
  } else if (file && clientBase64) {
    const um = addMessage('user', `u-${Date.now()}`)
    um.text = `[Uploaded File: ${file.name}]`
    um.html = htmlEscape(`[Uploaded File: ${file.name}]`)
    um.files.push({ name: file.name, data: clientBase64 })
  }

  const fd = new FormData()
  const chunks = []
  if (text) chunks.push({ type: 'text', text })
  fd.append('chunks', JSON.stringify(chunks))
  if (file) fd.append('file', file)

  const asst = addMessage('assistant', `a-${Date.now()}`)

  try {
    const r = await fetch(origin() + '/sessions/' + store.selectedSessionId + '/chat', { method: 'POST', body: fd })
    if (!r.ok) {
      const e = await r.json().catch(() => ({ error: r.statusText }))
      throw new Error(e.error || 'HTTP ' + r.status)
    }

    const reader = r.body.getReader()
    for await (const chunkData of readSSE(reader)) {
      if (chunkData.type === 'text' && chunkData.text !== undefined) {
        asst.text += chunkData.text
        asst.html = renderMd(asst.text)
      } else if (chunkData.type === 'blob') {
        asst.files.push({ name: chunkData.name, data: chunkData.data })
      } else if (chunkData.type === 'error') {
        asst.text += '\n[Error: ' + chunkData.error + ']'
      }
      scrollChatAreaIfLocked()
    }
  } catch (e) {
    asst.text += '\n[Error: ' + e.message + ']'
    asst.html = renderMd(asst.text)
  }

  store.streaming = false
  saveHistory(store.selectedSessionId, store.messages)

  const currentTitle = store.sessionTitles[store.selectedSessionId]
  if (!currentTitle || currentTitle === '新会话' || currentTitle.trim() === '') generateTitle()
}

async function generateTitle() {
  const sid = store.selectedSessionId
  if (!sid) return
  const msgs = loadHistory(sid)
  if (!msgs.length) return
  const userMsg = msgs.find(m => m.role === 'user')
  const asstMsg = msgs.find(m => m.role === 'assistant')
  if (!userMsg || !asstMsg) return

  try {
    const r = await fetch(origin() + '/titles/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: sid, user_text: userMsg.text, assistant_text: asstMsg.text }),
    })
    if (!r.ok) return
    const data = await r.json()
    if (data.title) {
      store.sessionTitles[sid] = data.title
    }
  } catch (e) {}
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
