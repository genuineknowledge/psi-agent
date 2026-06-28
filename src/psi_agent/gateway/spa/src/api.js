const G = () => window.location.origin.replace(/\/+$/, '')

export async function api(method, path, body) {
  const r = await fetch(G() + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    const e = await r.json().catch(() => ({ error: r.statusText }))
    throw new Error(e.error || 'HTTP ' + r.status)
  }
  return await r.json()
}

export async function streamChat(sessionId, formData) {
  const r = await fetch(G() + '/sessions/' + sessionId + '/chat', { method: 'POST', body: formData })
  if (!r.ok) {
    const e = await r.json().catch(() => ({ error: r.statusText }))
    throw new Error(e.error || 'HTTP ' + r.status)
  }
  return r.body.getReader()
}

export function parseSSELine(line) {
  const s = line.trim()
  if (!s.startsWith('data:')) return null
  const p = s.slice(5).trim()
  if (p === '[DONE]' || !p) return null
  try { return JSON.parse(p) }
  catch (_) { return p.startsWith('{') || p.startsWith('[') ? null : { type: 'text', text: p } }
}
