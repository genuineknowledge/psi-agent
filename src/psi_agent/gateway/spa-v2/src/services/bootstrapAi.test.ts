import { describe, expect, it } from 'vitest'
import type { AiInfo } from './api'
import {
  isPlaceholderAi,
  pickPreferredAi,
  PLACEHOLDER_API_KEY,
} from './bootstrapAi'

const ai = (partial: Partial<AiInfo> & Pick<AiInfo, 'id' | 'api_key'>): AiInfo => ({
  id: partial.id,
  socket: partial.socket ?? '',
  provider: partial.provider ?? 'deepseek',
  model: partial.model ?? 'deepseek-v4-flash',
  api_key: partial.api_key,
  base_url: partial.base_url ?? 'https://api.deepseek.com/v1',
})

describe('isPlaceholderAi', () => {
  it('detects haitun-default and empty keys', () => {
    expect(isPlaceholderAi(ai({ id: '1', api_key: PLACEHOLDER_API_KEY }))).toBe(true)
    expect(isPlaceholderAi(ai({ id: '2', api_key: '' }))).toBe(true)
    expect(isPlaceholderAi(ai({ id: '3', api_key: 'sk-real' }))).toBe(false)
  })
})

describe('pickPreferredAi', () => {
  const free = ai({ id: 'free', api_key: PLACEHOLDER_API_KEY, provider: 'openai' })
  const realA = ai({ id: 'real-a', api_key: 'sk-a' })
  const realB = ai({ id: 'real-b', api_key: 'sk-b' })

  it('skips placeholder when real AIs exist', () => {
    expect(pickPreferredAi([free, realA, realB])?.id).toBe('real-a')
  })

  it('honors preferred real id', () => {
    expect(pickPreferredAi([free, realA, realB], 'real-b')?.id).toBe('real-b')
  })

  it('ignores preferred placeholder when real AIs exist', () => {
    expect(pickPreferredAi([free, realA], 'free')?.id).toBe('real-a')
  })

  it('falls back to placeholder only when pool is free-only', () => {
    expect(pickPreferredAi([free])?.id).toBe('free')
  })
})
