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

export async function fetchWorkspaceRoots() {
  return api('GET', '/workspace/roots')
}

export async function browseWorkspace(path, { kind = 'directory', q = '' } = {}) {
  const params = new URLSearchParams()
  if (path) params.set('path', path)
  if (kind) params.set('kind', kind)
  if (q) params.set('q', q)
  const r = await fetch(`${G()}/workspace/browse?${params.toString()}`)
  if (!r.ok) {
    const e = await r.json().catch(() => ({ error: r.statusText }))
    throw new Error(e.error || 'HTTP ' + r.status)
  }
  return r.json()
}

export async function streamChat(sessionId, formData, signal) {
  const r = await fetch(G() + '/sessions/' + sessionId + '/chat', { method: 'POST', body: formData, signal })
  if (!r.ok) {
    const e = await r.json().catch(() => ({ error: r.statusText }))
    throw new Error(e.error || 'HTTP ' + r.status)
  }
  return r.body.getReader()
}
