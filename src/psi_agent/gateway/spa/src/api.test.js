import { afterEach, describe, expect, it, vi } from 'vitest'

import { listWorkspaceWorkflows } from './api.js'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('listWorkspaceWorkflows', () => {
  it('serializes the selected workspace without relying on URLSearchParams.size', async () => {
    const NativeURLSearchParams = globalThis.URLSearchParams
    class LegacyURLSearchParams {
      constructor() {
        this.params = new NativeURLSearchParams()
      }

      set(key, value) {
        this.params.set(key, value)
      }

      toString() {
        return this.params.toString()
      }
    }

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ workflows: [{ name: 'daily-brief' }] }),
    })
    vi.stubGlobal('URLSearchParams', LegacyURLSearchParams)
    vi.stubGlobal('window', { location: { origin: 'https://example.test/' } })
    vi.stubGlobal('fetch', fetchMock)

    await expect(listWorkspaceWorkflows('/tmp/my workspace')).resolves.toEqual([
      { name: 'daily-brief' },
    ])
    expect(fetchMock).toHaveBeenCalledWith(
      'https://example.test/workspace/workflows?path=%2Ftmp%2Fmy+workspace',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('omits the query suffix when no workspace is supplied', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ workflows: [] }),
    })
    vi.stubGlobal('window', { location: { origin: 'https://example.test' } })
    vi.stubGlobal('fetch', fetchMock)

    await listWorkspaceWorkflows('')

    expect(fetchMock).toHaveBeenCalledWith(
      'https://example.test/workspace/workflows',
      expect.objectContaining({ method: 'GET' }),
    )
  })
})
