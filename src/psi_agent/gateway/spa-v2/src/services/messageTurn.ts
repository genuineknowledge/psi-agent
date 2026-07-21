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

export const FAILED_REASON_LABEL: Record<FailedReason, string> = {
  error: '未收到回复（请求异常）',
  stopped: '未收到完整回复（已停止）',
  incomplete: '未收到回复',
}
