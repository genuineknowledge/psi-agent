/**
 * Client-side helpers for Channel file-transfer markers embedded in message text.
 *
 * Wire protocol (Channel ↔ Session) encodes uploads as ``[RECV:/abs/path]`` and
 * deliveries as ``[SEND:/abs/path]``. Those absolute paths must not surface in the
 * Web Console UI — SPA shows opaque base64 chips instead, and never re-resolves
 * filesystem paths from text.
 *
 * This strip is a **presentation-layer** filter (incl. history reload). Server
 * JSONL may still store the markers for Session; a longer-term fix is separating
 * display history from wire content (or stripping at HistoryManager read).
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
