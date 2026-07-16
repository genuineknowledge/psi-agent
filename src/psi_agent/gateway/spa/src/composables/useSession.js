import { nextTick } from 'vue'
import { useSessionStore } from '../stores/session.js'
import { useChatStore } from '../stores/chat.js'
import { useAiStore } from '../stores/ai.js'
import { useUiStore } from '../stores/ui.js'
import { api } from '../api.js'
import { loadHistory, htmlEscape, renderMd, saveActiveState } from '../utils.js'
import { stripTransferMarkers } from '../sendMarkers.js'
import { isSseKeepaliveText } from '../sseKeepalive.js'
import { normalizeFailedTurns } from '../messageTurn.js'
import { normalizeWorkspacePath, resolveSessionWorkspace } from '../sessionList.js'

function origin() {
  return window.location.origin.replace(/\/+$/, '')
}

function snapshotDraftSession() {
  const session = useSessionStore()
  const chat = useChatStore()
  const draft = session.draftSession
  if (!draft) return
  session.sessionInputs[draft.draftId] = { text: chat.inputText, files: [...chat.selectedFiles] }
  if (chat.messages.length > 0) {
    session.sessionMessages[draft.draftId] = [...chat.messages]
  }
}

/** Drop in-memory caches when a session is deleted from Gateway. */
export function clearSessionLocalState(id) {
  const session = useSessionStore()
  delete session.sessionMessages[id]
  delete session.sessionInputs[id]
  delete session.sessionStreaming[id]
  delete session.sessionStreamMarks[id]
  delete session.sessionAbortControllers[id]
}

export function discardDraft() {
  const session = useSessionStore()
  const draft = session.draftSession
  if (!draft) return
  clearSessionLocalState(draft.draftId)
  session.draftSession = null
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
  const normalized = normalizeFailedTurns(list)
  if (normalized.length !== list.length || normalized.some((m, i) => m !== list[i])) {
    session.sessionMessages[id] = normalized
  }
  chat.messages.splice(0, chat.messages.length, ...normalized)
}

function restoreSessionView(id) {
  const session = useSessionStore()
  const chat = useChatStore()
  const controller = session.sessionAbortControllers[id] ?? null
  let streaming = !!session.sessionStreaming[id]
  // Gateway/tab restart can leave streaming=true without a live AbortController.
  if (streaming && !controller) {
    streaming = false
    session.sessionStreaming[id] = false
  }
  chat.streaming = streaming
  chat.abortController = controller
}

function clearChatView() {
  const chat = useChatStore()
  chat.messages.splice(0, chat.messages.length)
  chat.inputText = ''
  chat.selectedFiles = []
  chat.streaming = false
  chat.abortController = null
  chat.userHasScrolledUp = false
}

function restoreDraftView(draftId) {
  const session = useSessionStore()
  const chat = useChatStore()
  const saved = session.sessionInputs[draftId]
  chat.inputText = saved ? saved.text : ''
  chat.selectedFiles = saved?.files ? [...saved.files] : []
  if (session.sessionMessages[draftId]) {
    mirrorSessionMessages(draftId)
  } else {
    chat.messages.splice(0, chat.messages.length)
  }
  restoreSessionView(draftId)
}

/** Promote client draft to a Gateway session on first send. */
export async function promoteDraftToSession() {
  const session = useSessionStore()
  const ai = useAiStore()
  const draft = session.draftSession
  if (!draft) return session.selectedSessionId

  const aiId = draft.aiId || ai.selectedAiId
  if (!aiId) throw new Error('请先选择一个大模型代理')

  const info = await api('POST', '/sessions', {
    ai_id: aiId,
    workspace: draft.workspace,
  })
  const sid = info.id
  const draftId = draft.draftId

  if (session.sessionMessages[draftId]) {
    session.sessionMessages[sid] = session.sessionMessages[draftId]
    delete session.sessionMessages[draftId]
  } else {
    session.sessionMessages[sid] = []
  }
  if (session.sessionInputs[draftId]) {
    session.sessionInputs[sid] = session.sessionInputs[draftId]
    delete session.sessionInputs[draftId]
  }
  if (session.sessionStreaming[draftId]) {
    session.sessionStreaming[sid] = session.sessionStreaming[draftId]
    delete session.sessionStreaming[draftId]
  }
  if (session.sessionStreamMarks[draftId]) {
    session.sessionStreamMarks[sid] = session.sessionStreamMarks[draftId]
    delete session.sessionStreamMarks[draftId]
  }
  if (session.sessionAbortControllers[draftId]) {
    session.sessionAbortControllers[sid] = session.sessionAbortControllers[draftId]
    delete session.sessionAbortControllers[draftId]
  }

  session.draftSession = null
  session.selectedSessionId = sid

  try {
    session.sessions = await api('GET', '/sessions')
  } catch (_) {
    session.sessions = []
  }
  session.syncRegisteredWorkspaces()
  saveActiveState(ai.selectedAiId, sid, session.selectedWorkspacePath)
  return sid
}

export async function startDraftChat(workspacePath) {
  const session = useSessionStore()
  const ai = useAiStore()
  const ui = useUiStore()

  if (!ai.ais.length) {
    ui.showAlert('请先配置大模型')
    return false
  }
  if (!ai.selectedAiId) ai.selectedAiId = ai.ais[0].id

  const path = normalizeWorkspacePath(workspacePath || session.selectedWorkspacePath)
  if (!path) return false

  const oldId = session.selectedSessionId
  if (oldId) snapshotCurrentSession(oldId)

  if (session.draftSession) {
    snapshotDraftSession()
    discardDraft()
  }

  session.setSelectedWorkspace(path)
  session.ensureWorkspaceExpanded(path)
  session.selectedSessionId = null

  const draftId = crypto.randomUUID()
  session.draftSession = {
    draftId,
    workspace: path,
    aiId: ai.selectedAiId,
  }
  session.sessionMessages[draftId] = []
  session.sessionStreaming[draftId] = false
  delete session.sessionAbortControllers[draftId]

  clearChatView()
  saveActiveState(ai.selectedAiId, null, path)
  ui.isMobileSidebarOpen = false
  return true
}

export async function selectDraftChat(workspacePath) {
  const session = useSessionStore()
  const ai = useAiStore()
  const ui = useUiStore()
  const path = normalizeWorkspacePath(workspacePath)
  const draft = session.draftSession

  if (draft && draft.workspace === path) {
    session.setSelectedWorkspace(path)
    session.ensureWorkspaceExpanded(path)
    session.selectedSessionId = null
    delete session.sessionStreamMarks[draft.draftId]
    restoreDraftView(draft.draftId)
    saveActiveState(ai.selectedAiId, null, path)
    ui.isMobileSidebarOpen = false
    return
  }

  await startDraftChat(path)
}

export async function selectWorkspace(path) {
  const session = useSessionStore()
  const ai = useAiStore()
  const ui = useUiStore()

  const oldId = session.selectedSessionId
  if (oldId) snapshotCurrentSession(oldId)

  if (session.draftSession) {
    snapshotDraftSession()
    discardDraft()
  }

  session.setSelectedWorkspace(path)
  session.ensureWorkspaceExpanded(path)
  session.selectedSessionId = null
  clearChatView()

  saveActiveState(ai.selectedAiId, null, session.selectedWorkspacePath)
  ui.isMobileSidebarOpen = false
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

  if (session.draftSession) {
    snapshotDraftSession()
    discardDraft()
  }

  session.selectedSessionId = id
  delete session.sessionStreamMarks[id]

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
          if (isSseKeepaliveText(h.text)) return
          const local = i < localHist.length ? localHist[i] : null
          const text = stripTransferMarkers(h.text)
          built.push({
            id: '', role: h.role, text,
            html: h.role === 'user' ? htmlEscape(text) : renderMd(text),
            files: local ? local.files || [] : [],
            stopped: local ? local.stopped || false : false,
            failed: local ? local.failed || false : false,
            failedReason: local?.failedReason || '',
            // Server stamps feedback on assistant from hidden user_feedback rows.
            feedback: h.feedback || local?.feedback || '',
          })
        })
        if (localHist.length > built.length) {
          for (let i = built.length; i < localHist.length; i++) {
            const h = localHist[i]
            if (isSseKeepaliveText(h.text)) continue
            const text = stripTransferMarkers(h.text)
            built.push({
              id: '', role: h.role, text,
              html: h.role === 'user' ? htmlEscape(text) : renderMd(text),
              files: h.files || [],
              stopped: h.stopped || false,
              failed: h.failed || false,
              failedReason: h.failedReason || '',
              feedback: h.feedback || '',
            })
          }
        }
      } else { throw new Error() }
    } catch (e) {
      localHist.forEach(h => {
        if (isSseKeepaliveText(h.text)) return
        const text = stripTransferMarkers(h.text)
        built.push({
          id: '', role: h.role, text,
          html: h.role === 'user' ? htmlEscape(text) : renderMd(text),
          files: h.files || [],
          stopped: h.stopped || false,
          failed: h.failed || false,
          failedReason: h.failedReason || '',
          feedback: h.feedback || '',
        })
      })
    }
    const normalized = normalizeFailedTurns(built)
    if (session.selectedSessionId === id) {
      chat.messages.splice(0, chat.messages.length, ...normalized)
      session.sessionMessages[id] = [...normalized]
      restoreSessionView(id)
    }
  }

  const currentSess = session.sessions.find(s => s.id === id)
  if (currentSess) {
    ai.selectedAiId = currentSess.ai_id
    session.setSelectedWorkspace(resolveSessionWorkspace(currentSess, session.gatewayCwd))
    session.ensureWorkspaceExpanded(session.selectedWorkspacePath)
  }
  saveActiveState(ai.selectedAiId, session.selectedSessionId, session.selectedWorkspacePath)

  chat.userHasScrolledUp = false
  ui.isMobileSidebarOpen = false
  nextTick(() => {
    const el = document.getElementById('messages')
    if (el) el.scrollTop = el.scrollHeight
  })
}
