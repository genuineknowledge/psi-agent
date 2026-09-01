/**
 * Whether *userMsg* already has at least one assistant bubble in *msgs*.
 * Used to choose append (reasoning split) vs insert-after-user (first segment, #327).
 * @param {object[]} msgs
 * @param {object | null | undefined} userMsg
 */
export function hasAssistantSegmentAfterUser(msgs, userMsg) {
  if (!Array.isArray(msgs) || !userMsg) return false
  const userIdx = msgs.indexOf(userMsg)
  if (userIdx < 0) return false
  for (let i = userIdx + 1; i < msgs.length; i++) {
    const m = msgs[i]
    if (m?.role === 'user') break
    if (m?.role === 'assistant') return true
  }
  return false
}
