/** Whole-message SSE / transport keepalive tokens — never show as chat bubbles. */
const KEEPALIVE = new Set(['ping', 'pong'])

export function isSseKeepaliveText(text) {
  return typeof text === 'string' && KEEPALIVE.has(text.trim().toLowerCase())
}
