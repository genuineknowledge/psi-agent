<template>
  <div id="sidebar" :class="{ collapsed: store.isSidebarCollapsed, 'mobile-open': store.isMobileSidebarOpen }">
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
  </div>
</template>

<script setup>
import { nextTick } from 'vue'
import { store } from '../store.js'
import { api } from '../api.js'
import { saveActiveState, loadHistory, htmlEscape, renderMd } from '../utils.js'

defineEmits(['new-session'])

function origin() {
  return window.location.origin.replace(/\/+$/, '')
}

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

  if (!targetText || targetText === oldText) return
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
    store.snackbar.message = '更新会话名称失败: ' + e.message
    store.snackbar.show = true
    setTimeout(() => { if (store.snackbar.message.includes('更新会话名称失败')) store.snackbar.show = false }, 4000)
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
  const oldId = store.selectedSessionId
  if (oldId) {
    store.sessionMessages[oldId] = [...store.messages]
    store.sessionInputs[oldId] = { text: store.inputText, files: [...store.selectedFiles] }
  }
  store.selectedSessionId = id

  const saved = store.sessionInputs[id]
  store.inputText = saved ? saved.text : ''
  store.selectedFiles = saved?.files || []

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
</script>

<style scoped>
#sidebar {
  width: 280px;
  display: flex;
  background: var(--md-surface);
  border-right: 1px solid var(--md-outline-variant);
  flex-shrink: 0;
  transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1), background 0.25s, border-color 0.25s;
  overflow: hidden;
}
#sidebar.collapsed {
  width: 0;
  border-right: 0 solid transparent;
}
#sidebar .col {
  width: 280px;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow-y: auto;
}
.col-header {
  font-size: 14px;
  font-weight: 500;
  letter-spacing: .5px;
  color: var(--md-primary);
  padding: 16px 16px 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.col-header button {
  background: var(--md-primary-container);
  color: var(--md-on-primary-container);
  border: none;
  border-radius: var(--md-shape-full);
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 4px;
  transition: box-shadow .2s;
  box-shadow: var(--md-elevation-1);
}
.col-header button:hover { box-shadow: var(--md-elevation-2); filter: brightness(1.05); }
.col-header button .material-symbols-outlined { font-size: 16px; }
.item {
  padding: 10px 12px;
  border-radius: 12px;
  cursor: pointer;
  font-size: 13px;
  margin: 4px 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  transition: background 0.2s;
}
.item:hover { background: var(--md-surface-variant); }
.item.selected {
  background: var(--md-secondary-container);
  color: var(--md-on-secondary-container);
}
.item .info { flex: 1; overflow: hidden; display: flex; align-items: center; }
.item .info .name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; direction: rtl; text-align: left; width: 100%; }
.item .info .edit-input {
  width: 100%;
  background: var(--md-bg);
  color: var(--md-text-primary);
  border: 1px solid var(--md-primary);
  border-radius: 6px;
  padding: 2px 6px;
  font-size: 13px;
  outline: none;
}
.item .del {
  color: var(--md-text-secondary);
  background: none;
  border: none;
  cursor: pointer;
  padding: 6px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  visibility: hidden;
  transition: all 0.2s;
}
.item:hover .del { visibility: visible; }
.item .del:hover {
  background: rgba(255,180,171,0.15);
  color: var(--md-text-error);
}
.item .del .material-symbols-outlined { font-size: 18px; }

@media (hover: none) {
  .item .del { visibility: visible; }
}
</style>
