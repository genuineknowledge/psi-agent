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
import { applyTurnOutcome, normalizeFailedTurns, resolveTurnOutcome } from '../messageTurn.js'
import { hasAssistantSegmentAfterUser } from '../assistantSegments.js'

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

/** Clear streaming flag when no live AbortController (tab refresh / crashed fetch). */
export function clearStaleStreaming() {
  const sid = resolveActiveChatKey()
  if (!sid || !isSessionStreaming(sid)) return
  const session = useSessionStore()
  const chat = useChatStore()
  if (session.sessionAbortControllers[sid] || chat.abortController) return
  setSessionStreaming(sid, false)
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
  const m = { id, role, text: '', html: '', files: [], stopped: false, failed: false }
  list.push(m)
  mirrorVisibleMessages(sid, list)
  if (isVisibleChatKey(sid)) {
    scrollToBottomIfLocked()
  }
  return list[list.length - 1]
}

function addAssistantAfter(sid, userMsg) {
  const list = getMessagesList(sid)
  const idx = list.indexOf(userMsg)
  const m = { id: `a-${Date.now()}`, role: 'assistant', text: '', html: '', files: [], stopped: false, failed: false }
  if (idx >= 0) {
    list.splice(idx + 1, 0, m)
  } else {
    list.push(m)
  }
  mirrorVisibleMessages(sid, list)
  if (isVisibleChatKey(sid)) {
    scrollToBottomIfLocked()
  }
  return m
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

function base64ToFile(b64, name) {
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return new File([bytes], name)
}

function turnTextForPayload(text, files) {
  if (!text) return ''
  if (files?.length && text.startsWith('[Uploaded File')) return ''
  return text
}

function appendFilesToFormData(fd, files) {
  for (const f of files) {
    if (f instanceof File) {
      fd.append('file', f)
    } else if (f?.data && f?.name) {
      fd.append('file', base64ToFile(f.data, f.name), f.name)
    }
  }
}

function findPendingAssistantAfter(list, userMsg) {
  const idx = list.indexOf(userMsg)
  if (idx < 0 || idx + 1 >= list.length) return null
  const next = list[idx + 1]
  if (
    next.role === 'assistant'
    && !next.text
    && !next.files.length
    && !next.stopped
    && !next.failed
  ) {
    return next
  }
  return null
}

/** First assistant segment sits right after user (#327); reasoning splits append below. */
function ensureStreamingAssistant(sid, userMsg, currentAsst) {
  if (currentAsst) return currentAsst
  const list = getMessagesList(sid)
  if (hasAssistantSegmentAfterUser(list, userMsg)) {
    return addMessage(sid, 'assistant', `a-${Date.now()}`)
  }
  return addAssistantAfter(sid, userMsg)
}

async function runChatTurn(sid, { userMsg, text, files }) {
  ensureSessionMessageList(sid)
  setSessionStreaming(sid, true)
  chatTurnScrollReset()

  const payloadText = turnTextForPayload(text, files)
  const fd = new FormData()
  const chunks = []
  if (payloadText) chunks.push({ type: 'text', text: payloadText })
  fd.append('chunks', JSON.stringify(chunks))
  appendFilesToFormData(fd, files)

  let asst = findPendingAssistantAfter(getMessagesList(sid), userMsg)
  if (!asst) asst = addAssistantAfter(sid, userMsg)

  const controller = new AbortController()
  setSessionAbortController(sid, controller)

  let streamError = false
  try {
    try {
      const reader = await streamChat(sid, fd, controller.signal)
      for await (const chunkData of readSSE(reader)) {
        if (chunkData.type === 'text' && chunkData.text !== undefined) {
          asst = ensureStreamingAssistant(sid, userMsg, asst)
          asst.text += chunkData.text
          asst.html = renderMd(asst.text)
        } else if (chunkData.type === 'blob') {
          asst = ensureStreamingAssistant(sid, userMsg, asst)
          asst.files.push({ name: chunkData.name, data: chunkData.data })
        } else if (chunkData.type === 'error') {
          streamError = true
          asst = ensureStreamingAssistant(sid, userMsg, asst)
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
      asst = ensureStreamingAssistant(sid, userMsg, asst)
      if (e.name === 'AbortError') {
        asst.stopped = true
      } else {
        streamError = true
        asst.text += '\n[Error: ' + e.message + ']'
        asst.html = renderMd(asst.text)
      }
    }

    const msgs = getMessagesList(sid)
    const asstIdx = asst ? msgs.indexOf(asst) : -1
    if (asstIdx >= 0) {
      const stub = msgs[asstIdx]
      if (!stub.text && !stub.files.length) {
        msgs.splice(asstIdx, 1)
        if (asst === stub) asst = null
      }
    }

    let outcome = resolveTurnOutcome(msgs, userMsg, asst)
    if (streamError && outcome === 'ok') outcome = 'error'
    applyTurnOutcome(msgs, userMsg, asst, outcome)
    const normalized = normalizeFailedTurns(msgs)
    msgs.splice(0, msgs.length, ...normalized)
    mirrorVisibleMessages(sid, msgs)
    saveHistory(sid, msgs)
  } finally {
    setSessionStreaming(sid, false)
    setSessionAbortController(sid, null)
  }

  const session = useSessionStore()
  const currentTitle = session.sessionTitles[sid]
  if (isPlaceholderSessionTitle(currentTitle)) {
    await generateTitle(sid)
  }
}

function chatTurnScrollReset() {
  useChatStore().userHasScrolledUp = false
}

function abortOptimisticSend(sid, userMsg) {
  setSessionStreaming(sid, false)
  if (!userMsg) return
  const list = getMessagesList(sid)
  userMsg.failed = true
  userMsg.failedReason = 'error'
  const idx = list.indexOf(userMsg)
  if (idx >= 0) {
    const next = list[idx + 1]
    if (next?.role === 'assistant' && !next.text && !next.files.length) {
      list.splice(idx + 1, 1)
    }
  }
  mirrorVisibleMessages(sid, list)
}

export async function sendMessage() {
  const chat = useChatStore()
  const session = useSessionStore()

  let sid = resolveActiveChatKey()
  if (!sid) return
  // Recover stale streaming after Gateway restart or aborted fetch without finally.
  if (isSessionStreaming(sid) && !session.sessionAbortControllers[sid] && !chat.abortController) {
    setSessionStreaming(sid, false)
  }
  if (isSessionStreaming(sid)) return
  const text = chat.inputText.trim()
  const files = [...chat.selectedFiles]
  if (!text && !files.length) return

  ensureSessionMessageList(sid)

  // Optimistic UI: clear input and show user bubble + thinking immediately.
  chat.inputText = ''
  chat.selectedFiles = []
  chat.uploadResetToken++
  chatTurnScrollReset()

  let um = null
  if (text) {
    um = addMessage(sid, 'user', `u-${Date.now()}`)
    um.text = text
    um.html = htmlEscape(text)
  } else if (files.length) {
    um = addMessage(sid, 'user', `u-${Date.now()}`)
    um.text = `[Uploaded File${files.length > 1 ? 's' : ''}: ${files.map(f => f.name).join(', ')}]`
    um.html = htmlEscape(um.text)
  }

  setSessionStreaming(sid, true)
  addAssistantAfter(sid, um)

  try {
    await encodeFiles(files, um)

    if (session.draftSession) {
      sid = await promoteDraftToSession()
      const list = getMessagesList(sid)
      if (!list.includes(um)) {
        um = list.filter(m => m.role === 'user').at(-1) ?? um
      }
      mirrorVisibleMessages(sid, list)
      if (!chat.streaming) setSessionStreaming(sid, true)
    }

    void ensureSessionSidebarTitle(sid)
    await runChatTurn(sid, { userMsg: um, text: um.text, files })
  } catch (e) {
    abortOptimisticSend(sid, um)
    useUiStore().showAlert(e.message || '发送失败')
  }
}

function cloneStoredFiles(files) {
  return (files || []).map(f => ({ name: f.name, data: f.data }))
}

/** Re-run the turn for the user message preceding *assistantMsg*. */
export async function regenerateAssistantMessage(assistantMsg) {
  const sid = resolveActiveChatKey()
  if (!sid || isSessionStreaming(sid) || !assistantMsg || assistantMsg.role !== 'assistant') return

  const msgs = getMessagesList(sid)
  const asstIdx = msgs.indexOf(assistantMsg)
  if (asstIdx < 0) return

  let userMsg = null
  for (let i = asstIdx - 1; i >= 0; i--) {
    if (msgs[i]?.role === 'user') {
      userMsg = msgs[i]
      break
    }
  }
  if (!userMsg) return

  const text = userMsg.text
  const files = cloneStoredFiles(userMsg.files)

  msgs.splice(asstIdx, 1)
  addAssistantAfter(sid, userMsg)
  mirrorVisibleMessages(sid, msgs)

  await runChatTurn(sid, { userMsg, text, files })
}

/** Remove failed bubble, append a fresh copy, and send again. */
export async function resendFailedMessage(userMsg) {
  const sid = resolveActiveChatKey()
  if (!sid || isSessionStreaming(sid) || !userMsg?.failed) return

  const msgs = getMessagesList(sid)
  const idx = msgs.indexOf(userMsg)
  if (idx < 0) return

  const text = userMsg.text
  const files = cloneStoredFiles(userMsg.files)

  msgs.splice(idx, 1)

  const um = addMessage(sid, 'user', `u-${Date.now()}`)
  um.text = text
  um.html = htmlEscape(text)
  um.files = files

  mirrorVisibleMessages(sid, msgs)

  await runChatTurn(sid, { userMsg: um, text, files })
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
