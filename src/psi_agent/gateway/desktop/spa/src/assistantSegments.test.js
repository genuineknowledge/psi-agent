import { describe, expect, it } from 'vitest'
import { hasAssistantSegmentAfterUser } from './assistantSegments.js'

describe('hasAssistantSegmentAfterUser', () => {
  const user = { role: 'user', text: 'hi' }

  it('returns false when no assistant follows user', () => {
    expect(hasAssistantSegmentAfterUser([user], user)).toBe(false)
  })

  it('returns true when an assistant follows user', () => {
    const msgs = [user, { role: 'assistant', text: 'ok' }]
    expect(hasAssistantSegmentAfterUser(msgs, user)).toBe(true)
  })

  it('returns true for empty assistant stub after user', () => {
    const msgs = [user, { role: 'assistant', text: '' }]
    expect(hasAssistantSegmentAfterUser(msgs, user)).toBe(true)
  })

  it('stops at the next user message', () => {
    const user2 = { role: 'user', text: 'later' }
    const msgs = [user, user2, { role: 'assistant', text: 'nope' }]
    expect(hasAssistantSegmentAfterUser(msgs, user)).toBe(false)
  })
})
