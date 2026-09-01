import { describe, expect, it } from 'vitest'
import type { Task } from '../haitun-agent/model'
import {
  extractSendPaths,
  historyToChat,
  historyToDeliverables,
  withCompletedTurn,
  withDeliverables,
  withTodoProgress,
} from './sessionBridge'

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
  turnSettled: false,
  todoItems: [],
  steps: [
    { label: '理解目标与上下文', state: 'done' },
    { label: '推进中', state: 'working' },
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

  it('attaches file stubs from sends for reload preview', () => {
    expect(
      historyToChat([
        {
          role: 'assistant',
          text: '写好了',
          sends: ['/ws/reports/out.md', '/ws/reports/out.md'],
        },
      ]),
    ).toEqual([
      {
        role: 'agent',
        text: '写好了',
        files: [{ name: 'out.md', data: '', path: '/ws/reports/out.md' }],
      },
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

  it('coalesces consecutive assistant rows keeping only the last prose', () => {
    expect(
      historyToChat([
        { role: 'user', text: '做剧本杀包' },
        { role: 'assistant', text: 'Step 2 ✅ 开场与规则说明' },
        { role: 'assistant', text: 'Step 3 ✅ 角色卡对照表' },
        {
          role: 'assistant',
          text: 'Step 4 ✅ 生成 .docx 并交付',
          sends: ['/ws/pack.docx'],
        },
        {
          role: 'assistant',
          text: 'write_word 的工具结果和实际写入不一致，我直接用 Python 生成文件。',
        },
        { role: 'user', text: '再改一版' },
        { role: 'assistant', text: '好的' },
      ]),
    ).toEqual([
      { role: 'user', text: '做剧本杀包' },
      {
        role: 'agent',
        text: 'write_word 的工具结果和实际写入不一致，我直接用 Python 生成文件。',
        files: [{ name: 'pack.docx', data: '', path: '/ws/pack.docx' }],
      },
      { role: 'user', text: '再改一版' },
      { role: 'agent', text: '好的' },
    ])
  })

  it('merges consecutive assistant reasoning for expandable thinking', () => {
    expect(
      historyToChat([
        {
          role: 'assistant',
          text: 'Step 1',
          reasoning: '先读文件',
        },
        {
          role: 'assistant',
          text: '最终回复',
          reasoning: '再总结',
        },
      ]),
    ).toEqual([
      {
        role: 'agent',
        text: '最终回复',
        reasoning: '先读文件\n再总结',
      },
    ])
  })

  it('maps structured tools separately from reasoning', () => {
    expect(
      historyToChat([
        {
          role: 'assistant',
          text: '完成了',
          reasoning: '先列目录再读文件',
          tools: [
            { name: 'list_dir', arguments: '{"path": "."}' },
            { name: 'read', arguments: '{"path": "a.md"}' },
          ],
        },
      ]),
    ).toEqual([
      {
        role: 'agent',
        text: '完成了',
        reasoning: '先列目录再读文件',
        tools: ['浏览 `.`', '读取 `a.md`'],
      },
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

describe('extractSendPaths', () => {
  it('parses plain and space-padded SEND markers', () => {
    expect(
      extractSendPaths('ok\n[SEND:/ws/a.md]\n[ SEND:C:/docs/b.html ]'),
    ).toEqual(['/ws/a.md', 'C:/docs/b.html'])
  })

  it('parses lowercase SEND markers', () => {
    expect(
      extractSendPaths('ok\n[Send:/ws/a.md]\n[send: C:/docs/b.html ]'),
    ).toEqual(['/ws/a.md', 'C:/docs/b.html'])
  })
})

describe('withTodoProgress (via layered resolver)', () => {
  it('streaming without todos → 正在处理 (activity)', () => {
    const next = withTodoProgress(baseTask(), [], { streaming: true, turnSettled: false })
    expect(next.phase).toBe('advance')
    expect(next.hasTodoTrack).toBe(false)
    expect(next.progressIndeterminate).toBe(true)
    expect(next.steps).toEqual([{ label: '正在处理', state: 'working' }])
  })

  it('maps todo in_progress to checklist + N/M', () => {
    const next = withTodoProgress(
      baseTask(),
      [
        { id: '1', content: '调研', status: 'completed' },
        { id: '2', content: '写方案', status: 'in_progress' },
        { id: '3', content: '评审', status: 'pending' },
        { id: 'x', content: '废弃', status: 'cancelled' },
      ],
      { streaming: true, turnSettled: false },
    )
    expect(next.phase).toBe('advance')
    expect(next.hasTodoTrack).toBe(true)
    expect(next.progressLabel).toBe('2/3')
    expect(next.steps).toEqual([
      { label: '调研', state: 'done' },
      { label: '写方案', state: 'working' },
      { label: '评审', state: 'waiting' },
    ])
    expect(next.progress).toBe(33)
  })

  it('all todos done while streaming → deliver', () => {
    const next = withTodoProgress(
      baseTask(),
      [
        { id: '1', content: 'a', status: 'completed' },
        { id: '2', content: 'b', status: 'completed' },
      ],
      { streaming: true, turnSettled: false },
    )
    expect(next.phase).toBe('deliver')
    expect(next.steps.at(-1)?.state).toBe('working')
    expect(next.steps.at(-1)?.label).toBe('产出与确认')
  })
})

describe('withCompletedTurn', () => {
  it('settles to done and keeps deliverable progress', () => {
    const withFile = withDeliverables(baseTask(), ['game.html'], { streaming: true })
    const next = withCompletedTurn(withFile, { summary: '已交付智斗游戏 HTML' })
    expect(next.phase).toBe('done')
    expect(next.turnSettled).toBe(true)
    expect(next.steps.every((s) => s.state === 'done')).toBe(true)
    expect(next.progress).toBe(100)
  })
})
