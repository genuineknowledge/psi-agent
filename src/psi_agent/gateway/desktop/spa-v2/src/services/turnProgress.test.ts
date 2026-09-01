import { describe, expect, it } from 'vitest'
import {
  applyProgressEvent,
  progressLogStart,
  summarizeToolCall,
  TURN_PROGRESS,
} from './turnProgress'

describe('Cursor-style progress log', () => {
  it('starts with no sealed lines and 规划下一步 trailer', () => {
    expect(progressLogStart()).toEqual({
      lines: [],
      current: TURN_PROGRESS.planning,
    })
  })

  it('thinking never seals 规划下一步 into lines', () => {
    let log = progressLogStart()
    log = applyProgressEvent(log, 'thinking', '长段内心…')
    log = applyProgressEvent(log, 'thinking', '更多…')
    expect(log).toEqual({ lines: [], current: TURN_PROGRESS.planning })
  })

  it('tool_call seals brief summary; trailer stays 规划下一步', () => {
    let log = progressLogStart()
    log = applyProgressEvent(
      log,
      'tool_call',
      '[Tool Call: read({"path": "D:\\\\Desk\\\\a.py"})]',
    )
    expect(log.lines).toEqual(['读取 `a.py`'])
    expect(log.current).toBe(TURN_PROGRESS.planning)

    log = applyProgressEvent(
      log,
      'tool_call',
      '[Tool Call: bash({"command": "python a.py"})]',
    )
    expect(log.lines).toEqual(['读取 `a.py`', '执行 `python a.py`'])
    expect(log.current).toBe(TURN_PROGRESS.planning)
  })

  it('does not repeat identical consecutive summaries', () => {
    let log = progressLogStart()
    const ev = '[Tool Call: read({"path": "a.py"})]'
    log = applyProgressEvent(log, 'tool_call', ev)
    log = applyProgressEvent(log, 'tool_call', ev)
    expect(log.lines).toEqual(['读取 `a.py`'])
  })

  it('content switches trailer to 撰写回复 without sealing planning', () => {
    let log = progressLogStart()
    log = applyProgressEvent(log, 'tool_call', '[Tool Call: read({"path": "a.py"})]')
    log = applyProgressEvent(log, 'content', '')
    expect(log.lines).toEqual(['读取 `a.py`'])
    expect(log.current).toBe(TURN_PROGRESS.writing)
  })

  it('summarizeToolCall covers common tools', () => {
    expect(summarizeToolCall('edit', '{"path":"src/app.tsx"}')).toBe('编辑 `app.tsx`')
    expect(summarizeToolCall('find_files', '{"glob":"**/*.ts"}')).toBe('查找 **/*.ts')
    expect(summarizeToolCall('browser_navigate', '{}')).toBe('浏览页面')
  })
})
