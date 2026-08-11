import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchDefaults, streamChat } from './api.js'

const TRACE_ID = '123e4567-e89b-12d3-a456-426614174000'

const originalWindow = globalThis.window
const originalFetch = globalThis.fetch

afterEach(() => {
  if (originalWindow === undefined) delete globalThis.window
  else globalThis.window = originalWindow
  globalThis.fetch = originalFetch
})

describe('abortable Gateway requests', () => {
  it('forwards the turn AbortSignal while resolving draft defaults', async () => {
    globalThis.window = { location: { origin: 'http://gateway.test' } }
    const controller = new AbortController()
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      json: async () => ({ agent: '' }),
    }))

    await fetchDefaults(controller.signal)

    expect(globalThis.fetch).toHaveBeenCalledWith('http://gateway.test/defaults', expect.objectContaining({
      method: 'GET',
      signal: controller.signal,
    }))
  })

  it('propagates and verifies the chat trace ID', async () => {
    globalThis.window = { location: { origin: 'http://gateway.test' } }
    const reader = { read: vi.fn() }
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      headers: { get: name => name === 'X-Psi-Trace-Id' ? TRACE_ID : null },
      body: { getReader: () => reader },
    }))

    const result = await streamChat('session-1', new FormData(), undefined, TRACE_ID)

    expect(result).toBe(reader)
    expect(globalThis.fetch).toHaveBeenCalledWith(
      'http://gateway.test/sessions/session-1/chat',
      expect.objectContaining({
        headers: { 'X-Psi-Trace-Id': TRACE_ID },
      }),
    )
  })

  it('rejects a mismatched Gateway trace ID', async () => {
    globalThis.window = { location: { origin: 'http://gateway.test' } }
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      headers: { get: () => '123e4567-e89b-12d3-a456-426614174001' },
      body: { getReader: vi.fn() },
    }))

    await expect(streamChat('session-1', new FormData(), undefined, TRACE_ID)).rejects.toThrow(/mismatched/)
  })
})
