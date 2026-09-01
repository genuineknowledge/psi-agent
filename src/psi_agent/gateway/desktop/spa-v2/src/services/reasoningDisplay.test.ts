import { describe, expect, it } from 'vitest'
import {
  hasDisplayableReasoning,
  hasToolMarkerReasoning,
  hasTurnProcess,
  parseReasoningSegments,
  stripToolMarkersFromReasoning,
  thinkingHeaderLabel,
  toolSummariesFromReasoning,
  toolsHeaderLabel,
} from './reasoningDisplay'

describe('stripToolMarkersFromReasoning', () => {
  it('keeps plain thinking', () => {
    expect(stripToolMarkersFromReasoning('先分析问题')).toBe('先分析问题')
  })

  it('removes tool call and result markers', () => {
    const raw = [
      '先想一步',
      '[Tool Call: list_dir({"path":"."})]',
      '[Tool Result: tools/ AGENTS.md]',
      '再总结',
    ].join('\n')
    expect(stripToolMarkersFromReasoning(raw)).toBe('先想一步\n\n再总结')
  })

  it('hides incomplete trailing tool markers while streaming', () => {
    expect(stripToolMarkersFromReasoning('思考\n[Tool Call: ba')).toBe('思考')
  })

  it('handles nested json in tool args', () => {
    const raw = 'a\n[Tool Call: todo({"items":[{"x":1}]})]\nb'
    expect(stripToolMarkersFromReasoning(raw)).toBe('a\n\nb')
  })

  it('strips Working keepalive markers', () => {
    expect(stripToolMarkersFromReasoning('think\n[Working…]\nmore')).toBe('think\n\nmore')
  })
})

describe('parseReasoningSegments / toolSummariesFromReasoning', () => {
  it('splits thinking and tool calls chronologically', () => {
    const raw = [
      '先想',
      '[Tool Call: read({"path":"a.py"})]',
      '[Tool Result: ok]',
      '中间结论：文件很短',
      '[Tool Call: bash({"command":"ls"})]',
      '[Tool Result: a.py]',
      '再总结',
    ].join('\n')
    const segs = parseReasoningSegments(raw)
    expect(segs.map((s) => s.kind)).toEqual([
      'thinking',
      'tool_call',
      'thinking',
      'tool_call',
      'thinking',
    ])
    expect(toolSummariesFromReasoning(raw)).toEqual([
      '读取 `a.py`',
      '执行 `ls`',
    ])
    expect(stripToolMarkersFromReasoning(raw)).toContain('中间结论：文件很短')
  })

  it('dedupes consecutive identical tool summaries', () => {
    const raw = [
      '[Tool Call: read({"path":"a.py"})]',
      '[Tool Call: read({"path":"a.py"})]',
    ].join('\n')
    expect(toolSummariesFromReasoning(raw)).toEqual(['读取 `a.py`'])
  })
})

describe('hasDisplayableReasoning', () => {
  it('is false when only tool markers remain', () => {
    expect(hasDisplayableReasoning('[Tool Call: bash({})]\n[Tool Result: ok]')).toBe(false)
    expect(hasDisplayableReasoning('有想法')).toBe(true)
  })
})

describe('hasToolMarkerReasoning / hasTurnProcess', () => {
  it('detects tools even when prose is empty', () => {
    expect(hasToolMarkerReasoning('[Tool Call: bash({})]')).toBe(true)
    expect(hasToolMarkerReasoning('纯思考')).toBe(false)
    expect(hasTurnProcess('[Tool Call: bash({})]')).toBe(true)
    expect(hasTurnProcess('纯思考')).toBe(true)
    expect(hasTurnProcess('')).toBe(false)
  })
})

describe('thinkingHeaderLabel / toolsHeaderLabel', () => {
  it('labels streaming vs done', () => {
    expect(thinkingHeaderLabel({ streaming: true })).toBe('思考中')
    expect(thinkingHeaderLabel({})).toBe('已思考')
  })

  it('counts tools', () => {
    expect(toolsHeaderLabel(1)).toBe('已调用 1 个工具')
    expect(toolsHeaderLabel(3)).toBe('已调用 3 个工具')
  })
})
