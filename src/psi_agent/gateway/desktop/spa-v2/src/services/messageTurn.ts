import type { ChatMessage, FailedReason } from '../haitun-agent/model'

/** Remove trailing/inline `[Error: …]` annotations (spa v1 parity). */
export function stripErrorAnnotations(text: string): string {
  if (!text) return ''
  return String(text)
    .replace(/\n?\[Error:[^\]]*\]/g, '')
    .replace(/\n?\[错误\][^\n]*/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/** Whether an agent message counts as a complete reply (spa v1 `isCompleteAssistant`). */
export function isCompleteAgent(msg: ChatMessage | null | undefined): boolean {
  if (!msg || msg.role !== 'agent') return false
  if (msg.stopped) return false
  const text = typeof msg.text === 'string' ? msg.text : ''
  const hasFiles = Array.isArray(msg.files) && msg.files.length > 0
  const clean = stripErrorAnnotations(text)
  return !!clean || hasFiles
}

/**
 * Infer why a user turn failed from the trailing agent stub (if any).
 * spa v1 `inferFailedReason` — agent role instead of assistant.
 */
export function inferFailedReason(agentMsg: ChatMessage | null | undefined): FailedReason {
  if (!agentMsg || agentMsg.role !== 'agent') return 'incomplete'
  if (agentMsg.stopped) return 'stopped'
  const text = typeof agentMsg.text === 'string' ? agentMsg.text : ''
  const hasFiles = Array.isArray(agentMsg.files) && agentMsg.files.length > 0
  const clean = stripErrorAnnotations(text)
  if (!clean && !hasFiles && (text.includes('[Error:') || text.includes('[错误]'))) return 'error'
  return 'incomplete'
}

export const FAILED_REASON_LABEL: Record<FailedReason, string> = {
  error: '未收到回复（请求异常）',
  stopped: '未收到完整回复（已停止）',
  incomplete: '未收到回复',
}

/**
 * Mark orphaned user turns failed and drop incomplete agent stubs.
 * Used after `/history` projection so refresh surfaces a retry control when
 * Session committed the user message but never a displayable assistant reply.
 */
export function normalizeFailedTurns(msgs: ChatMessage[]): ChatMessage[] {
  if (!Array.isArray(msgs) || !msgs.length) return []

  const out: ChatMessage[] = []
  for (let i = 0; i < msgs.length; i++) {
    const m = msgs[i]
    if (!m || typeof m !== 'object') continue

    if (m.role === 'agent') {
      if (isCompleteAgent(m)) out.push({ ...m, failed: false })
      continue
    }

    if (m.role !== 'user') {
      out.push(m)
      continue
    }

    const next = msgs[i + 1]
    if (isCompleteAgent(next)) {
      out.push({ ...m, failed: false })
      out.push({ ...next!, failed: false })
      i++
      continue
    }

    out.push({
      ...m,
      failed: true,
      failedReason: inferFailedReason(next?.role === 'agent' ? next : null),
    })
    if (next?.role === 'agent') i++
  }
  return out
}
