import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AiInfo } from './api'
import {
  dedupeAisForDisplay,
  DEFAULT_REMOTE_AI,
  hydrateAiForSessions,
  isPlaceholderAi,
  pickPreferredAi,
  PLACEHOLDER_API_KEY,
} from './bootstrapAi'

vi.mock('./api', () => ({
  listAis: vi.fn(),
  createAi: vi.fn(),
  deleteAi: vi.fn(),
}))

import { createAi, deleteAi, listAis } from './api'

const ai = (partial: Partial<AiInfo> & Pick<AiInfo, 'id' | 'api_key'>): AiInfo => ({
  id: partial.id,
  socket: partial.socket ?? '',
  provider: partial.provider ?? 'deepseek',
  model: partial.model ?? 'deepseek-v4-flash',
  api_key: partial.api_key,
  base_url: partial.base_url ?? 'https://api.deepseek.com/v1',
})

describe('DEFAULT_REMOTE_AI', () => {
  /**
   * These three values are a contract with `gateway/desktop/_free_model.py`. The Gateway
   * only swaps the placeholder for a login token when the key matches AND the
   * base_url is same-origin with the account service. Drift on either side means
   * the free model ships a placeholder and the cloud answers 401 — a failure the
   * SPA's own tests would otherwise never see.
   */
  it('keeps the placeholder key so the Gateway substitutes a token', () => {
    expect(DEFAULT_REMOTE_AI.api_key).toBe(PLACEHOLDER_API_KEY)
  })

  it('points at the account service origin, /llm/v1 path', () => {
    expect(DEFAULT_REMOTE_AI.base_url).toBe('https://account.genuineknowledge.cn/llm/v1')
  })

  it('is treated as a placeholder AI, so real keys still win', () => {
    expect(isPlaceholderAi(DEFAULT_REMOTE_AI)).toBe(true)
  })
})

describe('dedupeAisForDisplay', () => {
  it('collapses same config different ids; keeps preferred', () => {
    const a = ai({
      id: 'a',
      api_key: PLACEHOLDER_API_KEY,
      provider: 'openai',
      model: 'deepseek-v4-flash',
      base_url: 'https://account.genuineknowledge.cn/llm/v1/',
    })
    const b = ai({
      id: 'b',
      api_key: PLACEHOLDER_API_KEY,
      provider: 'openai',
      model: 'deepseek-v4-flash',
      base_url: 'https://account.genuineknowledge.cn/llm/v1',
    })
    expect(dedupeAisForDisplay([a, b]).map((x) => x.id)).toEqual(['a'])
    expect(dedupeAisForDisplay([a, b], 'b').map((x) => x.id)).toEqual(['b'])
  })

  it('keeps rows that differ by api_key', () => {
    const free = ai({ id: 'free', api_key: PLACEHOLDER_API_KEY, provider: 'openai' })
    const real = ai({ id: 'real', api_key: 'sk-real', provider: 'openai' })
    expect(dedupeAisForDisplay([free, real]).map((x) => x.id).sort()).toEqual(['free', 'real'])
  })
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

  it('honors explicitly preferred placeholder even when real AIs exist', () => {
    expect(pickPreferredAi([free, realA], 'free')?.id).toBe('free')
  })

  it('falls back to placeholder only when pool is free-only', () => {
    expect(pickPreferredAi([free])?.id).toBe('free')
  })
})

describe('hydrateAiForSessions', () => {
  beforeEach(() => {
    vi.mocked(listAis).mockReset()
    vi.mocked(createAi).mockReset()
    vi.mocked(deleteAi).mockReset()
  })

  it('opens models only when pool stays empty', async () => {
    vi.mocked(listAis).mockResolvedValue([])
    const empty = await hydrateAiForSessions()
    expect(empty.openModels).toBe(true)
    expect(empty.preferred).toBeNull()
  })

  it('uses the existing pool without creating AIs', async () => {
    const real = ai({ id: 'real', api_key: 'sk-x' })
    vi.mocked(listAis).mockResolvedValue([real])

    const out = await hydrateAiForSessions()
    expect(out.openModels).toBe(false)
    expect(out.preferred?.id).toBe('real')
    expect(createAi).not.toHaveBeenCalled()
  })
})
