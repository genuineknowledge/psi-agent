import { useChatStore } from '../stores/chat.js'
import { useSessionStore } from '../stores/session.js'
import { htmlEscape, renderMd, saveHistory, loadHistory } from '../utils.js'
import { readSSE } from './useSSE.js'
import { api, streamChat } from '../api.js'
import { scrollToBottomIfLocked } from './useScroll.js'
import { promoteDraftToSession } from './useSession.js'
import { useUiStore } from '../stores/ui.js'
import {
  buildSessionTitlePayload,
  isPlaceholderSessionTitle,
  PLACEHOLDER_SESSION_TITLE,
} from '../sessionList.js'

function origin() {
  return window.location.origin.replace(/\/+$/, '')
}

function isVisibleChatKey(key) {
  const session = useSessionStore()
  if (!key) return false
  if (session.selectedSessionId === key) return true
  return session.draftSession?.draftId === key
}

function resolveActiveChatKey() {
  const session = useSessionStore()
  if (session.selectedSessionId) return session.selectedSessionId
  return session.draftSession?.draftId ?? null
}

function ensureSessionMessageList(sid) {
  const session = useSessionStore()
  const chat = useChatStore()
  if (!session.sessionMessages[sid]) {
    if (isVisibleChatKey(sid) && chat.messages.length > 0) {
      session.sessionMessages[sid] = [...chat.messages]
    } else {
      session.sessionMessages[sid] = []
    }
  }
  return session.sessionMessages[sid]
}

/** Message list for *sid* — always sessionMessages; visible session mirrors into chat.messages. */
function getMessagesList(sid) {
  return ensureSessionMessageList(sid)
}

function mirrorVisibleMessages(sid, list) {
  const chat = useChatStore()
  if (!isVisibleChatKey(sid)) return
  if (chat.messages.length !== list.length || chat.messages.some((m, i) => m !== list[i])) {
    chat.messages.splice(0, chat.messages.length, ...list)
  }
}

function isSessionStreaming(sid) {
  return !!useSessionStore().sessionStreaming[sid]
}

function setSessionStreaming(sid, value) {
  const session = useSessionStore()
  const chat = useChatStore()
  session.sessionStreaming[sid] = value
  if (isVisibleChatKey(sid)) {
    chat.streaming = value
  }
}

/** Ensure sidebar shows this session immediately with a placeholder title. */
export async function ensureSessionSidebarTitle(sid) {
  if (!sid) return
  const session = useSessionStore()
  const current = session.sessionTitles[sid]
  if (current && !isPlaceholderSessionTitle(current)) return
  session.sessionTitles[sid] = PLACEHOLDER_SESSION_TITLE
  try {
    await api('POST', '/titles', buildSessionTitlePayload({ id: sid }, PLACEHOLDER_SESSION_TITLE))
  } catch (_) {}
}

function setSessionAbortController(sid, controller) {
  const session = useSessionStore()
  const chat = useChatStore()
  if (controller) {
    session.sessionAbortControllers[sid] = controller
  } else {
    delete session.sessionAbortControllers[sid]
  }
  if (isVisibleChatKey(sid)) {
    chat.abortController = controller
  }
}

function addMessage(sid, role, id) {
  const list = getMessagesList(sid)
  const m = { id, role, text: '', html: '', files: [], stopped: false }
  list.push(m)
  mirrorVisibleMessages(sid, list)
  if (isVisibleChatKey(sid)) {
    scrollToBottomIfLocked()
  }
  return list[list.length - 1]
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

export async function sendMessage() {
  const chat = useChatStore()
  const session = useSessionStore()

  let sid = resolveActiveChatKey()
  if (!sid || isSessionStreaming(sid)) return
  const text = chat.inputText.trim()
  const files = [...chat.selectedFiles]
  if (!text && !files.length) return

  if (session.draftSession) {
    try {
      sid = await promoteDraftToSession()
    } catch (e) {
      useUiStore().showAlert(e.message || '创建会话失败')
      return
    }
    if (!sid) return
  }

  await ensureSessionSidebarTitle(sid)
  ensureSessionMessageList(sid)

  setSessionStreaming(sid, true)
  chat.inputText = ''
  chat.selectedFiles = []
  chat.uploadResetToken++
  chat.userHasScrolledUp = false

  let um = null
  if (text) {
    um = addMessage(sid, 'user', `u-${Date.now()}`)
    um.text = text
    um.html = htmlEscape(text)
    await encodeFiles(files, um)
  } else if (files.length) {
    um = addMessage(sid, 'user', `u-${Date.now()}`)
    um.text = `[Uploaded File${files.length > 1 ? 's' : ''}: ${files.map(f => f.name).join(', ')}]`
    um.html = htmlEscape(um.text)
    await encodeFiles(files, um)
  }

  const fd = new FormData()
  const chunks = []
  if (text) chunks.push({ type: 'text', text })
  fd.append('chunks', JSON.stringify(chunks))
  for (const f of files) fd.append('file', f)

  let asst = addMessage(sid, 'assistant', `a-${Date.now()}`)

  const controller = new AbortController()
  setSessionAbortController(sid, controller)

  try {
    const reader = await streamChat(sid, fd, controller.signal)
    for await (const chunkData of readSSE(reader)) {
      if (chunkData.type === 'text' && chunkData.text !== undefined) {
        if (!asst) asst = addMessage(sid, 'assistant', `a-${Date.now()}`)
        asst.text += chunkData.text
        asst.html = renderMd(asst.text)
      } else if (chunkData.type === 'blob') {
        if (!asst) asst = addMessage(sid, 'assistant', `a-${Date.now()}`)
        asst.files.push({ name: chunkData.name, data: chunkData.data })
      } else if (chunkData.type === 'error') {
        if (!asst) asst = addMessage(sid, 'assistant', `a-${Date.now()}`)
        asst.text += '\n[Error: ' + chunkData.error + ']'
        asst.html = renderMd(asst.text)
      } else if (chunkData.type === 'reasoning') {
        if (asst && (asst.text || asst.files.length)) asst = null
      }
      if (isVisibleChatKey(sid)) {
        mirrorVisibleMessages(sid, getMessagesList(sid))
        scrollToBottomIfLocked()
      }
    }
  } catch (e) {
    if (!asst) asst = addMessage(sid, 'assistant', `a-${Date.now()}`)
    if (e.name === 'AbortError') {
      asst.stopped = true
    } else {
      asst.text += '\n[Error: ' + e.message + ']'
      asst.html = renderMd(asst.text)
    }
  }

  const msgs = getMessagesList(sid)
  const last = msgs[msgs.length - 1]
  if (last && last.role === 'assistant' && !last.text && !last.files.length) {
    msgs.pop()
  }
  mirrorVisibleMessages(sid, msgs)

  setSessionStreaming(sid, false)
  setSessionAbortController(sid, null)
  saveHistory(sid, msgs)

  const currentTitle = session.sessionTitles[sid]
  if (isPlaceholderSessionTitle(currentTitle)) {
    await generateTitle(sid)
  }
}

export function stopMessage() {
  const session = useSessionStore()
  const chat = useChatStore()
  const sid = resolveActiveChatKey()
  if (!sid) return
  const controller = session.sessionAbortControllers[sid] ?? chat.abortController
  if (controller) controller.abort()
}

async function generateTitle(sid) {
  const session = useSessionStore()
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
    if (data.title) session.sessionTitles[sid] = data.title
  } catch (e) {}
}
