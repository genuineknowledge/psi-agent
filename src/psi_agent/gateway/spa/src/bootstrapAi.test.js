import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ensureDefaultAi } from './bootstrapAi.js'

vi.mock('./api.js', () => ({
  api: vi.fn(),
}))

import { api } from './api.js'

describe('ensureDefaultAi', () => {
  beforeEach(() => {
    vi.mocked(api).mockReset()
  })

  it('returns AI info when bootstrap succeeds', async () => {
    vi.mocked(api).mockResolvedValue({ id: 'ai-1', model: 'glm-4-flash' })
    await expect(ensureDefaultAi()).resolves.toEqual({ id: 'ai-1', model: 'glm-4-flash' })
    expect(api).toHaveBeenCalledWith('POST', '/ais/bootstrap')
  })

  it('returns null when bootstrap fails', async () => {
    vi.mocked(api).mockRejectedValue(new Error('No API key'))
    await expect(ensureDefaultAi()).resolves.toBeNull()
  })

  it('returns null when bootstrap skips without id', async () => {
    vi.mocked(api).mockResolvedValue({ skipped: true })
    await expect(ensureDefaultAi()).resolves.toBeNull()
  })
})
