import type { ChatFile, ChatMessage, DeliveryState, Task } from '../haitun-agent/model'
import type { HistoryMessage, HistoryToolCall, SessionInfo, SessionTodo } from './api'
import { stripTransferMarkers } from './sendMarkers'
import { applyTaskProgress } from './taskProgress'
import { summarizeToolCall } from './turnProgress'
import { translate, DEFAULT_LANGUAGE, type Language } from '../i18n'

const ACCENTS = ['#007bff', '#27a06b', '#d8a62a', '#ff6b57', '#4d8eff', '#7c5cfc']

export function shortTitleOf(title: string, max = 10, language: Language = DEFAULT_LANGUAGE): string {
  const t = title.trim() || translate(language, 'app.newTaskDefault')
  return t.length > max ? `${t.slice(0, max)}…` : t
}

export function titleFromPrompt(description: string, language: Language = DEFAULT_LANGUAGE): string {
  const clean = description.split(/[。！？\n]/)[0]?.trim() || translate(language, 'app.newTaskDefault')
  return clean.slice(0, 30)
}

/**
 * Task title from chat history — DeepSeek-style: first user message.
 * Withdrawn / abandoned turns must not appear in history, so they cannot
 * become the title either.
 */
export function titleFromHistoryMessages(
  messages: Array<{ role: string; text?: string }>,
  language: Language = DEFAULT_LANGUAGE,
): string {
  const firstUser = messages.find((m) => m.role === 'user' && (m.text ?? '').trim())
  if (!firstUser?.text?.trim()) return translate(language, 'app.newTaskDefault')
  return titleFromPrompt(firstUser.text, language)
}

export function workspaceLabel(path: string, language: Language = DEFAULT_LANGUAGE): string {
  const p = path.replace(/\\/g, '/').replace(/\/+$/, '')
  const parts = p.split('/').filter(Boolean)
  return parts[parts.length - 1] || p || translate(language, 'session.workspaceFallback')
}

export function basenameOf(path: string): string {
  const n = path.replace(/\\/g, '/').split('/').filter(Boolean)
  return n[n.length - 1] || path
}

/** Extract ``[SEND:path]`` values in order (parity with backend ``extract_send_paths``). */
export function extractSendPaths(text: string): string[] {
  const out: string[] = []
  const re = /\[\s*SEND\s*:\s*([^\]]*?)\s*\]/gi
  let m: RegExpExecArray | null
  while ((m = re.exec(text ?? '')) !== null) {
    const p = m[1]?.trim()
    if (p) out.push(p)
  }
  return out
}

/** Map file basenames to their absolute paths for preview reload. */
export function pathsByName(paths: string[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const p of paths) out[basenameOf(p)] = p
  return out
}

/**
 * Project Gateway `/history` rows into workspace chat bubbles.
 * Server already whitelists by ``kind``; still strip transfer markers and drop empties
 * (parity with spa v1 useSession / historyReconcile).
 * Assistant ``sends`` become file stubs (name + path, empty data) so chat chips
 * survive refresh and can lazy-load via ``GET /workspace/file``.
 *
 * **刻意为之**：连续 `assistant` 行合并成一个 agent 气泡（files 去重合并）。
 * Session 在每轮 `tool_calls` 都会把带正文的 assistant 落盘，todo 多步时 JSONL 常有
 * 「Step N ✅ …」+ 短计划各占一行；流式 UI 累进临时气泡，结算只留最后一段。
 * 刷新合并时同样**只保留最后一段**正文，前面的步骤叙述丢弃（不对齐进工具区）。
 */
export function historyToChat(
  messages: HistoryMessage[],
  language: Language = DEFAULT_LANGUAGE,
): ChatMessage[] {
  const out: ChatMessage[] = []
  for (const m of messages) {
    // Defense in depth: never surface silent schedule rows if a proxy leaks them.
    if (m.kind === 'schedule.silent') continue
    const text = stripTransferMarkers(typeof m.text === 'string' ? m.text : '')
    const files = filesFromHistorySends(m)
    // Empty text + no files → skip (SEND-only rows still feed historyToDeliverables).
    if (!text.trim() && !files.length) continue
    // Pure SEND bubble (no prose): still skip chat row; chest owns those files.
    if (!text.trim()) continue
    const role = m.role === 'assistant' ? 'agent' : 'user'
    const reasoning =
      role === 'agent' && typeof m.reasoning === 'string' && m.reasoning.trim()
        ? m.reasoning
        : undefined
    const tools = role === 'agent' ? toolSummariesFromHistory(m.tools, language) : []
    const last = out[out.length - 1]
    if (role === 'agent' && last?.role === 'agent') {
      const mergedFiles = mergeChatFiles(last.files, files)
      const mergedReasoning = [last.reasoning, reasoning]
        .filter((r): r is string => typeof r === 'string' && !!r.trim())
        .join('\n')
      const mergedTools = mergeToolLines(last.tools, tools)
      const { interimText: _dropInterim, ...rest } = last
      out[out.length - 1] = {
        ...rest,
        // Only the last tool-round prose remains as the bubble body.
        text,
        ...(mergedFiles.length ? { files: mergedFiles } : {}),
        ...(mergedReasoning ? { reasoning: mergedReasoning } : {}),
        ...(mergedTools.length ? { tools: mergedTools } : {}),
      }
      continue
    }
    out.push({
      role,
      text,
      ...(files.length ? { files } : {}),
      ...(reasoning ? { reasoning } : {}),
      ...(tools.length ? { tools } : {}),
    })
  }
  return out
}

function toolSummariesFromHistory(
  tools: HistoryToolCall[] | undefined,
  language: Language,
): string[] {
  if (!Array.isArray(tools) || !tools.length) return []
  const out: string[] = []
  for (const t of tools) {
    if (!t || typeof t.name !== 'string' || !t.name.trim()) continue
    const args = typeof t.arguments === 'string' ? t.arguments : '{}'
    const line = summarizeToolCall(t.name, args, language)
    if (out[out.length - 1] === line) continue
    out.push(line)
  }
  return out
}

function mergeToolLines(
  a: string[] | undefined,
  b: string[] | undefined,
): string[] {
  const out: string[] = []
  for (const line of [...(a ?? []), ...(b ?? [])]) {
    if (!line.trim()) continue
    if (out[out.length - 1] === line) continue
    out.push(line)
  }
  return out
}

/** Merge history file stubs by basename (later path wins). */
function mergeChatFiles(
  a: ChatFile[] | undefined,
  b: ChatFile[] | undefined,
): ChatFile[] {
  const map = new Map<string, ChatFile>()
  for (const f of [...(a ?? []), ...(b ?? [])]) {
    if (!f?.name) continue
    map.set(f.name, f)
  }
  return [...map.values()]
}

/** Build chat file stubs from history ``sends`` (no base64 until preview load). */
export function filesFromHistorySends(m: HistoryMessage): ChatFile[] {
  if (m.role !== 'assistant' || !Array.isArray(m.sends)) return []
  const out: ChatFile[] = []
  const seen = new Set<string>()
  for (const raw of m.sends) {
    if (typeof raw !== 'string' || !raw.trim()) continue
    const path = raw.trim()
    const name = basenameOf(path)
    if (seen.has(name)) continue
    seen.add(name)
    out.push({ name, data: '', path })
  }
  return out
}

/** Collect session deliverables from history ``sends`` (order preserved, unique by basename). */
export function historyToDeliverables(messages: HistoryMessage[]): {
  names: string[]
  paths: Record<string, string>
} {
  const names: string[] = []
  const paths: Record<string, string> = {}
  const seen = new Set<string>()
  for (const m of messages) {
    if (m.role !== 'assistant' || !Array.isArray(m.sends)) continue
    for (const raw of m.sends) {
      if (typeof raw !== 'string' || !raw.trim()) continue
      const path = raw.trim()
      const name = basenameOf(path)
      if (seen.has(name)) {
        paths[name] = path
        continue
      }
      seen.add(name)
      names.push(name)
      paths[name] = path
    }
  }
  return { names, paths }
}

/** Map a Gateway session + title into the task-card UI model. */
export function sessionToTask(
  session: SessionInfo,
  title: string,
  opts?: {
    summary?: string
    status?: Task['status']
    progress?: number
    deliveryState?: DeliveryState
    deliverables?: string[]
    newDeliverables?: string[]
    deliverablePaths?: Record<string, string>
  },
  language: Language = DEFAULT_LANGUAGE,
): Task {
  const display = title.trim() || translate(language, 'app.newTaskDefault')
  const accent = ACCENTS[Math.abs(hash(session.id)) % ACCENTS.length]
  const status = opts?.status ?? 'working'
  const base: Task = {
    id: session.id,
    title: display,
    shortTitle: shortTitleOf(display, 10, language),
    category: workspaceLabel(session.workspace, language),
    summary:
      opts?.summary
      ?? translate(language, 'session.summaryPlaceholder'),
    progress: opts?.progress ?? 0,
    status,
    statusLabel: statusLabelFor(status, language),
    eta: status === 'completed' ? translate(language, 'session.etaCompleted') : translate(language, 'session.etaWorking'),
    updated: translate(language, 'session.updatedJustSynced'),
    accent,
    deliverables: opts?.deliverables ?? [],
    newDeliverables: opts?.newDeliverables ?? [],
    deliverablePaths: opts?.deliverablePaths ?? {},
    deliveryState: opts?.deliveryState ?? 'none',
    steps: [],
    turnSettled: status === 'completed',
    todoItems: [],
  }
  return applyTaskProgress(
    base,
    {
      streaming: false,
      turnSettled: base.turnSettled,
      todos: [],
    },
    language,
  )
}

export function statusLabelFor(status: Task['status'], language: Language): string {
  switch (status) {
    case 'attention':
      return translate(language, 'session.statusAttention')
    case 'completed':
      return translate(language, 'session.statusCompleted')
    case 'continuous':
      return translate(language, 'session.statusContinuous')
    default:
      return translate(language, 'session.statusWorking')
  }
}

const REVISE_LABELS = new Set(['按意见修改中', 'Revising per feedback', '按意見修改中'])
const STATUS_LABEL_VARIANTS: Record<Task['status'], Set<string>> = {
  attention: new Set(['待您处理', 'Needs your input', '待您處理']),
  completed: new Set(['已完成', 'Completed', '已完成']),
  continuous: new Set(['持续运行', 'Continuous', '持續運行']),
  working: new Set(['进行中', 'In progress', '進行中']),
}

/** 把缓存的任务状态标签按当前语言实时翻译（切换语言后旧状态立即跟随）。 */
export function displayTaskStatusLabel(
  status: Task['status'],
  label: string,
  language: Language,
): string {
  if (REVISE_LABELS.has(label)) return translate(language, 'app.statusRevising')
  if (STATUS_LABEL_VARIANTS[status]?.has(label)) return statusLabelFor(status, language)
  return label
}

const UPDATED_KEYS: [Set<string>, string][] = [
  [new Set(['刚刚同步', 'Just synced', '剛剛同步']), 'session.updatedJustSynced'],
  [new Set(['已从历史同步交付物', 'Deliverables synced from history', '已從歷史同步交付物']), 'session.updatedSynced'],
  [new Set(['刚刚收到交付物', 'Deliverables received just now', '剛剛收到交付物']), 'session.updatedReceived'],
  [new Set(['本轮回复已完成', 'This round replied', '本輪回覆已完成']), 'progress.updatedDone'],
  [new Set(['正在产出', 'Producing', '正在產出']), 'progress.updatedDelivering'],
  [new Set(['Agent 处理中', 'Agent processing']), 'progress.updatedAgentWorking'],
  [new Set(['待继续', 'To continue', '待繼續']), 'progress.updatedWaiting'],
  [new Set(['已从 todo 同步进度', 'Progress synced from todo', '已從 todo 同步進度']), 'progress.checklistSynced'],
  [new Set(['刚刚收到修改要求', 'Revision request received', '剛剛收到修改要求']), 'app.updatedReviseRequest'],
  [new Set(['刚刚保存交付物', 'Deliverables saved just now', '剛剛保存交付物']), 'app.updatedSavedDeliverables'],
  [new Set(['已从历史同步', 'Synced from history', '已從歷史同步']), 'app.updatedHistorySync'],
]

/** 把缓存的任务更新时间文案按当前语言实时翻译。 */
export function localizedTaskUpdated(updated: string, language: Language): string {
  for (const [variants, key] of UPDATED_KEYS) {
    if (variants.has(updated)) return translate(language, key)
  }
  const checklist = updated.match(
    /^(?:本轮已回复 · 清单|本輪已回覆 · 清單|This round replied · checklist) (.+)$/,
  )
  if (checklist?.[1]) return translate(language, 'progress.checklistRepliedWithList', { label: checklist[1] })
  return updated
}

export type TodoProgressOpts = {
  streaming?: boolean
  turnSettled?: boolean
  summary?: string
}

/**
 * Re-project card steps from todos + turn lifecycle (delegates to ``applyTaskProgress``).
 */
export function withTodoProgress(
  task: Task,
  todos: SessionTodo[],
  opts?: TodoProgressOpts,
  language: Language = DEFAULT_LANGUAGE,
): Task {
  return applyTaskProgress(
    task,
    {
      todos,
      streaming: opts?.streaming === true,
      turnSettled: opts?.turnSettled,
      summary: opts?.summary,
    },
    language,
  )
}

/** Mark turn settled and project 「done」 (or keep deliver if still streaming). */
export function withCompletedTurn(
  task: Task,
  opts?: { summary?: string; streaming?: boolean },
  language: Language = DEFAULT_LANGUAGE,
): Task {
  return applyTaskProgress(
    task,
    {
      turnSettled: true,
      streaming: opts?.streaming === true,
      summary: opts?.summary,
      todos: task.todoItems,
    },
    language,
  )
}

function hash(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  return h
}

/**
 * Register deliverable filenames from a live SSE blob turn.
 * Always accumulates into session ``deliverables`` (historical); marks as ``new`` by default.
 */
export function withDeliverables(
  task: Task,
  names: string[],
  opts?: { asNew?: boolean; paths?: Record<string, string>; streaming?: boolean },
  language: Language = DEFAULT_LANGUAGE,
): Task {
  const incoming = names.filter(Boolean)
  if (!incoming.length && !opts?.paths) return task
  const asNew = opts?.asNew !== false
  const mergedAll = [...new Set([...task.deliverables, ...incoming])]
  const mergedNew = asNew
    ? [...new Set([...task.newDeliverables, ...incoming])]
    : task.newDeliverables
  const mergedPaths = { ...task.deliverablePaths, ...(opts?.paths ?? {}) }
  const sameAll = mergedAll.length === task.deliverables.length
    && mergedAll.every((n, i) => n === task.deliverables[i])
  const sameNew = mergedNew.length === task.newDeliverables.length
    && mergedNew.every((n, i) => n === task.newDeliverables[i])
  const samePaths = Object.keys(mergedPaths).length === Object.keys(task.deliverablePaths).length
    && Object.entries(mergedPaths).every(([k, v]) => task.deliverablePaths[k] === v)
  if (sameAll && sameNew && samePaths) return task
  let deliveryState = task.deliveryState
  if (asNew && incoming.length) {
    deliveryState = 'ready'
  }
  const next: Task = {
    ...task,
    deliverables: mergedAll,
    newDeliverables: mergedNew,
    deliverablePaths: mergedPaths,
    deliveryState,
    updated: asNew ? translate(language, 'session.updatedReceived') : translate(language, 'session.updatedSynced'),
  }
  // Re-project phase so mid-stream blobs can surface 「产出与确认」 when appropriate.
  return applyTaskProgress(
    next,
    {
      streaming: opts?.streaming === true,
      turnSettled: next.turnSettled,
      todos: next.todoItems,
      hasDeliverables: true,
    },
    language,
  )
}

/** Apply history-derived deliverables without treating them as unread "new". */
export function withHistoricalDeliverables(
  task: Task,
  names: string[],
  paths: Record<string, string> = {},
  language: Language = DEFAULT_LANGUAGE,
): Task {
  return withDeliverables(task, names, { asNew: false, paths }, language)
}
