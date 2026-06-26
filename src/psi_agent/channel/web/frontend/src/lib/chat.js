// Agent Session Protocol client.
//
// Talks to the web channel backend under API_BASE (default "/v1"):
//   POST   /sessions                      -> { session_id, created_at, expires_at }
//   DELETE /sessions/{id}                  -> 204
//   POST   /sessions/{id}/messages         -> SSE stream
//   POST   /files            (multipart)   -> { id, name, size }
//   GET    /files/{id}                     -> blob download
//
// Message SSE events (each `data: {...}` line):
//   { event: "text_delta", text }
//   { event: "file", url, name }
//   { event: "error", message }
//   { event: "done" }
// Stream terminates with `data: [DONE]`.

import { API_BASE } from './config.js'

export async function createSession({ workspace } = {}) {
  const resp = await fetch(`${API_BASE}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace: workspace || '' }),
  })
  if (!resp.ok) {
    const detail = await resp.text().catch(() => '')
    throw new Error(`创建会话失败 ${resp.status}${detail ? '：' + detail : ''}`)
  }
  return await resp.json()
}

export async function deleteSession(sessionId) {
  if (!sessionId) return
  await fetch(`${API_BASE}/sessions/${sessionId}`, { method: 'DELETE' }).catch(() => {})
}

export async function uploadFile(file) {
  const form = new FormData()
  form.append('file', file, file.name)
  const resp = await fetch(`${API_BASE}/files`, { method: 'POST', body: form })
  if (!resp.ok) {
    const detail = await resp.text().catch(() => '')
    throw new Error(`上传失败 ${resp.status}${detail ? '：' + detail : ''}`)
  }
  return await resp.json()
}

// Stream one message over SSE. onEvent(evt) is called for every parsed event.
export async function streamMessage({ sessionId, text, attachments, onEvent, signal }) {
  const resp = await fetch(`${API_BASE}/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, attachments: attachments || [] }),
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
