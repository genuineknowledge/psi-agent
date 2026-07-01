import { nextTick } from 'vue'
import { store } from '../store.js'
import { loadHistory, htmlEscape, renderMd, saveActiveState } from '../utils.js'

function origin() {
  return window.location.origin.replace(/\/+$/, '')
}

export async function selectSession(id) {
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
