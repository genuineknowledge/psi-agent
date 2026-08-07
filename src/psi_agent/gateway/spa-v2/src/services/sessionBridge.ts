import type { ChatFile, ChatMessage, DeliveryState, Task } from '../haitun-agent/model'
import type { HistoryMessage, HistoryToolCall, SessionInfo, SessionTodo } from './api'
import { stripTransferMarkers } from './sendMarkers'
import { applyTaskProgress } from './taskProgress'
import { summarizeToolCall } from './turnProgress'

const ACCENTS = ['#007bff', '#27a06b', '#d8a62a', '#ff6b57', '#4d8eff', '#7c5cfc']

export function shortTitleOf(title: string, max = 10): string {
  const t = title.trim() || '新任务'
  return t.length > max ? `${t.slice(0, max)}…` : t
}

export function titleFromPrompt(description: string): string {
  const clean = description.split(/[。！？\n]/)[0]?.trim() || '新任务'
  return clean.slice(0, 30)
}

export function workspaceLabel(path: string): string {
  const p = path.replace(/\\/g, '/').replace(/\/+$/, '')
  const parts = p.split('/').filter(Boolean)
  return parts[parts.length - 1] || p || '海豚工作室'
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
export function historyToChat(messages: HistoryMessage[]): ChatMessage[] {
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
    const tools = role === 'agent' ? toolSummariesFromHistory(m.tools) : []
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

function toolSummariesFromHistory(tools: HistoryToolCall[] | undefined): string[] {
  if (!Array.isArray(tools) || !tools.length) return []
  const out: string[] = []
  for (const t of tools) {
    if (!t || typeof t.name !== 'string' || !t.name.trim()) continue
    const args = typeof t.arguments === 'string' ? t.arguments : '{}'
    const line = summarizeToolCall(t.name, args)
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
): Task {
  const display = title.trim() || '新任务'
  const accent = ACCENTS[Math.abs(hash(session.id)) % ACCENTS.length]
  const status = opts?.status ?? 'working'
  const base: Task = {
    id: session.id,
    title: display,
    shortTitle: shortTitleOf(display),
    category: workspaceLabel(session.workspace),
    summary:
      opts?.summary
      ?? '任务已接入 Gateway Session。在下方对话中继续推进，Agent 会真实执行工具并回复。',
    progress: opts?.progress ?? 0,
    status,
    statusLabel: statusLabelFor(status),
    eta: status === 'completed' ? '已完成' : '进行中',
    updated: '刚刚同步',
    accent,
    deliverables: opts?.deliverables ?? [],
    newDeliverables: opts?.newDeliverables ?? [],
    deliverablePaths: opts?.deliverablePaths ?? {},
    deliveryState: opts?.deliveryState ?? 'none',
    steps: [],
    turnSettled: status === 'completed',
    todoItems: [],
  }
  return applyTaskProgress(base, {
    streaming: false,
    turnSettled: base.turnSettled,
    todos: [],
  })
}

function statusLabelFor(status: Task['status']): string {
  switch (status) {
    case 'attention':
      return '待您处理'
    case 'completed':
      return '已完成'
    case 'continuous':
      return '持续运行'
    default:
      return '进行中'
  }
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
): Task {
  return applyTaskProgress(task, {
    todos,
    streaming: opts?.streaming === true,
    turnSettled: opts?.turnSettled,
    summary: opts?.summary,
  })
}

/** Mark turn settled and project 「done」 (or keep deliver if still streaming). */
export function withCompletedTurn(
  task: Task,
  opts?: { summary?: string; streaming?: boolean },
): Task {
  return applyTaskProgress(task, {
    turnSettled: true,
    streaming: opts?.streaming === true,
    summary: opts?.summary,
    todos: task.todoItems,
  })
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
    updated: asNew ? '刚刚收到交付物' : '已从历史同步交付物',
  }
  // Re-project phase so mid-stream blobs can surface 「产出与确认」 when appropriate.
  return applyTaskProgress(next, {
    streaming: opts?.streaming === true,
    turnSettled: next.turnSettled,
    todos: next.todoItems,
    hasDeliverables: true,
  })
}

/** Apply history-derived deliverables without treating them as unread "new". */
export function withHistoricalDeliverables(
  task: Task,
  names: string[],
  paths: Record<string, string> = {},
): Task {
  return withDeliverables(task, names, { asNew: false, paths })
}
