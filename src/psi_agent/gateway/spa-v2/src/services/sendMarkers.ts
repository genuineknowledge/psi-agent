/**
 * Strip ``[SEND:…]`` / ``[RECV:…]`` before bubble render / copy.
 * Presentation-layer only — same contract as spa v1 ``sendMarkers.js``.
 */
export function stripTransferMarkers(text: string): string {
  if (!text) return ''
  return String(text)
    .replace(/\[SEND:[^\]]*\]/g, '')
    .replace(/\[RECV:[^\]]*\]/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
