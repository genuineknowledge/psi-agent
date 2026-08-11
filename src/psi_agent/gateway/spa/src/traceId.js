export const TRACE_ID_HEADER = 'X-Psi-Trace-Id'

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function normalizeTraceId(value) {
  if (typeof value !== 'string') return null
  const normalized = value.trim().toLowerCase()
  return UUID_PATTERN.test(normalized) ? normalized : null
}

export function createTraceId() {
  const nativeValue = globalThis.crypto?.randomUUID?.()
  const nativeTraceId = normalizeTraceId(nativeValue)
  if (nativeTraceId) return nativeTraceId

  const bytes = new Uint8Array(16)
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256)
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function assertMatchingTraceId(value, expected) {
  const normalized = normalizeTraceId(value)
  if (!normalized || normalized !== expected) {
    throw new Error('Gateway returned a mismatched request trace ID')
  }
  return normalized
}
