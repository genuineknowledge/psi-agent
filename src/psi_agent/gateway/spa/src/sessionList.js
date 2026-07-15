export const PINNED_SESSIONS_KEY = 'gw-pinned-session-ids'

export function getSessionDisplayName(session, titles = {}) {
  if (session && titles && titles[session.id]) {
    return titles[session.id]
  }
  return session?.workspace || '新会话'
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
    // 仅显示已生成标题的会话：新对话在首轮问答生成标题前不进侧栏，
    // 标题写入 titles[id] 后自动出现（对齐 Gemini），避免显示丑陋的 workspace 路径。
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
