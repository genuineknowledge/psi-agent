import { nextTick } from 'vue'
import { useSessionStore } from '../stores/session.js'
import { useChatStore } from '../stores/chat.js'
import { useAiStore } from '../stores/ai.js'
import { useUiStore } from '../stores/ui.js'
import { loadHistory, htmlEscape, renderMd, saveActiveState } from '../utils.js'

function origin() {
  return window.location.origin.replace(/\/+$/, '')
}

export async function selectSession(id) {
  const session = useSessionStore()
  const chat = useChatStore()
  const ai = useAiStore()
  const ui = useUiStore()

  if (session.editingSessionId === id) return
  const oldId = session.selectedSessionId
  if (oldId) {
    session.sessionMessages[oldId] = [...chat.messages]
    session.sessionInputs[oldId] = { text: chat.inputText, files: [...chat.selectedFiles] }
  }
  session.selectedSessionId = id

  const saved = session.sessionInputs[id]
  chat.inputText = saved ? saved.text : ''
  chat.selectedFiles = saved?.files || []

  if (session.sessionMessages[id]) {
    chat.messages.splice(0, chat.messages.length, ...session.sessionMessages[id])
  } else {
    // 先在本地数组里把消息构建完整，再一次性原子替换 chat.messages，
    // 避免「先清空 → await fetch → 逐条 push」中间那段空窗让聊天区闪一下空白。
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
    // 切换期间用户可能又点了别的会话；仅当仍停留在本会话时才落地，避免竞态覆盖。
    if (session.selectedSessionId === id) {
      chat.messages.splice(0, chat.messages.length, ...built)
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
