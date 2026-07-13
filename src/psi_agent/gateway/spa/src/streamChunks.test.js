import { describe, expect, it } from 'vitest'
import { isTurnBoundaryReasoning } from './streamChunks.js'

describe('isTurnBoundaryReasoning', () => {
  it('treats tool-call and tool-result markers as boundaries', () => {
    expect(isTurnBoundaryReasoning('[Tool Call: browser_click({"ref":"e1"})]')).toBe(true)
    expect(isTurnBoundaryReasoning('[Tool Result: ok]')).toBe(true)
  })

  it('tolerates leading whitespace on markers', () => {
    expect(isTurnBoundaryReasoning('  [Tool Call: x()]')).toBe(true)
  })

  it('does not treat the model thinking stream as a boundary', () => {
    // Reasoning models interleave these token-by-token with content.
    expect(isTurnBoundaryReasoning('让')).toBe(false)
    expect(isTurnBoundaryReasoning('我')).toBe(false)
    expect(isTurnBoundaryReasoning('Let me think about this...')).toBe(false)
    expect(isTurnBoundaryReasoning('')).toBe(false)
  })

  it('is safe on non-string input', () => {
    expect(isTurnBoundaryReasoning(undefined)).toBe(false)
    expect(isTurnBoundaryReasoning(null)).toBe(false)
    expect(isTurnBoundaryReasoning(42)).toBe(false)
  })
})
