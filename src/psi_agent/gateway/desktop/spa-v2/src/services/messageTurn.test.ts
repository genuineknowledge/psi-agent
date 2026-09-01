import { describe, expect, it } from 'vitest'
import {
  inferFailedReason,
  isCompleteAgent,
  normalizeFailedTurns,
  stripErrorAnnotations,
} from './messageTurn'

describe('stripErrorAnnotations', () => {
  it('removes Error and 错误 annotations', () => {
    expect(stripErrorAnnotations('hello\n[Error: boom]')).toBe('hello')
    expect(stripErrorAnnotations('x\n[错误] failed')).toBe('x')
  })
})

describe('isCompleteAgent', () => {
  it('requires non-empty cleaned text or files', () => {
    expect(isCompleteAgent({ role: 'agent', text: '' })).toBe(false)
    expect(isCompleteAgent({ role: 'agent', text: '[Error: x]' })).toBe(false)
    expect(isCompleteAgent({ role: 'agent', text: 'ok' })).toBe(true)
    expect(isCompleteAgent({ role: 'agent', text: '', files: [{ name: 'a.md', data: '' }] })).toBe(true)
  })
})

describe('normalizeFailedTurns', () => {
  it('marks orphan user failed and drops empty agent stub', () => {
    const out = normalizeFailedTurns([
      { role: 'user', text: 'q1' },
      { role: 'agent', text: 'a1' },
      { role: 'user', text: 'orphan' },
      { role: 'agent', text: '' },
    ])
    expect(out).toHaveLength(3)
    expect(out[0]).toMatchObject({ role: 'user', text: 'q1', failed: false })
    expect(out[1]).toMatchObject({ role: 'agent', text: 'a1' })
    expect(out[2]).toMatchObject({
      role: 'user',
      text: 'orphan',
      failed: true,
      failedReason: 'incomplete',
    })
  })

  it('marks trailing user with no agent as incomplete', () => {
    const out = normalizeFailedTurns([
      { role: 'user', text: 'q1' },
      { role: 'agent', text: 'a1' },
      { role: 'user', text: 'lonely' },
    ])
    expect(out.at(-1)).toMatchObject({
      role: 'user',
      text: 'lonely',
      failed: true,
      failedReason: 'incomplete',
    })
  })

  it('keeps complete pairs unmarked', () => {
    const out = normalizeFailedTurns([
      { role: 'user', text: 'q' },
      { role: 'agent', text: 'a' },
    ])
    expect(out[0]).toMatchObject({ failed: false })
    expect(out[1]).toMatchObject({ role: 'agent', text: 'a', failed: false })
  })
})

describe('inferFailedReason', () => {
  it('detects error stubs', () => {
    expect(inferFailedReason({ role: 'agent', text: '[Error: x]' })).toBe('error')
    expect(inferFailedReason(null)).toBe('incomplete')
  })
})
