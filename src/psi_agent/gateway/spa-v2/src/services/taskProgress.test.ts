import { describe, expect, it } from 'vitest'
import { resolveTaskProgress } from './taskProgress'
import type { SessionTodo } from './api'

const todos = (rows: Array<Pick<SessionTodo, 'id' | 'content' | 'status'>>): SessionTodo[] =>
  rows.map((r) => ({ ...r, status: r.status }))

describe('resolveTaskProgress (layered)', () => {
  it('streaming without todos → single activity「正在处理」, indeterminate', () => {
    const p = resolveTaskProgress({
      streaming: true,
      turnSettled: false,
      todos: [],
      hasDeliverables: false,
    })
    expect(p.phase).toBe('advance')
    expect(p.hasTodoTrack).toBe(false)
    expect(p.indeterminate).toBe(true)
    expect(p.progressLabel).toBe('')
    expect(p.steps).toEqual([{ label: '正在处理', state: 'working' }])
    expect(p.phaseLabel).toBe('正在处理')
  })

  it('idle without todos →「待继续」, not indeterminate', () => {
    const p = resolveTaskProgress({
      streaming: false,
      turnSettled: false,
      todos: [],
      hasDeliverables: false,
    })
    expect(p.phase).toBe('advance')
    expect(p.indeterminate).toBe(false)
    expect(p.steps[0]).toMatchObject({ label: '待继续', state: 'waiting' })
  })

  it('advance with todos → checklist steps + N/M label', () => {
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
    expect(p.hasTodoTrack).toBe(true)
    expect(p.progressLabel).toBe('2/3')
    expect(p.progress).toBe(33)
    expect(p.indeterminate).toBe(false)
    expect(p.steps).toEqual([
      { label: '调研', state: 'done' },
      { label: '写方案', state: 'working' },
      { label: '评审', state: 'waiting' },
    ])
    expect(p.phaseLabel).toBe('2/3 · 写方案')
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
    expect(p.steps.at(-1)).toMatchObject({ label: '产出与确认', state: 'working' })
    expect(p.phaseLabel).toBe('产出与确认')
  })

  it('turn settled without todos → done single step', () => {
    const p = resolveTaskProgress({
      streaming: false,
      turnSettled: true,
      todos: [],
      hasDeliverables: true,
    })
    expect(p.phase).toBe('done')
    expect(p.indeterminate).toBe(false)
    expect(p.progress).toBe(100)
    expect(p.steps).toEqual([{ label: '本轮已完成', state: 'done' }])
  })
})
