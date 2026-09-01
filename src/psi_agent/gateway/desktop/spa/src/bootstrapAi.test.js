import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api.js'
import { DEFAULT_REMOTE_AI, ensureDefaultAi } from './bootstrapAi.js'

vi.mock('./api.js', () => ({
  api: vi.fn(),
}))

describe('ensureDefaultAi', () => {
  beforeEach(() => {
    vi.mocked(api).mockReset()
  })

  it('POSTs /ais with SPA defaults when pool is empty', async () => {
    vi.mocked(api)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ id: 'ai-1', model: DEFAULT_REMOTE_AI.model })
    await expect(ensureDefaultAi()).resolves.toEqual({
      id: 'ai-1',
      model: DEFAULT_REMOTE_AI.model,
    })
    expect(api).toHaveBeenNthCalledWith(1, 'GET', '/ais')
    expect(api).toHaveBeenNthCalledWith(2, 'POST', '/ais', { ...DEFAULT_REMOTE_AI })
  })

  it('skips create when AIs already exist', async () => {
    vi.mocked(api).mockResolvedValueOnce([{ id: 'ai-existing' }])
    await expect(ensureDefaultAi()).resolves.toBeNull()
    expect(api).toHaveBeenCalledTimes(1)
    expect(api).toHaveBeenCalledWith('GET', '/ais')
  })

  it('returns null when create fails', async () => {
    vi.mocked(api).mockResolvedValueOnce([]).mockRejectedValueOnce(new Error('fail'))
    await expect(ensureDefaultAi()).resolves.toBeNull()
  })
})
