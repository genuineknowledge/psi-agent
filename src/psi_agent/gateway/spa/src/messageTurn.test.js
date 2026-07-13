import { describe, expect, it } from 'vitest'
import {
  applyTurnOutcome,
  inferFailedReason,
  isCompleteAssistant,
  normalizeFailedTurns,
  resolveTurnOutcome,
} from './messageTurn.js'

describe('isCompleteAssistant', () => {
  it('accepts assistant with text', () => {
    expect(isCompleteAssistant({ role: 'assistant', text: 'hello' })).toBe(true)
  })

  it('rejects empty, stopped, and error assistant', () => {
    expect(isCompleteAssistant({ role: 'assistant', text: '' })).toBe(false)
    expect(isCompleteAssistant({ role: 'assistant', text: 'x', stopped: true })).toBe(false)
    expect(isCompleteAssistant({ role: 'assistant', text: '[Error: bad]' })).toBe(false)
    expect(isCompleteAssistant({ role: 'assistant', text: 'oops\n[Error: bad]' })).toBe(false)
  })
})

describe('normalizeFailedTurns', () => {
  it('marks orphan user message failed and drops empty assistant', () => {
    const input = [
      { role: 'user', text: 'first' },
      { role: 'assistant', text: 'ok' },
      { role: 'user', text: 'orphan' },
      { role: 'assistant', text: '' },
    ]
    const out = normalizeFailedTurns(input)
    expect(out).toHaveLength(3)
    expect(out[2]).toMatchObject({ role: 'user', text: 'orphan', failed: true, failedReason: 'incomplete' })
  })

  it('marks duplicate user turns when first has no complete reply', () => {
    const input = [
      { role: 'user', text: 'q1' },
      { role: 'user', text: 'q1 retry' },
      { role: 'assistant', text: 'answer' },
    ]
    const out = normalizeFailedTurns(input)
    expect(out[0]).toMatchObject({ text: 'q1', failed: true })
    expect(out[1]).toMatchObject({ text: 'q1 retry', failed: false })
    expect(out[2]).toMatchObject({ role: 'assistant', text: 'answer' })
  })
})

describe('applyTurnOutcome', () => {
  it('removes assistant stub on error', () => {
    const user = { role: 'user', text: 'hi' }
    const asst = { role: 'assistant', text: '[Error: x]' }
    const msgs = [user, asst]
    applyTurnOutcome(msgs, user, asst, 'error')
    expect(msgs).toEqual([{ role: 'user', text: 'hi', failed: true, failedReason: 'error' }])
  })
})

describe('resolveTurnOutcome', () => {
  it('detects stopped and incomplete turns', () => {
    const user = { role: 'user', text: 'hi' }
    expect(resolveTurnOutcome([], user, { role: 'assistant', stopped: true, text: 'partial' })).toBe('stopped')
    expect(resolveTurnOutcome([], user, { role: 'assistant', text: '' })).toBe('incomplete')
    expect(resolveTurnOutcome([], user, { role: 'assistant', text: 'done' })).toBe('ok')
  })

  it('infers error reason from assistant text', () => {
    expect(inferFailedReason({ role: 'assistant', text: '[Error: key expired]' })).toBe('error')
  })
})
