<template>
  <div id="input-wrapper" v-show="store.selectedSessionId">
    <div id="file-preview-bar" v-if="store.selectedFiles.length">
      <div class="preview-chip" v-for="(f, i) in store.selectedFiles" :key="i">
        <span class="material-symbols-outlined" style="font-size:16px;">attach_file</span>
        <span>{{ f.name }}</span>
        <button class="close-btn" @click="store.selectedFiles.splice(i, 1)" title="移除附件">
          <span class="material-symbols-outlined" style="font-size:16px;">close</span>
        </button>
      </div>
    </div>

    <div id="input-area">
      <label class="btn" for="file-upload"><span class="material-symbols-outlined">attach_file</span></label>
      <input type="file" id="file-upload" multiple @change="onFileSelected">

      <textarea
        id="chat-input"
        v-model="store.inputText"
        rows="1"
        placeholder="发送消息..."
        @keydown.enter.exact.prevent="sendMessage"
        @input="autoResizeInput"
      ></textarea>

      <ModelPanel
        @select-ai="$emit('select-ai', $event)"
        @delete-ai="$emit('delete-ai', $event)"
        @new-ai="$emit('new-ai')"
      />

      <button class="send" :disabled="store.streaming" @click="sendMessage">
        <span class="material-symbols-outlined">send</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { watch, nextTick } from 'vue'
import { store } from '../store.js'
import { htmlEscape, renderMd, saveHistory, loadHistory } from '../utils.js'
import { readSSE } from '../composables/useSSE.js'
import ModelPanel from './ModelPanel.vue'

defineEmits(['select-ai', 'delete-ai', 'new-ai'])

function origin() {
  return window.location.origin.replace(/\/+$/, '')
}

function onFileSelected(e) {
  const files = Array.from(e.target.files || [])
  store.selectedFiles.push(...files)
}

function autoResizeInput() {
  const el = document.getElementById('chat-input')
  if (!el) return
  el.style.height = 'auto'
  const borders = el.offsetHeight - el.clientHeight
  el.style.height = el.scrollHeight + borders + 'px'
}

watch(() => store.inputText, () => nextTick(autoResizeInput))

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

async function encodeFiles(files, um) {
  for (const f of files) {
    try {
      const b64 = await new Promise((resolve, reject) => {
        const r = new FileReader()
        r.onload = () => resolve(r.result.split(',')[1])
        r.onerror = e => reject(e)
        r.readAsDataURL(f)
      })
      if (um) um.files.push({ name: f.name, data: b64 })
    } catch {}
  }
}

async function sendMessage() {
  if (store.streaming || !store.selectedSessionId) return
  const text = store.inputText.trim()
  const files = [...store.selectedFiles]
  if (!text && !files.length) return

  store.streaming = true
  store.inputText = ''
  store.selectedFiles = []
  document.getElementById('file-upload').value = ''
  store.userHasScrolledUp = false

  let um = null
  if (text) {
    um = addMessage('user', `u-${Date.now()}`)
    um.text = text
    um.html = htmlEscape(text)
    await encodeFiles(files, um)
  } else if (files.length) {
    um = addMessage('user', `u-${Date.now()}`)
    um.text = `[Uploaded File${files.length > 1 ? 's' : ''}: ${files.map(f => f.name).join(', ')}]`
    um.html = htmlEscape(um.text)
    await encodeFiles(files, um)
  }

  const fd = new FormData()
  const chunks = []
  if (text) chunks.push({ type: 'text', text })
  fd.append('chunks', JSON.stringify(chunks))
  for (const f of files) fd.append('file', f)

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
    if (data.title) store.sessionTitles[sid] = data.title
  } catch (e) {}
}
</script>

<style scoped>
#input-wrapper {
  border-top: 1px solid var(--md-outline-variant);
  background: var(--md-surface-container);
  display: flex;
  flex-direction: column;
  transition: background 0.25s, border-color 0.25s;
}
#file-preview-bar { display: flex; padding: 8px 24px 0 24px; }
.preview-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--md-surface-container-high);
  border: 1px solid var(--md-primary);
  border-radius: 8px;
  padding: 4px 10px;
  font-size: 12px;
  color: var(--md-primary);
}
.preview-chip .close-btn {
  background: none; border: none; color: var(--md-text-secondary);
  cursor: pointer; display: flex; align-items: center; padding: 2px; border-radius: 50%;
}
.preview-chip .close-btn:hover { background: rgba(0,0,0,0.05); color: var(--md-text-error); }

#input-area { padding: 12px 24px 16px 24px; display: flex; gap: 12px; align-items: center; }
#input-area textarea {
  flex: 1; background: var(--md-bg); color: var(--md-text-primary);
  border: 1px solid var(--md-outline-variant); border-radius: 24px;
  padding: 10px 18px; font-size: 14px; font-family: inherit;
  resize: none; min-height: 42px; max-height: 120px; outline: none;
  transition: background 0.25s, border-color 0.25s;
}
#input-area textarea:focus { border-color: var(--md-primary); }
#input-area label.btn {
  background: transparent; color: var(--md-primary); border: none; border-radius: 50%;
  width: 40px; height: 40px; display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: background 0.2s;
}
#input-area label.btn:hover { background: var(--md-surface-variant); }
#input-area input[type=file] { display: none; }
#input-area button.send {
  background: var(--md-primary); color: var(--md-on-primary); border: none; border-radius: 50%;
  width: 40px; height: 40px; display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: all 0.2s; box-shadow: 0 1px 3px rgba(0,0,0,0.1); flex-shrink: 0;
}
#input-area button.send:hover:not(:disabled) { filter: brightness(1.1); transform: scale(1.05); }
#input-area button.send:disabled { opacity: .4; cursor: default; box-shadow: none; }

@media (max-width: 768px) {
  #input-wrapper {
    position: fixed;
    left: 0; right: 0;
    bottom: 0;
    z-index: 25;
    background: var(--md-surface-container);
    border-top: 1px solid var(--md-outline-variant);
  }
  #input-area {
    padding: 8px 12px;
    padding-bottom: calc(8px + env(safe-area-inset-bottom));
    gap: 8px;
  }
  #file-preview-bar { padding: 6px 12px 0; }
}

@media (max-width: 400px) {
  #input-area { gap: 6px; }
}
</style>
