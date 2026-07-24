import type { SessionTodo } from './api'
import type { Task, TaskStep } from '../haitun-agent/model'

/** Upper-level lifecycle for a task card (independent of todo UI detail). */
export type TaskPhase = 'advance' | 'deliver' | 'done'

export type ProgressInput = {
  /** Chat SSE turn in flight. */
  streaming: boolean
  /** At least one successful agent reply has settled (this turn or history). */
  turnSettled: boolean
  todos: SessionTodo[]
  hasDeliverables: boolean
}

export type ProgressProjection = {
  phase: TaskPhase
  steps: TaskStep[]
  progress: number
  updated: string
  /** Sidebar「当前阶段」prefer this over digging into steps. */
  phaseLabel: string
}

function activeTodos(todos: SessionTodo[]): SessionTodo[] {
  return todos.filter((t) => t.status !== 'cancelled')
}

function todoMiddle(
  active: SessionTodo[],
): { label: string; detail?: string; done: boolean; completed: number; total: number } {
  const total = active.length
  const completed = active.filter((t) => t.status === 'completed').length
  const inProgIdx = active.findIndex((t) => t.status === 'in_progress')
  if (inProgIdx >= 0) {
    return {
      label: `${inProgIdx + 1}/${total}`,
      detail: active[inProgIdx]?.content,
      done: false,
      completed,
      total,
    }
  }
  if (completed >= total) {
    return { label: `${total}/${total}`, done: true, completed, total }
  }
  const nextIdx = active.findIndex((t) => t.status === 'pending')
  const current = nextIdx >= 0 ? nextIdx + 1 : Math.min(completed + 1, total)
  return {
    label: `${current}/${total}`,
    detail: nextIdx >= 0 ? active[nextIdx]?.content : undefined,
    done: false,
    completed,
    total,
  }
}

/**
 * Layer 1 — which lifecycle phase are we in?
 * Layer 2 — inside 「推进」, todo list → N/M, else plain 「推进中」.
 */
export function resolveTaskProgress(input: ProgressInput): ProgressProjection {
  const active = activeTodos(input.todos)
  const hasTodos = active.length > 0
  const middle = hasTodos
    ? todoMiddle(active)
    : { label: '推进中', detail: undefined, done: false, completed: 0, total: 0 }

  let phase: TaskPhase
  if (input.streaming) {
    // Todos finished → delivery/output; otherwise still advancing.
    phase = hasTodos && middle.done ? 'deliver' : 'advance'
  } else if (input.turnSettled) {
    phase = 'done'
  } else {
    phase = 'advance'
  }

  const understand: TaskStep = { label: '理解目标与上下文', state: 'done' }
  const advanceStep: TaskStep = {
    label: middle.label,
    state: phase === 'advance' ? 'working' : 'done',
    detail: phase === 'advance' ? middle.detail : undefined,
  }
  const deliverStep: TaskStep = {
    label: '产出与确认',
    state: phase === 'deliver' ? 'working' : phase === 'done' ? 'done' : 'waiting',
  }

  let progress: number
  if (phase === 'done') {
    progress = input.hasDeliverables || (hasTodos && middle.done) ? 100 : 90
  } else if (hasTodos) {
    progress = Math.round((middle.completed / Math.max(middle.total, 1)) * 100)
    if (phase === 'deliver') progress = Math.max(progress, 85)
  } else if (phase === 'deliver') {
    progress = Math.max(40, input.hasDeliverables ? 70 : 50)
  } else {
    progress = input.streaming ? 25 : 8
  }

  const phaseLabel =
    phase === 'done'
      ? '已完成'
      : phase === 'deliver'
        ? '产出与确认'
        : hasTodos
          ? (middle.detail ? `${middle.label} · ${middle.detail}` : middle.label)
          : '推进中'

  const updated =
    phase === 'done'
      ? '本轮回复已完成'
      : phase === 'deliver'
        ? '正在产出'
        : hasTodos
          ? '已从 todo 同步进度'
          : '推进中'

  return {
    phase,
    steps: [understand, advanceStep, deliverStep],
    progress: Number.isFinite(progress) ? progress : 0,
    updated,
    phaseLabel,
  }
}

export type ApplyProgressPatch = {
  streaming?: boolean
  turnSettled?: boolean
  todos?: SessionTodo[]
  summary?: string
  /** Force hasDeliverables; default derives from task file lists. */
  hasDeliverables?: boolean
}

/** Project phase → steps onto a task (single write path for the card). */
export function applyTaskProgress(task: Task, patch: ApplyProgressPatch = {}): Task {
  const todos = patch.todos ?? task.todoItems ?? []
  const turnSettled = patch.turnSettled !== undefined ? patch.turnSettled : (task.turnSettled ?? false)
  const streaming = patch.streaming === true
  const hasDeliverables =
    patch.hasDeliverables
    ?? (task.newDeliverables.length > 0 || task.deliverables.length > 0)

  const proj = resolveTaskProgress({
    streaming,
    turnSettled,
    todos,
    hasDeliverables,
  })

  const summary = patch.summary?.trim()
  return {
    ...task,
    turnSettled,
    todoItems: patch.todos !== undefined ? patch.todos : task.todoItems,
    phase: proj.phase,
    steps: proj.steps,
    progress: proj.progress,
    updated: proj.updated,
    summary: summary
      ? summary.slice(0, 120) + (summary.length > 120 ? '…' : '')
      : task.summary,
  }
}
