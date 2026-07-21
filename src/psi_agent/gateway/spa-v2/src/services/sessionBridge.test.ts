import { describe, expect, it } from 'vitest'
import { historyToChat } from './sessionBridge'

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
