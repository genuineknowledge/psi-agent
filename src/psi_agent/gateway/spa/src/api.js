import { assertMatchingTraceId, TRACE_ID_HEADER } from './traceId.js'

const G = () => window.location.origin.replace(/\/+$/, '')

export async function api(method, path, body, { signal } = {}) {
  const r = await fetch(G() + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
    signal,
  })
  if (!r.ok) {
    const e = await r.json().catch(() => ({ error: r.statusText }))
    throw new Error(e.error || 'HTTP ' + r.status)
  }
  return await r.json()
}

export async function fetchWorkspacePlaces() {
  return api('GET', '/workspace/places')
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

export async function streamChat(sessionId, formData, signal, traceId) {
  const r = await fetch(G() + '/sessions/' + sessionId + '/chat', {
    method: 'POST',
    body: formData,
    headers: { [TRACE_ID_HEADER]: traceId },
    signal,
  })
  const responseTraceId = r.headers?.get?.(TRACE_ID_HEADER)
  if (responseTraceId !== null && responseTraceId !== undefined) {
    assertMatchingTraceId(responseTraceId, traceId)
  }
  if (!r.ok) {
    const e = await r.json().catch(() => ({ error: r.statusText }))
    throw new Error(e.error || 'HTTP ' + r.status)
  }
  return r.body.getReader()
}

/** Step 2: GET /defaults (agent package + user workspace). Same contract as spa-v2. */
export async function fetchDefaults(signal) {
  return api('GET', '/defaults', undefined, { signal })
}
