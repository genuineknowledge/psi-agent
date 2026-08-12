import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useUxStore } from './ux.js'

const TRACE_ID = '123e4567-e89b-42d3-a456-426614174000'

describe('UX metrics store', () => {
  let now

  beforeEach(() => {
    setActivePinia(createPinia())
    now = 100
    vi.stubGlobal('location', { search: '?ux_debug=1' })
    vi.stubGlobal('performance', { now: () => now })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('tracks a promoted session and finalizes one content-free turn', () => {
    const ux = useUxStore()
    ux.startTurn({ traceId: TRACE_ID, sessionKey: 'draft' })
    now = 110
    ux.mark(TRACE_ID, 'assistant_ready')
    ux.moveTurn(TRACE_ID, 'draft', 'session')
    now = 150
    ux.recordSse(TRACE_ID, TRACE_ID)
    ux.recordRouterStatus(TRACE_ID, {
      mode: 'fallback', phase: 'attempting', depth: 0, attempt: 1, total: 2,
    })
    now = 170
    ux.markStopForSession('session')
    now = 200
    const completed = ux.finishTurn(TRACE_ID, { outcome: 'stopped', statusCleared: true })

    expect(completed.timings).toMatchObject({ client_ack_ms: 10, stop_ms: 30, total_ms: 100 })
    expect(completed.router_modes).toEqual(['fallback'])
    expect(ux.activeCount).toBe(0)
    expect(ux.turns).toHaveLength(1)
    expect(ux.exportSnapshot()).toMatchObject({
      collection: 'spa-v1-browser-observed',
      privacy: 'content-free-memory-only',
      summary: { sample_size: 1 },
    })
  })

  it('does not collect anything without the explicit query flag', () => {
    vi.stubGlobal('location', { search: '' })
    const ux = useUxStore()
    ux.startTurn({ traceId: TRACE_ID, sessionKey: 'session' })
    ux.finishTurn(TRACE_ID, { outcome: 'ok', statusCleared: true })
    expect(ux.enabled).toBe(false)
    expect(ux.turns).toEqual([])
  })
})
