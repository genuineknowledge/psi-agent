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
  agent?: string
  channel_socket: string
  /** Gateway 的 SessionInfo dataclass 经 ``asdict`` 整体序列化，故这两个字段
   *  一直在响应里；``ai_id`` 是 ``backend_type === 'ai'`` 时由 server.py 从
   *  ``backend_id`` 派生出来的别名。非 ai 后端只有 backend_id。 */
  backend_type?: string
  backend_id?: string
}

export type GatewayDefaults = {
  agent: string
  workspace: string
}

export type AiInfo = {
  id: string
  provider: string
  model: string
  base_url: string
  /** Present on GET /ais; used to detect free-path ``haitun-default``. */
  api_key?: string
  /** Gateway 的 AiInfo dataclass 有 socket 字段，``asdict`` 会一并序列化，
   *  所以 GET/POST /ais 的响应里一直有它。 */
  socket?: string
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

/** Step 2: GET /defaults — shared by spa v1/v2 (agent package + user workspace). */
export async function fetchDefaults() {
  return api<GatewayDefaults>('GET', '/defaults')
}

/** Step 2: optional ``agent`` is passed through to Gateway → Session (#472). */
export async function createSession(
  aiId: string,
  workspace: string,
  opts: { agent?: string; id?: string } = {},
) {
  return api<SessionInfo>('POST', '/sessions', {
    ai_id: aiId,
    workspace,
    ...(opts.agent ? { agent: opts.agent } : {}),
    ...(opts.id ? { id: opts.id } : {}),
  })
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

export async function listSummaries() {
  return api<Record<string, string>>('GET', '/summaries')
}

export async function generateSummary(sessionId: string, userText: string, assistantText: string) {
  return api<{ id: string; summary: string | null }>('POST', '/summaries/generate', {
    id: sessionId,
    user_text: userText,
    assistant_text: assistantText,
  })
}

export type HistoryToolCall = {
  name: string
  arguments: string
}

export type HistoryMessage = {
  role: 'user' | 'assistant'
  text: string
  /** Provenance from Session JSONL (`kind`); omitted for ordinary chat. */
  kind?: string
  /** ``[SEND:]`` paths extracted before marker strip (assistant turns). */
  sends?: string[]
  /** Session JSONL thinking prose only (not tool markers). */
  reasoning?: string
  /** Structured tool_calls projected for SPA tool list (separate from reasoning). */
  tools?: HistoryToolCall[]
}

export async function fetchHistory(sessionId: string) {
  return api<HistoryMessage[]>('GET', `/sessions/${sessionId}/history`)
}

export type SessionTodo = {
  id: string
  content: string
  status: 'pending' | 'in_progress' | 'completed' | 'cancelled' | string
}

export type SessionTodosResponse = {
  todos: SessionTodo[]
  summary: {
    total: number
    pending: number
    in_progress: number
    completed: number
    cancelled: number
  }
}

/** Workspace ``.psi/todos/{sessionId}.json`` via the ``todo`` tool. */
export async function fetchSessionTodos(sessionId: string) {
  return api<SessionTodosResponse>('GET', `/sessions/${sessionId}/todos`)
}

export type TodoSegmentSummary = {
  id: string
  label: string
  created_at: string
  updated_at: string
  closed_at: string | null
  source: string
  summary: SessionTodosResponse['summary']
}

export type TodoSegmentDetail = TodoSegmentSummary & {
  todos: SessionTodo[]
}

/** Sub-task segments from ``todo`` merge=false boundaries. */
export async function listTodoSegments(sessionId: string) {
  return api<TodoSegmentSummary[]>('GET', `/sessions/${sessionId}/todo-segments`)
}

export async function fetchTodoSegment(sessionId: string, segmentId: string) {
  return api<TodoSegmentDetail>('GET', `/sessions/${sessionId}/todo-segments/${segmentId}`)
}

/** P1: override segment label (e.g. turn summary). */
export async function setTodoSegmentLabel(sessionId: string, segmentId: string, label: string) {
  return api<TodoSegmentDetail>('POST', `/sessions/${sessionId}/todo-segments/${segmentId}`, { label })
}

export async function readWorkspaceFile(path: string, root = '') {
  const params = new URLSearchParams({ path })
  if (root) params.set('root', root)
  return api<{ name: string; data: string; path: string }>('GET', `/workspace/file?${params.toString()}`)
}

/** Open the OS file manager and select ``path`` (Gateway local desktop). */
export async function revealWorkspacePath(path: string) {
  return api<{ path: string; ok: boolean }>('POST', '/workspace/reveal', { path })
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

// ---------------------------------------------------------------- 认证 (/auth/*)
//
// 这些接口默认存在（认证地址有内置默认值）；把 `PSI_AUTH_ENDPOINT` 显式设成空值
// 才会关掉认证, 此时全部 404。旧版网关也没有这些路由。
// 前端必须先探 `getAuthStatus()`，据 `available` 决定显示登录入口还是「本地模式」
// 说明 —— 不能假定端点一定在。
//
// token 全程由 Gateway 侧持有并加密落盘，**前端拿不到也不该存 token**：
// 页面脚本一旦持有凭证，XSS 即等于凭证泄露。

export type AuthStatus = {
  /** Gateway 是否配了云端地址；false 时其余字段无意义 */
  available: boolean
  endpoint: string
  loggedIn: boolean
  deviceKey: string
  platform: string
  /** 钥匙串不可用时为 false —— 界面应提示「凭证未加密」而非假装安全 */
  credentialEncrypted: boolean
}

export type AuthUser = {
  id: string
  displayName: string | null
  avatarUrl: string | null
  createdAt: string
}

export type AuthIdentity = {
  provider: string
  identifier: string
  verifiedAt?: string | null
}

export type AuthDevice = {
  id: string
  platform: string
  name: string | null
  createdAt: string
  lastSeenAt: string | null
  current: boolean
}

export type SendCodeResult = { retryAfter: number }

/**
 * 认证接口的错误。
 *
 * 通用 `api()` 只把 `error` 塞进 message，把响应体其余字段丢掉。但登录界面要用
 * 两个：`retryAfter`（D2 按钮内倒计时）与 `remaining`（D1 剩余尝试次数）。丢了它们，
 * 界面只能自己猜秒数、猜次数 —— 猜错比不显示更糟。
 */
export class AuthApiError extends Error {
  readonly status: number
  readonly retryAfter?: number
  readonly remaining?: number

  constructor(code: string, status: number, retryAfter?: number, remaining?: number) {
    super(code)
    this.name = 'AuthApiError'
    this.status = status
    this.retryAfter = retryAfter
    this.remaining = remaining
  }
}

/** 认证专用请求：与 `api()` 同契约，但失败时抛 AuthApiError 保留限频字段。 */
async function authApi<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(G() + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    const e = (await r.json().catch(() => ({}))) as {
      error?: string
      retryAfter?: number
      remaining?: number
      remainingAttempts?: number
    }
    throw new AuthApiError(
      e.error || `HTTP ${r.status}`,
      r.status,
      typeof e.retryAfter === 'number' ? e.retryAfter : undefined,
      typeof e.remaining === 'number'
        ? e.remaining
        : typeof e.remainingAttempts === 'number'
          ? e.remainingAttempts
          : undefined,
    )
  }
  if (r.status === 204) return undefined as T
  return (await r.json()) as T
}

/** 校验结果：老用户当场登录完成，新用户看 `registrationRequired` 转去填昵称再
 * completeAuth。
 *
 * 没有 `tempToken` 字段：那枚凭证由 Gateway 扣在进程内，不下发到页面。 */
export type VerifyResult = {
  token?: string
  isNewUser?: boolean
  user?: AuthUser
  /**
   * 新用户标记。Gateway 把注册凭证 `tempToken` 扣在本进程、不下发给页面，
   * 这个布尔值是它留给页面的替代信号：为真就进建号屏。
   */
  registrationRequired?: boolean
}

/** 探测认证是否可用。404 表示这个 Gateway 没开认证（或版本旧），不是错误。 */
export async function getAuthStatus(): Promise<AuthStatus> {
  const r = await fetch(G() + '/auth/status')
  if (r.status === 404) {
    return {
      available: false,
      endpoint: '',
      loggedIn: false,
      deviceKey: '',
      platform: '',
      credentialEncrypted: false,
    }
  }
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  const data = (await r.json()) as Omit<AuthStatus, 'available'>
  return { ...data, available: true }
}

export async function sendAuthCode(body: { phone?: string; email?: string }): Promise<SendCodeResult> {
  return authApi<SendCodeResult>('POST', '/auth/send-code', body)
}

// 两段式注册的 tempToken **整个不进浏览器**：Gateway 的 AuthManager 在 verify 时
// 扣下它、在 complete 时自己取用，响应体里已经把该字段剥掉。
//
// 早先的写法是在本文件放一个模块级 `let _pendingTempToken` 暂存。那样凭证仍然进了
// 页面脚本的作用域（XSS 即可读走），而且违反「模块不留可变全局」——两个问题同一处
// 解决：状态挪到进程侧，前端连变量都不需要。
export async function verifyAuthCode(body: {
  code: string
  phone?: string
  email?: string
}): Promise<VerifyResult> {
  return authApi<VerifyResult>('POST', '/auth/verify', body)
}

export async function completeAuth(body: { displayName?: string } = {}): Promise<{
  token?: string
  user?: AuthUser
}> {
  return authApi<{ token?: string; user?: AuthUser }>('POST', '/auth/complete', body)
}

export async function getAuthMe(): Promise<{ user: AuthUser; identities: AuthIdentity[] }> {
  // 线上云端 /me 返回扁平 UserOut {id, displayName, avatarUrl, identities}，
  // 而界面按 {user, identities} 消费 —— 在此适配，兼容两种形状、缺字段给默认，
  // 避免登录后渲染崩成白屏。
  const raw = await api<Record<string, unknown>>('GET', '/auth/me')
  const u = (raw.user ?? raw) as Record<string, unknown>
  const ids = (raw.identities ?? u.identities ?? []) as AuthIdentity[]
  return {
    user: {
      id: String(u.id ?? ''),
      displayName: (u.displayName as string | null) ?? null,
      avatarUrl: (u.avatarUrl as string | null) ?? null,
      createdAt: String(u.createdAt ?? ''),
    },
    identities: Array.isArray(ids) ? ids : [],
  }
}

export async function authLogout(): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>('POST', '/auth/logout')
}

/** 已登录态下绑定手机号/邮箱到当前账号（R2）。复用发码，校验走 /auth/bind。 */
export async function bindAuthIdentity(body: {
  code: string
  phone?: string
  email?: string
}): Promise<{ ok?: boolean }> {
  return api<{ ok?: boolean }>('POST', '/auth/bind', body)
}

/** 解绑一种登录方式（R2）。云端拦截「解绑最后一个身份」，返回 409 conflict。 */
export async function unbindAuthIdentity(provider: 'phone' | 'email'): Promise<unknown> {
  return api<unknown>('DELETE', `/auth/identities/${provider}`)
}

export async function listAuthDevices(): Promise<{ devices: AuthDevice[] }> {
  // 线上云端 /sessions 返回裸数组（字段 lastUsedAt），界面按 {devices:[…]}（lastSeenAt）
  // 消费 —— 在此归一化，兼容裸数组 / {devices} / {sessions} 三种形状。
  const raw = await api<unknown>('GET', '/auth/devices')
  const arr = Array.isArray(raw)
    ? raw
    : ((raw as { devices?: unknown[]; sessions?: unknown[] })?.devices
      ?? (raw as { sessions?: unknown[] })?.sessions
      ?? [])
  const devices: AuthDevice[] = (arr as Record<string, unknown>[]).map((d) => ({
    id: String(d.id ?? ''),
    platform: String(d.platform ?? ''),
    name: (d.name as string | null) ?? null,
    createdAt: String(d.createdAt ?? ''),
    lastSeenAt: (d.lastSeenAt as string | null) ?? (d.lastUsedAt as string | null) ?? null,
    current: Boolean(d.current),
  }))
  return { devices }
}

export async function revokeAuthDevice(deviceId: string): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>('DELETE', `/auth/devices/${encodeURIComponent(deviceId)}`)
}

/** 问卷弹窗是否已被关闭。服务端存 —— Gateway 每次启动换随机端口，
 *  origin 跟着变，`localStorage` 的标记下次读不到。 */
export async function fetchSurveyDone() {
  return api<{ done: boolean }>('GET', '/ui/prefs/survey')
}

export async function markSurveyDone() {
  return api<{ done: boolean }>('POST', '/ui/prefs/survey', { done: true })
}

