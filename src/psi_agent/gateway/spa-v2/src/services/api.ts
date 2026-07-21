/** Gateway HTTP helpers — same contract as spa v1 `api.js`. */

const G = () => window.location.origin.replace(/\/+$/, '')

export async function api<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const r = await fetch(G() + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    const e = (await r.json().catch(() => ({ error: r.statusText }))) as { error?: string }
    throw new Error(e.error || `HTTP ${r.status}`)
  }
  if (r.status === 204) return undefined as T
  return (await r.json()) as T
}

export type SessionInfo = {
  id: string
  ai_id: string
  workspace: string
  channel_socket: string
}

export type AiInfo = {
  id: string
  provider: string
  model: string
  base_url: string
}

export async function createAi(body: {
  provider: string
  model: string
  api_key: string
  base_url: string
  id?: string
}) {
  return api<AiInfo>('POST', '/ais', body)
}

export async function deleteAi(aiId: string) {
  return api('DELETE', `/ais/${aiId}`)
}

export async function listAis() {
  return api<AiInfo[]>('GET', '/ais')
}

export async function bootstrapAi() {
  return api<AiInfo | { skipped: boolean }>('POST', '/ais/bootstrap')
}

export async function listSessions() {
  return api<SessionInfo[]>('GET', '/sessions')
}

export async function createSession(aiId: string, workspace: string) {
  return api<SessionInfo>('POST', '/sessions', { ai_id: aiId, workspace })
}

export async function deleteSession(sessionId: string) {
  return api('DELETE', `/sessions/${sessionId}`)
}

export async function listTitles() {
  return api<Record<string, string>>('GET', '/titles')
}

export async function setTitle(sessionId: string, title: string) {
  return api('POST', '/titles', { id: sessionId, title })
}

export async function generateTitle(sessionId: string, userText: string, assistantText: string) {
  return api<{ id: string; title: string | null }>('POST', '/titles/generate', {
    id: sessionId,
    user_text: userText,
    assistant_text: assistantText,
  })
}

export type HistoryMessage = {
  role: 'user' | 'assistant'
  text: string
  /** Provenance from Session JSONL (`kind`); omitted for ordinary chat. */
  kind?: string
}

export async function fetchHistory(sessionId: string) {
  return api<HistoryMessage[]>('GET', `/sessions/${sessionId}/history`)
}

export async function fetchCwd() {
  return api<{ cwd: string }>('GET', '/workspace/cwd')
}

export async function fetchWorkspaceRoots() {
  return api<{ roots: { path: string; label?: string }[] } | string[]>('GET', '/workspace/roots')
}

export type WorkspacePlace = { id: string; label: string; path: string }
export type WorkspaceDrive = { label: string; path: string }
export type BrowseEntry = { name: string; path: string; kind: 'directory' | 'file' | string }
export type BrowseResult = {
  path: string
  parent?: string
  segments?: { name: string; path: string }[]
  entries?: BrowseEntry[]
}

export async function fetchWorkspacePlaces() {
  return api<{ places: WorkspacePlace[]; drives: WorkspaceDrive[] }>('GET', '/workspace/places')
}

export async function browseWorkspace(
  path: string,
  opts: { kind?: 'directory' | 'file' | 'all'; q?: string } = {},
) {
  const params = new URLSearchParams()
  if (path) params.set('path', path)
  params.set('kind', opts.kind || 'directory')
  if (opts.q) params.set('q', opts.q)
  return api<BrowseResult>('GET', `/workspace/browse?${params.toString()}`)
}

export async function streamChat(
  sessionId: string,
  formData: FormData,
  signal?: AbortSignal,
): Promise<ReadableStreamDefaultReader<Uint8Array>> {
  const r = await fetch(G() + `/sessions/${sessionId}/chat`, {
    method: 'POST',
    body: formData,
    signal,
  })
  if (!r.ok) {
    const e = (await r.json().catch(() => ({ error: r.statusText }))) as { error?: string }
    throw new Error(e.error || `HTTP ${r.status}`)
  }
  if (!r.body) throw new Error('No response body')
  return r.body.getReader()
}
