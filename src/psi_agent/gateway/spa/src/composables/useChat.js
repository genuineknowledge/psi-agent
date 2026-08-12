import { nextTick } from 'vue'

import { useChatStore } from '../stores/chat.js'
import { useSessionStore } from '../stores/session.js'
import { htmlEscape, renderMd, saveHistory, loadHistory } from '../utils.js'
import { readSSE } from './useSSE.js'
import { api, streamChat } from '../api.js'
import { scrollToBottomIfLocked } from './useScroll.js'
import { promoteDraftToSession } from './useSession.js'
import { useUiStore } from '../stores/ui.js'
import { useUxStore } from '../stores/ux.js'
import {
  buildSessionTitlePayload,
  isPlaceholderSessionTitle,
  PLACEHOLDER_SESSION_TITLE,
} from '../sessionList.js'
import { applyTurnOutcome, normalizeFailedTurns, resolveTurnOutcome } from '../messageTurn.js'
import { hasAssistantSegmentAfterUser } from '../assistantSegments.js'
import { stripTransferMarkers } from '../sendMarkers.js'
import { normalizeRouterStatusEvent } from '../routerStatus.js'
import { applyStreamIssueToAssistant } from '../streamIssue.js'
import { assertMatchingTraceId, createTraceId } from '../traceId.js'
import {
  isAbortError,
  readFileAsBase64,
  throwIfAborted,
} from '../turnCancellation.js'

function origin() {
  return window.location.origin.replace(/\/+$/, '')
}

function isVisibleChatKey(key) {
  const session = useSessionStore()
  if (!key) return false
  if (session.selectedSessionId === key) return true
  return session.draftSession?.draftId === key
}

/** Ask Gateway to flash tray/webview when the user is not looking at this turn. */
function notifyAttentionIfNeeded(sid) {
  const lookingAtTurn =
    document.visibilityState === 'visible'
    && document.hasFocus()
    && isVisibleChatKey(sid)
  if (lookingAtTurn) return
  void api('POST', '/ui/attention').catch(() => {})
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

function clearSessionRouterStatus(sid) {
  const list = getMessagesList(sid)
  for (const message of list) {
    if (message.role === 'assistant' && message.routerStatus !== null) {
      message.routerStatus = null
    }
  }
  return list
}

function isSessionStreaming(sid) {
  return !!useSessionStore().sessionStreaming[sid]
}

function setSessionStreaming(sid, value, { markDone = false } = {}) {
  const session = useSessionStore()
  const chat = useChatStore()
  session.sessionStreaming[sid] = value
  if (value) {
    delete session.sessionStreamMarks[sid]
  } else if (markDone && sid && !isVisibleChatKey(sid)) {
    session.sessionStreamMarks[sid] = true
  }
  if (isVisibleChatKey(sid)) {
    chat.streaming = value
  }
}

/** Clear streaming flag when no live AbortController (tab refresh / crashed fetch). */
export function clearStaleStreaming() {
  const sid = resolveActiveChatKey()
  if (!sid) return
  if (!isSessionStreaming(sid)) {
    mirrorVisibleMessages(sid, clearSessionRouterStatus(sid))
    return
  }
  const session = useSessionStore()
  const chat = useChatStore()
  if (session.sessionAbortControllers[sid] || chat.abortController) return
  setSessionStreaming(sid, false)
  mirrorVisibleMessages(sid, clearSessionRouterStatus(sid))
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

function clearSessionAbortController(sid, controller) {
  const session = useSessionStore()
  const chat = useChatStore()
  if (session.sessionAbortControllers[sid] === controller) {
    delete session.sessionAbortControllers[sid]
  }
  if (isVisibleChatKey(sid) && chat.abortController === controller) {
    chat.abortController = null
  }
}

function addMessage(sid, role, id) {
  const list = getMessagesList(sid)
  const m = { id, role, text: '', html: '', files: [], stopped: false, failed: false }
  if (role === 'assistant') {
    m.routerStatus = null
    m.warnings = []
  }
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
  const m = {
    id: `a-${Date.now()}`,
    role: 'assistant',
    text: '',
    html: '',
    files: [],
    stopped: false,
    failed: false,
    routerStatus: null,
    warnings: [],
  }
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

async function encodeFiles(files, um, signal) {
  for (const f of files) {
    throwIfAborted(signal)
    try {
      const b64 = await readFileAsBase64(f, signal)
      throwIfAborted(signal)
      if (um) um.files.push({ name: f.name, data: b64 })
    } catch (error) {
      if (isAbortError(error)) throw error
    }
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

async function runChatTurn(
  sid,
  { userMsg, text, files, controller = new AbortController(), traceId = createTraceId() },
) {
  const ux = useUxStore()
  ux.startTurn({ traceId, sessionKey: sid })
  ensureSessionMessageList(sid)
  mirrorVisibleMessages(sid, clearSessionRouterStatus(sid))
  setSessionAbortController(sid, controller)
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
  ux.mark(traceId, 'assistant_ready')
  let visibleOutputMarked = false

  async function markFirstVisibleOutput() {
    if (!ux.enabled || visibleOutputMarked) return
    visibleOutputMarked = true
    await nextTick()
    ux.mark(traceId, 'first_visible_output')
  }

  let outcome = null
  try {
    try {
      throwIfAborted(controller.signal)
      ux.mark(traceId, 'request_start')
      const reader = await streamChat(sid, fd, controller.signal, traceId)
      ux.mark(traceId, 'response_headers')
      for await (const chunkData of readSSE(reader)) {
        ux.mark(traceId, 'first_sse')
        ux.recordSse(traceId, chunkData?.trace_id)
        if (chunkData?.trace_id !== undefined) {
          assertMatchingTraceId(chunkData.trace_id, traceId)
        }
        if (chunkData.type === 'text' && chunkData.text !== undefined) {
          asst = ensureStreamingAssistant(sid, userMsg, asst)
          asst.text += chunkData.text
          asst.html = renderMd(stripTransferMarkers(asst.text))
          if (chunkData.text) await markFirstVisibleOutput()
        } else if (chunkData.type === 'blob') {
          asst = ensureStreamingAssistant(sid, userMsg, asst)
          asst.files.push({ name: chunkData.name, data: chunkData.data })
          await markFirstVisibleOutput()
          // SEND path succeeded as a chip — hide leftover markers from bubble text.
          asst.html = renderMd(stripTransferMarkers(asst.text))
        } else if (chunkData.type === 'error') {
          asst = ensureStreamingAssistant(sid, userMsg, asst)
          const issue = applyStreamIssueToAssistant(asst, chunkData)
          ux.recordIssue(traceId, issue)
          if (issue?.severity !== 'warning') {
            clearSessionRouterStatus(sid)
          }
        } else if (chunkData.type === 'router_status') {
          const routerStatus = normalizeRouterStatusEvent(chunkData)
          if (routerStatus) {
            asst = ensureStreamingAssistant(sid, userMsg, asst)
            asst.routerStatus = routerStatus
            ux.recordRouterStatus(traceId, routerStatus)
          }
        } else if (chunkData.type === 'reasoning') {
          // Thinking + tool markers arrive as reasoning. Do not start a new
          // bubble — keep one assistant message for the whole user turn.
        }
        if (isVisibleChatKey(sid)) {
          mirrorVisibleMessages(sid, getMessagesList(sid))
          scrollToBottomIfLocked()
        }
      }
    } catch (e) {
      asst = ensureStreamingAssistant(sid, userMsg, asst)
      if (isAbortError(e)) {
        asst.stopped = true
      } else {
        asst.fatal = true
        ux.recordIssue(traceId, { severity: 'fatal' })
      }
      clearSessionRouterStatus(sid)
    }

    clearSessionRouterStatus(sid)

    const msgs = getMessagesList(sid)
    const asstIdx = asst ? msgs.indexOf(asst) : -1
    if (asstIdx >= 0) {
      const stub = msgs[asstIdx]
      if (!stub.text && !stub.files.length && !stub.fatal && !stub.stopped) {
        msgs.splice(asstIdx, 1)
        if (asst === stub) asst = null
      } else if (stub.text) {
        stub.text = stripTransferMarkers(stub.text)
        stub.html = renderMd(stub.text)
      }
    }

    outcome = resolveTurnOutcome(msgs, userMsg, asst)
    applyTurnOutcome(msgs, userMsg, asst, outcome)
    clearSessionRouterStatus(sid)
    const normalized = normalizeFailedTurns(msgs)
    msgs.splice(0, msgs.length, ...normalized)
    mirrorVisibleMessages(sid, msgs)
    saveHistory(sid, msgs)
  } finally {
    const cleared = clearSessionRouterStatus(sid)
    mirrorVisibleMessages(sid, cleared)
    setSessionStreaming(sid, false, { markDone: true })
    clearSessionAbortController(sid, controller)
    ux.finishTurn(traceId, {
      outcome: outcome ?? (controller.signal.aborted ? 'stopped' : 'incomplete'),
      statusCleared: cleared.every(message => message.routerStatus == null),
    })
  }

  if (outcome === 'ok') notifyAttentionIfNeeded(sid)

  const session = useSessionStore()
  const currentTitle = session.sessionTitles[sid]
  if (isPlaceholderSessionTitle(currentTitle)) {
    await generateTitle(sid)
  }
}

function chatTurnScrollReset() {
  useChatStore().userHasScrolledUp = false
}

function abortOptimisticSend(sid, userMsg, failedReason = 'error') {
  setSessionStreaming(sid, false)
  const list = getMessagesList(sid)
  clearSessionRouterStatus(sid)
  if (!userMsg) {
    mirrorVisibleMessages(sid, list)
    return
  }
  userMsg.failed = true
  userMsg.failedReason = failedReason
  const idx = list.indexOf(userMsg)
  if (idx >= 0) {
    const next = list[idx + 1]
    if (next?.role === 'assistant' && !next.text && !next.files.length) {
      list.splice(idx + 1, 1)
    }
  }
  mirrorVisibleMessages(sid, list)
  if (useSessionStore().draftSession?.draftId !== sid) {
    saveHistory(sid, list)
  }
}

export async function sendMessage() {
  const chat = useChatStore()
  const session = useSessionStore()
  const ux = useUxStore()

  let sid = resolveActiveChatKey()
  if (!sid) return
  // Recover stale streaming after Gateway restart or aborted fetch without finally.
  if (isSessionStreaming(sid) && !session.sessionAbortControllers[sid] && !chat.abortController) {
    setSessionStreaming(sid, false)
    mirrorVisibleMessages(sid, clearSessionRouterStatus(sid))
  }
  if (isSessionStreaming(sid)) return
  const text = chat.inputText.trim()
  const files = [...chat.selectedFiles]
  if (!text && !files.length) return

  const traceId = createTraceId()
  ux.startTurn({ traceId, sessionKey: sid })

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

  const controller = new AbortController()
  setSessionAbortController(sid, controller)
  setSessionStreaming(sid, true)
  addAssistantAfter(sid, um)
  ux.mark(traceId, 'assistant_ready')

  try {
    await encodeFiles(files, um, controller.signal)

    if (session.draftSession) {
      const draftId = sid
      sid = await promoteDraftToSession({ signal: controller.signal })
      ux.moveTurn(traceId, draftId, sid)
      const list = getMessagesList(sid)
      if (!list.includes(um)) {
        um = list.filter(m => m.role === 'user').at(-1) ?? um
      }
      mirrorVisibleMessages(sid, list)
      if (!chat.streaming) setSessionStreaming(sid, true)
    }

    throwIfAborted(controller.signal)
    void ensureSessionSidebarTitle(sid)
    await runChatTurn(sid, { userMsg: um, text: um.text, files, controller, traceId })
  } catch (e) {
    const stopped = isAbortError(e) || controller.signal.aborted
    abortOptimisticSend(sid, um, stopped ? 'stopped' : 'error')
    clearSessionAbortController(sid, controller)
    if (!stopped) ux.recordIssue(traceId, { severity: 'fatal' })
    ux.finishTurn(traceId, {
      outcome: stopped ? 'stopped' : 'error',
      statusCleared: getMessagesList(sid).every(message => message.routerStatus == null),
    })
    if (!stopped) useUiStore().showAlert(e.message || '发送失败')
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
  const ux = useUxStore()
  const sid = resolveActiveChatKey()
  if (!sid) return
  const controller = session.sessionAbortControllers[sid] ?? chat.abortController
  if (controller) {
    ux.markStopForSession(sid)
    controller.abort()
  }
  mirrorVisibleMessages(sid, clearSessionRouterStatus(sid))
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
