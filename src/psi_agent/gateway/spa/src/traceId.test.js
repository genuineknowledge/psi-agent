import { describe, expect, it, vi } from 'vitest'

import {
  assertMatchingTraceId,
  createTraceId,
  normalizeTraceId,
  TRACE_ID_HEADER,
} from './traceId.js'

const TRACE_ID = '123e4567-e89b-12d3-a456-426614174000'

describe('trace IDs', () => {
  it('uses the shared internal header name', () => {
    expect(TRACE_ID_HEADER).toBe('X-Psi-Trace-Id')
  })

  it('normalizes canonical UUID values', () => {
    expect(normalizeTraceId(`  ${TRACE_ID.toUpperCase()}  `)).toBe(TRACE_ID)
    expect(normalizeTraceId('not-a-uuid')).toBeNull()
  })

  it('creates a canonical UUID through browser crypto', () => {
    const spy = vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(TRACE_ID)
    expect(createTraceId()).toBe(TRACE_ID)
    spy.mockRestore()
  })

  it('rejects an event from another request', () => {
    expect(() => assertMatchingTraceId(
      '123e4567-e89b-12d3-a456-426614174001',
      TRACE_ID,
    )).toThrow(/mismatched/)
  })
})
