/** Gateway client — one turn of chat, streamed. */
import { readSSE } from './sse.js'

/**
 * Send one message and yield events as they arrive.
 *
 * Yields `{type: 'text'|'reasoning'|'blob'|'error', ...}` — the same events the
 * gateway SPA consumes. `doc_token` + `user_id` go in the body on every turn
 * because the gateway re-derives the session from them; the client never gets to
 * name a session_id.
 */
export async function* streamChat({ baseUrl, token, docToken, userId, text, signal }) {
  const resp = await fetch(`${baseUrl}/docs-addon/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Psi-Addon-Token': token,
    },
    body: JSON.stringify({
      doc_token: docToken,
      user_id: userId,
      chunks: [{ type: 'text', text }],
    }),
    signal,
  })

  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}))
    throw new Error(detail.error || describeStatus(resp.status))
  }
  yield* readSSE(resp.body.getReader())
}

/** Turn the handful of statuses this endpoint really returns into actionable text. */
export function describeStatus(status) {
  switch (status) {
    case 401:
      return '访问令牌不正确，请在设置里检查 token'
    case 404:
      return '网关未启用小组件端点：启动时需带 --docs-addon-token'
    case 400:
      return '请求被网关拒绝（参数不完整）'
    default:
      return `网关返回 HTTP ${status}`
  }
}
