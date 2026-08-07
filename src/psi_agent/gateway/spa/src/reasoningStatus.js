/**
 * Helpers for Session `reasoning` SSE (model thinking + tool markers).
 * Markers follow Session agent.py: `[Tool Call: name(...)]` / `[Tool Result: …]`.
 */

const TOOL_CALL_RE = /\[Tool Call:\s*([A-Za-z0-9_.-]+)/g
const TOOL_RESULT_RE = /\[Tool Result:/g

/**
 * Latest human-readable status line for the collapsed reasoning header.
 * @param {string} reasoning
 * @returns {string}
 */
export function reasoningStatusLabel(reasoning) {
  const text = typeof reasoning === 'string' ? reasoning : ''
  if (!text.trim()) return '思考中…'

  let lastCall = ''
  for (const m of text.matchAll(TOOL_CALL_RE)) {
    lastCall = m[1] || lastCall
  }
  const resultMatches = [...text.matchAll(TOOL_RESULT_RE)]
  const callMatches = [...text.matchAll(TOOL_CALL_RE)]

  if (lastCall && resultMatches.length >= callMatches.length) {
    return `已完成 ${lastCall}…`
  }
  if (lastCall) {
    return `调用 ${lastCall}…`
  }
  return '思考中…'
}

/**
 * Whether the assistant row has any reasoning text worth showing.
 * @param {{ reasoning?: string } | null | undefined} msg
 */
export function hasReasoningText(msg) {
  return !!(msg && typeof msg.reasoning === 'string' && msg.reasoning.trim())
}
