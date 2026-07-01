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
