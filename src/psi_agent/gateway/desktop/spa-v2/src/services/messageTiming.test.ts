import { describe, expect, it } from 'vitest'
import {
  formatMessageClock,
  formatThinkingDuration,
  thinkingHeaderWithDuration,
} from './messageTiming'

describe('formatThinkingDuration', () => {
  it('formats seconds / minutes / hours', () => {
    expect(formatThinkingDuration(0)).toBe('0秒')
    expect(formatThinkingDuration(4500)).toBe('5秒')
    expect(formatThinkingDuration(65_000)).toBe('1分5秒')
    expect(formatThinkingDuration(120_000)).toBe('2分')
    expect(formatThinkingDuration(3_661_000)).toBe('1小时1分')
  })

  it('rejects non-finite', () => {
    expect(formatThinkingDuration(Number.NaN)).toBe('')
    expect(formatThinkingDuration(-1)).toBe('')
  })
})

describe('formatMessageClock', () => {
  const now = new Date(2026, 8, 12, 15, 30, 0) // local Sep 12 15:30

  it('shows HH:mm for today', () => {
    const iso = new Date(2026, 8, 12, 15, 5, 0).toISOString()
    expect(formatMessageClock(iso, now)).toBe('15:05')
  })

  it('prefixes 昨天 for yesterday', () => {
    const iso = new Date(2026, 8, 11, 15, 5, 0).toISOString()
    expect(formatMessageClock(iso, now)).toBe('昨天 15:05')
  })

  it('returns empty for bad iso', () => {
    expect(formatMessageClock('not-a-date', now)).toBe('')
  })
})

describe('thinkingHeaderWithDuration', () => {
  it('appends duration to base label', () => {
    expect(thinkingHeaderWithDuration('已思考', 12_000)).toBe('已思考 · 12秒')
    expect(thinkingHeaderWithDuration('已思考', undefined)).toBe('已思考')
  })
})
