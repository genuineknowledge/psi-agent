/**
 * Client-side helpers for `[SEND:/path]` markers that Channel also turns into blobs.
 * We keep delivery as opaque base64 chips — never re-resolve filesystem paths from text.
 */

/** Strip SEND markers from assistant text before markdown render / history display. */
export function stripSendMarkers(text) {
  if (!text) return ''
  return String(text)
    .replace(/\[SEND:[^\]]*\]/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
