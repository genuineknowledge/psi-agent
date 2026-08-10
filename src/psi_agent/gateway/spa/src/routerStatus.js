const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const PHASES_BY_MODE = Object.freeze({
  routing: new Set(['selecting', 'generating']),
  aggregation: new Set(['collecting', 'synthesizing']),
  fallback: new Set(['attempting', 'switching', 'replaying']),
})

const MODE_PRESENTATION = Object.freeze({
  routing: { label: '智能路由', icon: 'alt_route' },
  aggregation: { label: '多模型汇总', icon: 'hub' },
  fallback: { label: '自动切换', icon: 'swap_horiz' },
})

const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value, key)
const isNonNegativeInteger = value => Number.isInteger(value) && value >= 0
const isPositiveInteger = value => Number.isInteger(value) && value > 0

/**
 * Validate one flattened Gateway router_status event and retain UI-safe fields only.
 * Invalid or incomplete lifecycle snapshots are ignored by returning null.
 */
export function normalizeRouterStatusEvent(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  if (value.type !== 'router_status' || value.version !== 1) return null
  if (typeof value.trace_id !== 'string') return null

  const traceId = value.trace_id.trim().toLowerCase()
  if (!UUID_PATTERN.test(traceId)) return null

  if (!hasOwn(PHASES_BY_MODE, value.mode)) return null
  const phases = PHASES_BY_MODE[value.mode]
  if (!phases.has(value.phase)) return null
  if (!isNonNegativeInteger(value.depth)) return null

  const normalized = {
    type: 'router_status',
    version: 1,
    trace_id: traceId,
    mode: value.mode,
    phase: value.phase,
    depth: value.depth,
  }

  const hasCompleted = hasOwn(value, 'completed')
  const hasTotal = hasOwn(value, 'total')
  const hasAttempt = hasOwn(value, 'attempt')
  const hasDegraded = hasOwn(value, 'degraded')

  if (value.mode === 'aggregation') {
    if (!hasCompleted || !hasTotal || hasAttempt) return null
    if (!isNonNegativeInteger(value.completed) || !isPositiveInteger(value.total)) return null
    if (value.completed > value.total) return null
    if (hasDegraded && typeof value.degraded !== 'boolean') return null

    normalized.completed = value.completed
    normalized.total = value.total
    if (value.degraded === true) normalized.degraded = true
    return normalized
  }

  if (value.mode === 'fallback') {
    if (!hasAttempt || !hasTotal || hasCompleted || hasDegraded) return null
    if (!isPositiveInteger(value.attempt) || !isPositiveInteger(value.total)) return null
    if (value.attempt > value.total) return null

    normalized.attempt = value.attempt
    normalized.total = value.total
    return normalized
  }

  if (hasCompleted || hasTotal || hasAttempt || hasDegraded) return null
  return normalized
}

/** Convert a normalized Router status into concise user-facing copy. */
export function describeRouterStatus(status) {
  const presentation = MODE_PRESENTATION[status.mode]
  let message = ''
  let badge = ''

  if (status.phase === 'selecting') {
    message = '正在选择最合适的模型'
  } else if (status.phase === 'generating') {
    message = '已选定模型，正在生成回复'
  } else if (status.phase === 'collecting') {
    message = `正在并行获取 ${status.total} 个模型的回答`
  } else if (status.phase === 'synthesizing') {
    message = status.degraded
      ? '部分模型未完成，正在综合可用回答'
      : '正在综合多个模型的回答'
  } else if (status.phase === 'attempting') {
    message = `正在尝试第 ${status.attempt} 个模型`
  } else if (status.phase === 'switching') {
    message = '当前模型未完成，正在切换'
  } else if (status.phase === 'replaying') {
    message = '已找到可用模型，正在返回结果'
  }

  if (status.mode === 'aggregation') {
    badge = status.degraded ? '降级汇总' : `${status.total} 路并行`
  } else if (status.mode === 'fallback') {
    badge = `${status.attempt}/${status.total}`
  }

  return {
    label: presentation.label,
    message,
    icon: presentation.icon,
    badge,
    nested: status.depth > 0,
  }
}
