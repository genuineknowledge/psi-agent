import { describe, expect, it } from 'vitest'

import {
  createUxTurnRecord,
  deriveUxTurn,
  finalizeUxTurn,
  isUxDebugEnabled,
  markUxTimestamp,
  recordUxIssue,
  recordUxRouterStatus,
  recordUxSseEvent,
  summarizeUxTurns,
} from './uxMetrics.js'

const TRACE_ID = '123e4567-e89b-42d3-a456-426614174000'

function turnAt(at = 100) {
  return createUxTurnRecord({ traceId: TRACE_ID, at, wallTime: '2026-08-11T10:00:00.000Z' })
}

describe('SPA v1 UX metrics', () => {
  it('derives browser-observed latency without retaining conversation content', () => {
    const turn = turnAt()
    markUxTimestamp(turn, 'assistant_ready', 112)
    markUxTimestamp(turn, 'request_start', 130)
    markUxTimestamp(turn, 'response_headers', 180)
    markUxTimestamp(turn, 'first_sse', 190)
    markUxTimestamp(turn, 'first_visible_output', 240)
    recordUxSseEvent(turn, { tracePresent: true, traceMatches: true })
    finalizeUxTurn(turn, { outcome: 'ok', statusCleared: true, at: 400 })

    const derived = deriveUxTurn(turn)
    expect(derived.timings).toEqual({
      client_ack_ms: 12,
      response_header_ms: 50,
      first_sse_ms: 90,
      first_status_ms: null,
      ux_ttft_ms: 140,
      total_ms: 300,
      stop_ms: null,
      routing_selection_ms: null,
      aggregation_collection_ms: null,
      aggregation_synthesis_to_visible_ms: null,
      fallback_replay_to_visible_ms: null,
    })
    expect(derived.trace_complete).toBe(true)
    expect(JSON.stringify(derived)).not.toContain('prompt')
    expect(JSON.stringify(derived)).not.toContain('assistant_text')
  })

  it('measures routing selection and aggregation degradation phases', () => {
    const routing = turnAt()
    recordUxRouterStatus(routing, {
      mode: 'routing', phase: 'selecting', depth: 0,
    }, 120)
    recordUxRouterStatus(routing, {
      mode: 'routing', phase: 'generating', depth: 0,
    }, 175)
    finalizeUxTurn(routing, { outcome: 'ok', statusCleared: true, at: 300 })
    expect(deriveUxTurn(routing).timings.routing_selection_ms).toBe(55)

    const aggregation = turnAt()
    recordUxRouterStatus(aggregation, {
      mode: 'aggregation', phase: 'collecting', depth: 0, completed: 0, total: 3,
    }, 115)
    recordUxRouterStatus(aggregation, {
      mode: 'aggregation', phase: 'synthesizing', depth: 0,
      completed: 2, total: 3, degraded: true,
    }, 260)
    markUxTimestamp(aggregation, 'first_visible_output', 310)
    finalizeUxTurn(aggregation, { outcome: 'ok', statusCleared: true, at: 450 })

    const result = deriveUxTurn(aggregation)
    expect(result.timings.aggregation_collection_ms).toBe(145)
    expect(result.timings.aggregation_synthesis_to_visible_ms).toBe(50)
    expect(result.aggregation_degraded).toBe(true)
    expect(result.status_sequence_valid).toBe(true)
  })

  it('counts fallback attempts, switches and successful recovery', () => {
    const turn = turnAt()
    recordUxRouterStatus(turn, {
      mode: 'fallback', phase: 'attempting', depth: 0, attempt: 1, total: 3,
    }, 110)
    recordUxRouterStatus(turn, {
      mode: 'fallback', phase: 'switching', depth: 0, attempt: 1, total: 3,
    }, 180)
    recordUxRouterStatus(turn, {
      mode: 'fallback', phase: 'attempting', depth: 0, attempt: 2, total: 3,
    }, 190)
    recordUxRouterStatus(turn, {
      mode: 'fallback', phase: 'replaying', depth: 0, attempt: 2, total: 3,
    }, 270)
    markUxTimestamp(turn, 'first_visible_output', 305)
    finalizeUxTurn(turn, { outcome: 'ok', statusCleared: true, at: 360 })

    const result = deriveUxTurn(turn)
    expect(result.fallback_attempts).toBe(2)
    expect(result.fallback_switches).toBe(1)
    expect(result.fallback_recovered).toBe(true)
    expect(result.timings.fallback_replay_to_visible_ms).toBe(35)
  })

  it('detects invalid status order and records only safe issue codes', () => {
    const turn = turnAt()
    recordUxRouterStatus(turn, {
      mode: 'aggregation', phase: 'synthesizing', depth: 0,
      completed: 2, total: 2, model: 'secret-model',
    }, 120)
    recordUxRouterStatus(turn, {
      mode: 'aggregation', phase: 'collecting', depth: 0,
      completed: 1, total: 2,
    }, 130)
    recordUxIssue(turn, { severity: 'warning', code: 'output_file_unavailable', message: 'secret' })
    recordUxIssue(turn, { severity: 'fatal', code: 'upstream-secret', message: 'secret' })
    finalizeUxTurn(turn, { outcome: 'error', statusCleared: true, at: 200 })

    const result = deriveUxTurn(turn)
    expect(result.status_sequence_valid).toBe(false)
    expect(result.warning_codes).toEqual(['output_file_unavailable'])
    expect(result.fatal).toBe(true)
    expect(JSON.stringify(result)).not.toContain('secret')
  })

  it('measures Stop completion from click until turn cleanup', () => {
    const turn = turnAt()
    markUxTimestamp(turn, 'stop_clicked', 160)
    finalizeUxTurn(turn, { outcome: 'stopped', statusCleared: true, at: 205 })
    expect(deriveUxTurn(turn).timings.stop_ms).toBe(45)
  })

  it('summarizes percentiles and UX rates with relevant denominators', () => {
    const records = [100, 200, 300, 400].map((ttft, index) => ({
      schema_version: 1,
      trace_id: `${TRACE_ID.slice(0, -1)}${index}`,
      started_at: '2026-08-11T10:00:00.000Z',
      outcome: index === 3 ? 'error' : 'ok',
      timings: { ux_ttft_ms: ttft, total_ms: ttft + 500, stop_ms: null },
      router_modes: index < 2 ? ['fallback'] : ['aggregation'],
      fallback_recovered: index === 0,
      aggregation_degraded: index === 2,
      fatal: index === 3,
      warning_codes: index === 1 ? ['stream_warning'] : [],
      trace_complete: index !== 3,
      status_sequence_valid: true,
      status_cleared: true,
    }))

    const summary = summarizeUxTurns(records)
    expect(summary.sample_size).toBe(4)
    expect(summary.timings.ux_ttft_ms).toEqual({ count: 4, p50: 250, p90: 370, p95: 385 })
    expect(summary.rates).toMatchObject({
      success: 0.75,
      fatal: 0.25,
      warning: 0.25,
      trace_complete: 0.75,
      fallback_activation: 0.5,
      fallback_recovery: 0.5,
      aggregation_degraded: 0.5,
    })
  })

  it('enables collection only for the explicit debug query flag', () => {
    expect(isUxDebugEnabled('?ux_debug=1')).toBe(true)
    expect(isUxDebugEnabled('?ux_debug=0')).toBe(false)
    expect(isUxDebugEnabled('?foo=1')).toBe(false)
    expect(isUxDebugEnabled('not a query')).toBe(false)
  })
})
