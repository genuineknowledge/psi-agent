/** @typedef {'ok' | 'error' | 'stopped' | 'incomplete'} TurnOutcome */

/** Remove trailing/inline `[Error: …]` annotations appended by soft stream failures. */
export function stripErrorAnnotations(text) {
  if (!text) return ''
  return String(text)
    .replace(/\n?\[Error:[^\]]*\]/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/**
 * Whether an assistant message counts as a complete reply to the preceding user turn.
 * Blob read failures may append `[Error:]` without canceling useful text/files.
 * @param {object | null | undefined} msg
 */
export function isCompleteAssistant(msg) {
  if (!msg || msg.role !== 'assistant') return false
  if (msg.stopped) return false
  const text = typeof msg.text === 'string' ? msg.text : ''
  const hasFiles = Array.isArray(msg.files) && msg.files.length > 0
  const clean = stripErrorAnnotations(text)
  if (!clean && !hasFiles) return false
  return true
}

/**
 * Infer why a user turn failed from the trailing assistant stub (if any).
 * @param {object | null | undefined} assistantMsg
 * @returns {'error' | 'stopped' | 'incomplete'}
 */
export function inferFailedReason(assistantMsg) {
  if (!assistantMsg || assistantMsg.role !== 'assistant') return 'incomplete'
  if (assistantMsg.stopped) return 'stopped'
  const text = typeof assistantMsg.text === 'string' ? assistantMsg.text : ''
  const hasFiles = Array.isArray(assistantMsg.files) && assistantMsg.files.length > 0
  const clean = stripErrorAnnotations(text)
  if (!clean && !hasFiles && text.includes('[Error:')) return 'error'
  return 'incomplete'
}

export const FAILED_REASON_LABEL = {
  error: '未收到回复（请求异常）',
  stopped: '未收到完整回复（已停止）',
  incomplete: '未收到回复',
}

/**
 * Mark orphaned user turns failed and drop incomplete assistant stubs.
 * @param {object[]} msgs
 * @returns {object[]}
 */
export function normalizeFailedTurns(msgs) {
  if (!Array.isArray(msgs) || !msgs.length) return []

  const out = []
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i]
    if (!m || typeof m !== 'object') continue

    if (m.role === 'assistant') {
      if (isCompleteAssistant(m)) out.push({ ...m, failed: false })
      continue
    }

    if (m.role !== 'user') {
      out.push(m)
      continue
    }

    const next = msgs[i + 1]
    if (isCompleteAssistant(next)) {
      out.push({ ...m, failed: false })
      out.push({ ...next, failed: false })
      i++
      continue
    }

    out.push({
      ...m,
      failed: true,
      failedReason: inferFailedReason(next?.role === 'assistant' ? next : null),
    })
    if (next?.role === 'assistant') i++
  }
  return out
}

/**
 * Apply turn outcome after streaming ends — mutates *msgs* in place.
 * @param {object[]} msgs
 * @param {object | null} userMsg
 * @param {object | null} assistantMsg
 * @param {TurnOutcome} outcome
 */
export function applyTurnOutcome(msgs, userMsg, assistantMsg, outcome) {
  if (!userMsg) return

  if (outcome === 'ok') {
    userMsg.failed = false
    delete userMsg.failedReason
    return
  }

  userMsg.failed = true
  userMsg.failedReason = outcome === 'ok' ? undefined : outcome

  if (!assistantMsg) return
  const idx = msgs.indexOf(assistantMsg)
  if (idx >= 0) msgs.splice(idx, 1)
}

/**
 * @param {object[]} msgs
 * @param {object | null} userMsg
 * @param {object | null} assistantMsg
 * @returns {TurnOutcome}
 */
export function resolveTurnOutcome(msgs, userMsg, assistantMsg) {
  if (!userMsg) return 'ok'

  const idx = assistantMsg != null ? msgs.indexOf(assistantMsg) : -1
  const asst = idx >= 0 ? msgs[idx] : assistantMsg

  if (!asst) return 'incomplete'
  if (asst.stopped) return 'stopped'
  const text = typeof asst.text === 'string' ? asst.text : ''
  const hasFiles = Array.isArray(asst.files) && asst.files.length > 0
  const clean = stripErrorAnnotations(text)
  if (clean || hasFiles) return 'ok'
  if (text.includes('[Error:')) return 'error'
  return 'incomplete'
}
