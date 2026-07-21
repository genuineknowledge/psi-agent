import { describe, expect, it } from 'vitest'
import type { Task } from '../haitun-agent/model'
import { historyToChat, historyToDeliverables, withTodoProgress } from './sessionBridge'

const baseTask = (): Task => ({
  id: 's1',
  title: 't',
  shortTitle: 't',
  category: 'ws',
  summary: 's',
  progress: 12,
  status: 'working',
  statusLabel: '进行中',
  eta: '进行中',
  updated: 'x',
  accent: '#007bff',
  deliverables: [],
  newDeliverables: [],
  deliverablePaths: {},
  deliveryState: 'none',
  steps: [
    { label: '理解目标与上下文', state: 'done' },
    { label: '与您对话推进', state: 'working' },
    { label: '产出与确认', state: 'waiting' },
  ],
})

describe('historyToChat', () => {
  it('maps roles and strips transfer markers', () => {
    expect(
      historyToChat([
        { role: 'user', text: '看图\n[RECV:/tmp/a.png]' },
        { role: 'assistant', text: '好的\n[SEND:/tmp/out.md]' },
      ]),
    ).toEqual([
      { role: 'user', text: '看图' },
      { role: 'agent', text: '好的' },
    ])
  })

  it('drops schedule.silent and empty rows', () => {
    expect(
      historyToChat([
        { role: 'user', text: '# Heartbeat', kind: 'schedule.silent' },
        { role: 'assistant', text: 'HEARTBEAT_OK', kind: 'schedule.silent' },
        { role: 'user', text: '[RECV:/x]' },
        { role: 'assistant', text: '日报', kind: 'schedule.display' },
        { role: 'user', text: '你好' },
      ]),
    ).toEqual([
      { role: 'agent', text: '日报' },
      { role: 'user', text: '你好' },
    ])
  })
})

describe('historyToDeliverables', () => {
  it('collects unique basenames and paths from sends', () => {
    expect(
      historyToDeliverables([
        { role: 'assistant', text: 'ok', sends: ['/ws/a.md', '/other/a.md'] },
        { role: 'assistant', text: '', sends: ['/ws/b.html'] },
        { role: 'user', text: 'hi', sends: ['/ws/ignore.md'] },
      ]),
    ).toEqual({
      names: ['a.md', 'b.html'],
      paths: { 'a.md': '/other/a.md', 'b.html': '/ws/b.html' },
    })
  })
})

describe('withTodoProgress', () => {
  it('keeps default middle step when todos empty', () => {
    const next = withTodoProgress(baseTask(), [])
    expect(next.steps[1]?.label).toBe('与您对话推进')
  })

  it('maps in_progress index to N/M and content detail', () => {
    const next = withTodoProgress(baseTask(), [
      { id: '1', content: '调研', status: 'completed' },
      { id: '2', content: '写方案', status: 'in_progress' },
      { id: '3', content: '评审', status: 'pending' },
      { id: 'x', content: '废弃', status: 'cancelled' },
    ])
    expect(next.steps[1]).toEqual({
      label: '2/3',
      state: 'working',
      detail: '写方案',
    })
    expect(next.progress).toBe(33)
  })

  it('shows total/total when all completed', () => {
    const next = withTodoProgress(baseTask(), [
      { id: '1', content: 'a', status: 'completed' },
      { id: '2', content: 'b', status: 'completed' },
    ])
    expect(next.steps[1]?.label).toBe('2/2')
    expect(next.steps[1]?.state).toBe('done')
    expect(next.steps[2]).toEqual({ label: '产出与确认', state: 'done' })
    expect(next.progress).toBe(100)
  })

  it('keeps output step working while streaming after todos finish', () => {
    const next = withTodoProgress(
      baseTask(),
      [
        { id: '1', content: 'a', status: 'completed' },
        { id: '2', content: 'b', status: 'completed' },
      ],
      { streaming: true },
    )
    expect(next.steps[1]?.state).toBe('done')
    expect(next.steps[2]).toEqual({ label: '产出与确认', state: 'working' })
  })
})
