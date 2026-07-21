import type { ChatMessage, DeliveryState, Task, TaskStep } from '../haitun-agent/model'
import type { HistoryMessage, SessionInfo, SessionTodo } from './api'
import { stripTransferMarkers } from './sendMarkers'

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
  return parts[parts.length - 1] || p || '工作区'
}

export function basenameOf(path: string): string {
  const n = path.replace(/\\/g, '/').split('/').filter(Boolean)
  return n[n.length - 1] || path
}

/**
 * Project Gateway `/history` rows into workspace chat bubbles.
 * Server already whitelists by ``kind``; still strip transfer markers and drop empties
 * (parity with spa v1 useSession / historyReconcile).
 */
export function historyToChat(messages: HistoryMessage[]): ChatMessage[] {
  const out: ChatMessage[] = []
  for (const m of messages) {
    // Defense in depth: never surface silent schedule rows if a proxy leaks them.
    if (m.kind === 'schedule.silent') continue
    const text = stripTransferMarkers(typeof m.text === 'string' ? m.text : '')
    if (!text.trim()) continue
    out.push({
      role: m.role === 'assistant' ? 'agent' : 'user',
      text,
    })
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
  const progress = opts?.progress ?? 12
  const status = opts?.status ?? 'working'
  return {
    id: session.id,
    title: display,
    shortTitle: shortTitleOf(display),
    category: workspaceLabel(session.workspace),
    summary:
      opts?.summary
      ?? '任务已接入 Gateway Session。在下方对话中继续推进，Agent 会真实执行工具并回复。',
    progress,
    status,
    statusLabel: statusLabelFor(status),
    eta: status === 'completed' ? '已完成' : '进行中',
    updated: '刚刚同步',
    accent,
    deliverables: opts?.deliverables ?? [],
    newDeliverables: opts?.newDeliverables ?? [],
    deliverablePaths: opts?.deliverablePaths ?? {},
    deliveryState: opts?.deliveryState ?? 'none',
    steps: defaultSteps(status),
  }
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

function defaultSteps(status: Task['status']): TaskStep[] {
  if (status === 'completed') {
    return [
      { label: '理解目标', state: 'done' },
      { label: '执行与交付', state: 'done' },
      { label: '收尾', state: 'done' },
    ]
  }
  return [
    { label: '理解目标与上下文', state: 'done' },
    { label: '与您对话推进', state: 'working' },
    { label: '产出与确认', state: 'waiting' },
  ]
}

export type TodoProgressOpts = {
  /**
   * True while the current chat turn is still streaming.
   * When todos are all done, the right step 「产出与确认」 stays ``working``
   * until the turn finishes, then becomes ``done``.
   */
  streaming?: boolean
}

/**
 * Map workspace ``todo`` items onto the middle task-card step as ``N/M``.
 * Cancelled items are excluded from the denominator. When the list is empty,
 * keep the default conversation-progress steps.
 */
export function withTodoProgress(
  task: Task,
  todos: SessionTodo[],
  opts?: TodoProgressOpts,
): Task {
  const streaming = opts?.streaming === true
  const active = todos.filter((t) => t.status !== 'cancelled')
  if (!active.length) {
    return { ...task, steps: defaultSteps(task.status) }
  }

  const total = active.length
  const completed = active.filter((t) => t.status === 'completed').length
  const inProgIdx = active.findIndex((t) => t.status === 'in_progress')
  let current: number
  let detail: string | undefined
  let middleState: TaskStep['state']

  if (inProgIdx >= 0) {
    current = inProgIdx + 1
    detail = active[inProgIdx]?.content
    middleState = 'working'
  } else if (completed >= total) {
    current = total
    detail = undefined
    middleState = 'done'
  } else {
    const nextIdx = active.findIndex((t) => t.status === 'pending')
    current = nextIdx >= 0 ? nextIdx + 1 : Math.min(completed + 1, total)
    detail = nextIdx >= 0 ? active[nextIdx]?.content : undefined
    middleState = 'working'
  }

  let outputState: TaskStep['state'] = 'waiting'
  if (middleState === 'done') {
    // Todos finished → produce reply while streaming; mark done when turn ends.
    outputState = streaming ? 'working' : 'done'
  }

  const steps: TaskStep[] = [
    { label: '理解目标与上下文', state: 'done' },
    {
      label: `${current}/${total}`,
      state: middleState,
      detail,
    },
    {
      label: '产出与确认',
      state: outputState,
    },
  ]

  const progress = Math.round((completed / total) * 100)
  return {
    ...task,
    steps,
    progress: Number.isFinite(progress) ? progress : task.progress,
    updated: '已从 todo 同步进度',
  }
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
  opts?: { asNew?: boolean; paths?: Record<string, string> },
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
  return {
    ...task,
    deliverables: mergedAll,
    newDeliverables: mergedNew,
    deliverablePaths: mergedPaths,
    deliveryState,
    updated: asNew ? '刚刚收到交付物' : '已从历史同步交付物',
  }
}

/** Apply history-derived deliverables without treating them as unread "new". */
export function withHistoricalDeliverables(
  task: Task,
  names: string[],
  paths: Record<string, string> = {},
): Task {
  return withDeliverables(task, names, { asNew: false, paths })
}
