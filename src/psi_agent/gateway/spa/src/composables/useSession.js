import { nextTick } from 'vue'
import { useSessionStore } from '../stores/session.js'
import { useChatStore } from '../stores/chat.js'
import { useAiStore } from '../stores/ai.js'
import { useUiStore } from '../stores/ui.js'
import { loadHistory, htmlEscape, renderMd, saveActiveState } from '../utils.js'

function origin() {
  return window.location.origin.replace(/\/+$/, '')
}

/** Drop in-memory caches when a session is deleted from Gateway. */
export function clearSessionLocalState(id) {
  const session = useSessionStore()
  delete session.sessionMessages[id]
  delete session.sessionInputs[id]
  delete session.sessionStreaming[id]
  delete session.sessionAbortControllers[id]
}

function snapshotCurrentSession(oldId) {
  const session = useSessionStore()
  const chat = useChatStore()
  if (!session.sessionMessages[oldId] && chat.messages.length > 0) {
    session.sessionMessages[oldId] = [...chat.messages]
  }
  session.sessionInputs[oldId] = { text: chat.inputText, files: [...chat.selectedFiles] }
  session.sessionStreaming[oldId] = chat.streaming
  if (chat.abortController) {
    session.sessionAbortControllers[oldId] = chat.abortController
  }
}

function mirrorSessionMessages(id) {
  const session = useSessionStore()
  const chat = useChatStore()
  const list = session.sessionMessages[id]
  if (!list) return
  chat.messages.splice(0, chat.messages.length, ...list)
}

function restoreSessionView(id) {
  const session = useSessionStore()
  const chat = useChatStore()
  chat.streaming = !!session.sessionStreaming[id]
  chat.abortController = session.sessionAbortControllers[id] ?? null
}

export async function selectSession(id) {
  const session = useSessionStore()
  const chat = useChatStore()
  const ai = useAiStore()
  const ui = useUiStore()

  if (session.editingSessionId === id) return
  const oldId = session.selectedSessionId
  if (oldId) {
    snapshotCurrentSession(oldId)
  }
  session.selectedSessionId = id

  const saved = session.sessionInputs[id]
  chat.inputText = saved ? saved.text : ''
  chat.selectedFiles = saved?.files || []

  if (session.sessionMessages[id]) {
    mirrorSessionMessages(id)
    restoreSessionView(id)
  } else {
    const localHist = loadHistory(id)
    const built = []
    try {
      const r = await fetch(origin() + '/sessions/' + id + '/history')
      if (r.ok) {
        const serverMsgs = await r.json()
        serverMsgs.forEach((h, i) => {
          const local = i < localHist.length ? localHist[i] : null
          built.push({
            id: '', role: h.role, text: h.text,
            html: h.role === 'user' ? htmlEscape(h.text) : renderMd(h.text),
            files: local ? local.files || [] : [],
            stopped: local ? local.stopped || false : false,
          })
        })
      } else { throw new Error() }
    } catch (e) {
      localHist.forEach(h => {
        built.push({ id: '', role: h.role, text: h.text, html: h.role === 'user' ? htmlEscape(h.text) : renderMd(h.text), files: h.files || [], stopped: h.stopped || false })
      })
    }
    if (session.selectedSessionId === id) {
      chat.messages.splice(0, chat.messages.length, ...built)
      session.sessionMessages[id] = [...built]
      restoreSessionView(id)
    }
  }

  const currentSess = session.sessions.find(s => s.id === id)
  if (currentSess) ai.selectedAiId = currentSess.ai_id
  saveActiveState(ai.selectedAiId, session.selectedSessionId)

  chat.userHasScrolledUp = false
  ui.isMobileSidebarOpen = false
  nextTick(() => {
    const el = document.getElementById('messages')
    if (el) el.scrollTop = el.scrollHeight
  })
}
