import { describe, expect, it } from 'vitest'
import { resolveTaskProgress } from './taskProgress'
import type { SessionTodo } from './api'

const todos = (rows: Array<Pick<SessionTodo, 'id' | 'content' | 'status'>>): SessionTodo[] =>
  rows.map((r) => ({ ...r, status: r.status }))

describe('resolveTaskProgress (layered)', () => {
  it('advance without todos → middle 「推进中」', () => {
    const p = resolveTaskProgress({
      streaming: true,
      turnSettled: false,
      todos: [],
      hasDeliverables: false,
    })
    expect(p.phase).toBe('advance')
    expect(p.steps[1]).toMatchObject({ label: '推进中', state: 'working' })
    expect(p.steps[2]?.state).toBe('waiting')
    expect(p.phaseLabel).toBe('推进中')
  })

  it('advance with todos → middle N/M', () => {
    const p = resolveTaskProgress({
      streaming: true,
      turnSettled: false,
      todos: todos([
        { id: '1', content: '调研', status: 'completed' },
        { id: '2', content: '写方案', status: 'in_progress' },
        { id: '3', content: '评审', status: 'pending' },
      ]),
      hasDeliverables: false,
    })
    expect(p.phase).toBe('advance')
    expect(p.steps[1]).toMatchObject({ label: '2/3', state: 'working', detail: '写方案' })
    expect(p.progress).toBe(33)
  })

  it('todos all done while streaming → deliver phase', () => {
    const p = resolveTaskProgress({
      streaming: true,
      turnSettled: false,
      todos: todos([
        { id: '1', content: 'a', status: 'completed' },
        { id: '2', content: 'b', status: 'completed' },
      ]),
      hasDeliverables: false,
    })
    expect(p.phase).toBe('deliver')
    expect(p.steps[1]).toMatchObject({ label: '2/2', state: 'done' })
    expect(p.steps[2]).toMatchObject({ label: '产出与确认', state: 'working' })
  })

  it('turn settled → done phase with all steps complete', () => {
    const p = resolveTaskProgress({
      streaming: false,
      turnSettled: true,
      todos: [],
      hasDeliverables: true,
    })
    expect(p.phase).toBe('done')
    expect(p.steps.every((s) => s.state === 'done')).toBe(true)
    expect(p.steps[1]?.label).toBe('推进中')
    expect(p.progress).toBe(100)
  })
})
