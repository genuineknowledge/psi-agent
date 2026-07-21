import { describe, expect, it } from 'vitest'
import { hasReasoningText, reasoningStatusLabel } from './reasoningStatus.js'

describe('reasoningStatusLabel', () => {
  it('defaults to thinking', () => {
    expect(reasoningStatusLabel('')).toBe('思考中…')
    expect(reasoningStatusLabel('   ')).toBe('思考中…')
    expect(reasoningStatusLabel('plain thoughts')).toBe('思考中…')
  })

  it('shows active tool call', () => {
    expect(reasoningStatusLabel('[Tool Call: bash({"cmd":"ls"})]')).toBe('调用 bash…')
  })

  it('updates after tool result', () => {
    const text = '[Tool Call: clarify({"q":"x"})]\n[Tool Result: options…]'
    expect(reasoningStatusLabel(text)).toBe('已完成 clarify…')
  })

  it('prefers the latest tool call', () => {
    const text = [
      '[Tool Call: read({"path":"a"})]',
      '[Tool Result: ok]',
      '[Tool Call: bash({"cmd":"echo"})]',
    ].join('\n')
    expect(reasoningStatusLabel(text)).toBe('调用 bash…')
  })
})

describe('hasReasoningText', () => {
  it('detects non-empty reasoning', () => {
    expect(hasReasoningText({ reasoning: 'x' })).toBe(true)
    expect(hasReasoningText({ reasoning: '  ' })).toBe(false)
    expect(hasReasoningText({})).toBe(false)
  })
})
