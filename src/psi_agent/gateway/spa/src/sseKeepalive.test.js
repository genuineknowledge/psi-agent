import { describe, expect, it } from 'vitest'
import { isSseKeepaliveText } from './sseKeepalive.js'

describe('isSseKeepaliveText', () => {
  it('matches whole-message ping/pong only', () => {
    expect(isSseKeepaliveText('ping')).toBe(true)
    expect(isSseKeepaliveText(' PONG ')).toBe(true)
    expect(isSseKeepaliveText('ping pong')).toBe(false)
    expect(isSseKeepaliveText('')).toBe(false)
  })
})
