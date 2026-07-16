/**
 * Client-side helpers for Channel file-transfer markers embedded in message text.
 *
 * Wire protocol (Channel ↔ Session) encodes uploads as ``[RECV:/abs/path]`` and
 * deliveries as ``[SEND:/path]``. Those absolute paths must not surface in the
 * Web Console UI — SPA shows opaque base64 chips instead, and never re-resolves
 * filesystem paths from text.
 *
 * **Transition:** this is the current strip implementation (stream + history
 * reload). JSONL / ``GET /history`` may still contain raw markers. New wire
 * markers must be registered in ``gateway/AGENTS.md`` 「Wire 标记登记表」and
 * added here until the authoritative strip moves to Gateway HistoryManager.
 * See gateway/SPA AGENTS 「展示剥离约定」.
 */

/** Strip ``[SEND:…]`` / ``[RECV:…]`` before bubble render / copy / local cache. */
export function stripTransferMarkers(text) {
  if (!text) return ''
  return String(text)
    .replace(/\[SEND:[^\]]*\]/g, '')
    .replace(/\[RECV:[^\]]*\]/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/** @deprecated Prefer ``stripTransferMarkers`` (strips SEND and RECV). */
export function stripSendMarkers(text) {
  return stripTransferMarkers(text)
}
