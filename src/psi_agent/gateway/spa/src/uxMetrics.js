const UX_SCHEMA_VERSION = 1
const MAX_WARNING_CODES = 8

const TIMESTAMP_NAMES = new Set([
  'send_click',
  'assistant_ready',
  'request_start',
  'response_headers',
  'first_sse',
  'first_router_status',
  'first_visible_output',
  'stop_clicked',
  'stop_completed',
  'stream_done',
])

const SAFE_WARNING_CODES = new Set(['output_file_unavailable', 'stream_warning'])
const ROUTER_PHASES = Object.freeze({
  routing: new Set(['selecting', 'generating']),
  aggregation: new Set(['collecting', 'synthesizing']),
  fallback: new Set(['attempting', 'switching', 'replaying']),
})

const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value, key)
const round = value => Math.round(value * 10) / 10

function finiteTime(value) {
  return Number.isFinite(value) && value >= 0 ? value : null
}

function duration(start, end) {
  const a = finiteTime(start)
  const b = finiteTime(end)
  return a === null || b === null || b < a ? null : round(b - a)
}

function firstPhase(statuses, mode, phase) {
  const matching = statuses.filter(status => status.mode === mode)
  if (!matching.length) return null
  const rootDepth = Math.min(...matching.map(status => status.depth))
  return matching.find(status => status.depth === rootDepth && status.phase === phase)?.at ?? null
}

function validLaneSequence(statuses, mode) {
  if (mode === 'routing') {
    let generating = false
    for (const status of statuses) {
      if (status.phase === 'generating') generating = true
      if (generating && status.phase === 'selecting') return false
    }
    return true
  }

  if (mode === 'aggregation') {
    let synthesizing = false
    for (const status of statuses) {
      if (status.phase === 'synthesizing') synthesizing = true
      if (synthesizing && status.phase === 'collecting') return false
    }
    return true
  }

  let state = null
  for (const status of statuses) {
    const phase = status.phase
    if (state === null && phase !== 'attempting') return false
    if (state === 'replaying' && phase !== 'replaying') return false
    if (state === 'switching' && phase === 'replaying') return false
    if (state === 'attempting' && phase === 'attempting') {
      state = phase
      continue
    }
    if (state === 'switching' && phase === 'switching') continue
    state = phase
  }
  return true
}

function statusSequenceValid(statuses) {
  const lanes = new Map()
  for (const status of statuses) {
    const key = `${status.mode}:${status.depth}`
    const lane = lanes.get(key) ?? []
    lane.push(status)
    lanes.set(key, lane)
  }
  return [...lanes.entries()].every(([key, lane]) => validLaneSequence(lane, key.split(':')[0]))
}

function relativeTimeline(timestamps, origin) {
  const timeline = {}
  for (const name of TIMESTAMP_NAMES) {
    if (!hasOwn(timestamps, name)) continue
    const value = duration(origin, timestamps[name])
    if (value !== null) timeline[name] = value
  }
  return timeline
}

function percentile(values, quantile) {
  if (!values.length) return null
  const ordered = [...values].sort((a, b) => a - b)
  const position = (ordered.length - 1) * quantile
  const lower = Math.floor(position)
  const upper = Math.ceil(position)
  if (lower === upper) return round(ordered[lower])
  const weight = position - lower
  return round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)
}

function distribution(records, key) {
  const values = records
    .map(record => record.timings?.[key])
    .filter(value => Number.isFinite(value) && value >= 0)
  return {
    count: values.length,
    p50: percentile(values, 0.5),
    p90: percentile(values, 0.9),
    p95: percentile(values, 0.95),
  }
}

function ratio(numerator, denominator) {
  return denominator > 0 ? Math.round((numerator / denominator) * 10000) / 10000 : null
}

export function isUxDebugEnabled(search = '') {
  if (typeof search !== 'string' || !search.startsWith('?')) return false
  try {
    return new URLSearchParams(search).get('ux_debug') === '1'
  } catch (_) {
    return false
  }
}

/** Create an in-memory, content-free record for one browser-observed chat turn. */
export function createUxTurnRecord({ traceId, at, wallTime }) {
  const startedAt = finiteTime(at) ?? 0
  return {
    schema_version: UX_SCHEMA_VERSION,
    trace_id: String(traceId),
    started_at: typeof wallTime === 'string' ? wallTime : new Date().toISOString(),
    timestamps: { send_click: startedAt },
    router_statuses: [],
    sse_events: 0,
    traced_sse_events: 0,
    trace_consistent: true,
    warning_codes: [],
    fatal: false,
    outcome: null,
    status_cleared: false,
  }
}

/** Record the first occurrence of one allowlisted lifecycle timestamp. */
export function markUxTimestamp(record, name, at) {
  if (!record || !TIMESTAMP_NAMES.has(name) || hasOwn(record.timestamps, name)) return
  const value = finiteTime(at)
  if (value !== null) record.timestamps[name] = value
}

/** Count SSE trace coverage without retaining event payloads. */
export function recordUxSseEvent(record, { tracePresent, traceMatches }) {
  if (!record) return
  record.sse_events += 1
  if (tracePresent) record.traced_sse_events += 1
  if (tracePresent && !traceMatches) record.trace_consistent = false
}

/** Retain only the Router lifecycle fields needed to derive UX timings. */
export function recordUxRouterStatus(record, status, at) {
  const phases = ROUTER_PHASES[status?.mode]
  const timestamp = finiteTime(at)
  if (!record || !phases?.has(status?.phase) || timestamp === null) return
  if (!Number.isInteger(status.depth) || status.depth < 0) return

  const safe = {
    mode: status.mode,
    phase: status.phase,
    depth: status.depth,
    at: timestamp,
  }
  if (status.mode === 'aggregation') {
    if (Number.isInteger(status.completed) && status.completed >= 0) safe.completed = status.completed
    if (Number.isInteger(status.total) && status.total > 0) safe.total = status.total
    if (status.degraded === true) safe.degraded = true
  } else if (status.mode === 'fallback') {
    if (Number.isInteger(status.attempt) && status.attempt > 0) safe.attempt = status.attempt
    if (Number.isInteger(status.total) && status.total > 0) safe.total = status.total
  }
  record.router_statuses.push(safe)
  markUxTimestamp(record, 'first_router_status', timestamp)
}

/** Retain a warning allowlist or a fatal bit; raw exception text never enters metrics. */
export function recordUxIssue(record, issue) {
  if (!record || !issue) return
  if (issue.severity !== 'warning') {
    record.fatal = true
    return
  }
  const code = SAFE_WARNING_CODES.has(issue.code) ? issue.code : 'stream_warning'
  if (!record.warning_codes.includes(code) && record.warning_codes.length < MAX_WARNING_CODES) {
    record.warning_codes.push(code)
  }
}

export function finalizeUxTurn(record, { outcome, statusCleared, at }) {
  if (!record) return
  markUxTimestamp(record, 'stream_done', at)
  if (hasOwn(record.timestamps, 'stop_clicked')) {
    markUxTimestamp(record, 'stop_completed', at)
  }
  record.outcome = ['ok', 'error', 'stopped', 'incomplete'].includes(outcome)
    ? outcome
    : 'incomplete'
  record.status_cleared = statusCleared === true
}

/** Build an export-safe derived record. All times are relative to the send click. */
export function deriveUxTurn(record) {
  const timestamps = record?.timestamps ?? {}
  const statuses = Array.isArray(record?.router_statuses) ? record.router_statuses : []
  const origin = timestamps.send_click
  const routingSelecting = firstPhase(statuses, 'routing', 'selecting')
  const routingGenerating = firstPhase(statuses, 'routing', 'generating')
  const aggregationCollecting = firstPhase(statuses, 'aggregation', 'collecting')
  const aggregationSynthesizing = firstPhase(statuses, 'aggregation', 'synthesizing')
  const fallbackReplaying = firstPhase(statuses, 'fallback', 'replaying')
  const routerModes = [...new Set(statuses.map(status => status.mode))]
  const fallbackStatuses = statuses.filter(status => status.mode === 'fallback')
  const attempts = fallbackStatuses
    .map(status => status.attempt)
    .filter(value => Number.isInteger(value))

  return {
    schema_version: UX_SCHEMA_VERSION,
    trace_id: String(record?.trace_id ?? ''),
    started_at: String(record?.started_at ?? ''),
    outcome: record?.outcome ?? 'incomplete',
    timings: {
      client_ack_ms: duration(origin, timestamps.assistant_ready),
      response_header_ms: duration(timestamps.request_start, timestamps.response_headers),
      first_sse_ms: duration(origin, timestamps.first_sse),
      first_status_ms: duration(origin, timestamps.first_router_status),
      ux_ttft_ms: duration(origin, timestamps.first_visible_output),
      total_ms: duration(origin, timestamps.stream_done),
      stop_ms: duration(timestamps.stop_clicked, timestamps.stop_completed),
      routing_selection_ms: duration(routingSelecting, routingGenerating),
      aggregation_collection_ms: duration(aggregationCollecting, aggregationSynthesizing),
      aggregation_synthesis_to_visible_ms: duration(
        aggregationSynthesizing,
        timestamps.first_visible_output,
      ),
      fallback_replay_to_visible_ms: duration(fallbackReplaying, timestamps.first_visible_output),
    },
    timeline_ms: relativeTimeline(timestamps, origin),
    router_statuses: statuses.map(({ at, ...status }) => ({
      ...status,
      at_ms: duration(origin, at),
    })),
    router_modes: routerModes,
    fallback_attempts: attempts.length ? Math.max(...attempts) : 0,
    fallback_switches: fallbackStatuses.filter(status => status.phase === 'switching').length,
    fallback_recovered: fallbackStatuses.some(status => status.phase === 'replaying')
      && record?.outcome === 'ok',
    aggregation_degraded: statuses.some(status => (
      status.mode === 'aggregation' && status.degraded === true
    )),
    warning_codes: [...(record?.warning_codes ?? [])],
    fatal: record?.fatal === true,
    trace_complete: record?.sse_events > 0
      && record.traced_sse_events === record.sse_events
      && record.trace_consistent === true,
    status_sequence_valid: statusSequenceValid(statuses),
    status_cleared: record?.status_cleared === true,
  }
}

export function summarizeUxTurns(records) {
  const turns = Array.isArray(records) ? records : []
  const fallback = turns.filter(turn => turn.router_modes?.includes('fallback'))
  const aggregation = turns.filter(turn => turn.router_modes?.includes('aggregation'))
  const count = turns.length

  return {
    schema_version: UX_SCHEMA_VERSION,
    sample_size: count,
    timings: {
      client_ack_ms: distribution(turns, 'client_ack_ms'),
      response_header_ms: distribution(turns, 'response_header_ms'),
      first_sse_ms: distribution(turns, 'first_sse_ms'),
      first_status_ms: distribution(turns, 'first_status_ms'),
      ux_ttft_ms: distribution(turns, 'ux_ttft_ms'),
      total_ms: distribution(turns, 'total_ms'),
      stop_ms: distribution(turns, 'stop_ms'),
      routing_selection_ms: distribution(turns, 'routing_selection_ms'),
      aggregation_collection_ms: distribution(turns, 'aggregation_collection_ms'),
      aggregation_synthesis_to_visible_ms: distribution(
        turns,
        'aggregation_synthesis_to_visible_ms',
      ),
      fallback_replay_to_visible_ms: distribution(turns, 'fallback_replay_to_visible_ms'),
    },
    rates: {
      success: ratio(turns.filter(turn => turn.outcome === 'ok').length, count),
      fatal: ratio(turns.filter(turn => turn.fatal).length, count),
      warning: ratio(turns.filter(turn => turn.warning_codes?.length).length, count),
      trace_complete: ratio(turns.filter(turn => turn.trace_complete).length, count),
      status_sequence_valid: ratio(turns.filter(turn => turn.status_sequence_valid).length, count),
      status_cleared: ratio(turns.filter(turn => turn.status_cleared).length, count),
      fallback_activation: ratio(fallback.length, count),
      fallback_recovery: ratio(fallback.filter(turn => turn.fallback_recovered).length, fallback.length),
      aggregation_degraded: ratio(
        aggregation.filter(turn => turn.aggregation_degraded).length,
        aggregation.length,
      ),
    },
  }
}
