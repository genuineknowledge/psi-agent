<template>
  <div class="col">
    <div class="col-header">
      会话
      <button @click="$emit('new-session')">
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
</template>

<script setup>
import { store } from '../store.js'
import { api } from '../api.js'
import { saveActiveState, loadHistory, clearHistory } from '../utils.js'
import { renderMd, htmlEscape } from '../utils.js'

defineEmits(['new-session'])

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
  } catch (e) {
    // silent
  }
}

function confirmDeleteSession(id) {
  store.dlgConfirm.message = `确认删除会话 ${id}? 删除后将无法恢复。`
  store.dlgConfirm.actionType = 'session'
  store.dlgConfirm.actionArgs = id
  store.dlgConfirm.show = true
}

async function selectSession(id) {
  if (store.editingSessionId === id) return
  store.selectedSessionId = id
  store.messages.splice(0)
  const localHist = loadHistory(id)
  try {
    const r = await fetch(window.location.origin.replace(/\/+$/, '') + '/sessions/' + id + '/history')
    if (r.ok) {
      const serverMsgs = await r.json()
      serverMsgs.forEach(h => {
        const local = localHist.find(l => l.role === h.role && l.text === h.text)
        store.messages.push({
          id: '',
          role: h.role,
          text: h.text,
          html: h.role === 'user' ? htmlEscape(h.text) : renderMd(h.text),
          files: local ? local.files || [] : [],
        })
      })
    } else {
      localHist.forEach(h => {
        store.messages.push({
          id: '',
          role: h.role,
          text: h.text,
          html: h.role === 'user' ? htmlEscape(h.text) : renderMd(h.text),
          files: h.files || [],
        })
      })
    }
  } catch (e) {
    localHist.forEach(h => {
      store.messages.push({
        id: '',
        role: h.role,
        text: h.text,
        html: h.role === 'user' ? htmlEscape(h.text) : renderMd(h.text),
        files: h.files || [],
      })
    })
  }
  const currentSess = store.sessions.find(s => s.id === id)
  if (currentSess) store.selectedAiId = currentSess.ai_id
  saveActiveState(store.selectedAiId, store.selectedSessionId)

  store.userHasScrolledUp = false
  store.isMobileSidebarOpen = false
}
</script>
