export const PINNED_SESSIONS_KEY = 'gw-pinned-session-ids'
/** Placeholder shown in sidebar as soon as the user sends the first message. */
export const PLACEHOLDER_SESSION_TITLE = '新对话'

export function isPlaceholderSessionTitle(title) {
  if (typeof title !== 'string' || !title.trim()) return true
  return title === PLACEHOLDER_SESSION_TITLE || title === '新会话'
}

export function getSessionDisplayName(session, titles = {}) {
  if (session && titles && titles[session.id]) {
    return titles[session.id]
  }
  return PLACEHOLDER_SESSION_TITLE
}

/** Normalize workspace paths for grouping (forward slashes, no trailing slash). */
export function normalizeWorkspacePath(path) {
  if (typeof path !== 'string') return ''
  let p = path.trim().replace(/\\/g, '/')
  if (!p) return ''
  if (p.length > 1) p = p.replace(/\/+$/, '')
  return p
}

/** Last path segment for sidebar display. */
export function getWorkspaceLabel(path) {
  const p = normalizeWorkspacePath(path)
  if (!p) return '工作区'
  const parts = p.split('/').filter(Boolean)
  return parts[parts.length - 1] || p
}

/** Session workspace: explicit path, else Gateway cwd when empty. */
export function resolveSessionWorkspace(session, defaultCwd = '') {
  const n = normalizeWorkspacePath(session?.workspace ?? '')
  if (n) return n
  return normalizeWorkspacePath(defaultCwd)
}

export function mergeWorkspacePaths(registered, sessions, defaultCwd = '') {
  const seen = new Set()
  const result = []
  const add = (raw) => {
    const n = normalizeWorkspacePath(raw)
    if (!n || seen.has(n)) return
    seen.add(n)
    result.push(n)
  }
  if (Array.isArray(registered)) {
    for (const p of registered) add(p)
  }
  if (Array.isArray(sessions)) {
    for (const s of sessions) add(resolveSessionWorkspace(s, defaultCwd))
  }
  return result
}

export function sessionsForWorkspace(sessions, workspacePath, defaultCwd = '') {
  const target = normalizeWorkspacePath(workspacePath)
  if (!Array.isArray(sessions)) return []
  return sessions.filter(s => resolveSessionWorkspace(s, defaultCwd) === target)
}

export function buildWorkspaceGroups(
  sessions,
  {
    registered = [],
    defaultCwd = '',
    titles = {},
    query = '',
    pinnedIds = [],
    requireTitle = true,
  } = {},
) {
  const paths = mergeWorkspacePaths(registered, sessions, defaultCwd)
  const normalizedQuery = query.trim().toLowerCase()
  return paths
    .map(path => {
      const workspaceSessions = sessionsForWorkspace(sessions, path, defaultCwd)
      const visibleSessions = buildVisibleSessions(workspaceSessions, {
        titles,
        query,
        pinnedIds,
        requireTitle,
      })
      const label = getWorkspaceLabel(path)
      const searchable = [label, path].join('\n').toLowerCase()
      return { path, label, sessions: visibleSessions, searchable }
    })
    .filter(group => {
      if (!normalizedQuery) return true
      if (group.searchable.includes(normalizedQuery)) return true
      return group.sessions.length > 0
    })
}

function normalizeIdList(ids) {
  const seen = new Set()
  const result = []
  if (!Array.isArray(ids)) return result
  ids.forEach((id) => {
    if (typeof id !== 'string') return
    const trimmed = id.trim()
    if (!trimmed || seen.has(trimmed)) return
    seen.add(trimmed)
    result.push(trimmed)
  })
  return result
}

export function loadPinnedSessionIds(storage = window.localStorage) {
  try {
    return normalizeIdList(JSON.parse(storage.getItem(PINNED_SESSIONS_KEY) || '[]'))
  } catch (_) {
    return []
  }
}

export function savePinnedSessionIds(storage = window.localStorage, ids = []) {
  storage.setItem(PINNED_SESSIONS_KEY, JSON.stringify(normalizeIdList(ids)))
}

export function togglePinnedSessionId(ids, id) {
  const normalized = normalizeIdList(ids)
  if (normalized.includes(id)) {
    return normalized.filter(existing => existing !== id)
  }
  return [...normalized, id]
}

export function buildSessionTitlePayload(session, title) {
  return {
    id: session.id,
    title: title.trim(),
  }
}

function hasTitle(session, titles) {
  const t = session && titles ? titles[session.id] : ''
  return typeof t === 'string' && t.trim() !== ''
}

export function buildVisibleSessions(sessions, { titles = {}, query = '', pinnedIds = [], requireTitle = true } = {}) {
  const normalizedQuery = query.trim().toLowerCase()
  const pinned = new Set(normalizeIdList(pinnedIds))
  return sessions
    .filter(session => !requireTitle || hasTitle(session, titles))
    .map((session, index) => ({
      session,
      index,
      pinned: pinned.has(session.id),
      searchable: [
        getSessionDisplayName(session, titles),
        session.workspace || '',
        session.id || '',
      ].join('\n').toLowerCase(),
    }))
    .filter(item => !normalizedQuery || item.searchable.includes(normalizedQuery))
    .sort((a, b) => {
      if (a.pinned !== b.pinned) return a.pinned ? -1 : 1
      return a.index - b.index
    })
    .map(item => item.session)
}
