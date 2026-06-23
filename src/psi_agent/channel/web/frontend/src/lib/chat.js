// Calls the real backend at /api/chat and streams the reply.
//
// Request body:  { message, modules, compare }
// Response:      SSE lines `data: {...}\n\n`, where each event may carry:
//   { reasoning } | { content } | { error }
// and, when compare is on, a channel marker:
//   { channel: "dolphin" | "hermes", ... }
// Stream terminates with `data: [DONE]`.
//
// onEvent(evt) is called for every parsed event object.

export async function streamChat({ message, modules, compare, onEvent, signal }) {
  const resp = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, modules, compare }),
    signal,
  })

  if (!resp.ok) {
    const detail = await resp.text().catch(() => '')
    throw new Error(`后端返回 ${resp.status}${detail ? '：' + detail : ''}`)
  }
  if (!resp.body) {
    throw new Error('后端未返回流式响应')
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop()
    for (const part of parts) {
      const line = part.trim()
      if (!line.startsWith('data:')) continue
      const data = line.slice(5).trim()
      if (data === '[DONE]') return
      let evt
      try { evt = JSON.parse(data) } catch { continue }
      onEvent(evt)
    }
  }
}
