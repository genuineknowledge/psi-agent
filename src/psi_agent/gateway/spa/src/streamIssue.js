const WARNING_CODES = new Set(['output_file_unavailable', 'stream_warning'])

const WARNING_COPY = Object.freeze({
  output_file_unavailable: '部分生成文件未能加载，请重试。',
  stream_warning: '部分内容未能完整加载。',
})

const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value, key)

/**
 * Normalize one Gateway error event without retaining raw exception details.
 * Missing or unknown severities fail closed as fatal for legacy compatibility.
 */
export function normalizeStreamIssueEvent(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  if (value.type !== 'error') return null

  if (value.severity !== 'warning') {
    return { severity: 'fatal', code: 'chat_failed' }
  }

  const code = typeof value.code === 'string' && WARNING_CODES.has(value.code)
    ? value.code
    : 'stream_warning'
  return { severity: 'warning', code }
}

/** Keep persisted warning metadata small, unique, and safe to render. */
export function normalizeStreamWarningCodes(value) {
  if (!Array.isArray(value)) return []
  const normalized = value.map(code => (
    typeof code === 'string' && WARNING_CODES.has(code) ? code : 'stream_warning'
  ))
  return [...new Set(normalized)]
}

/** Apply one normalized issue to an in-memory assistant without retaining raw errors. */
export function applyStreamIssueToAssistant(assistant, value) {
  const issue = normalizeStreamIssueEvent(value)
  if (!issue || !assistant || typeof assistant !== 'object' || Array.isArray(assistant)) {
    return issue
  }

  if (issue.severity === 'warning') {
    assistant.warnings = normalizeStreamWarningCodes([
      ...(Array.isArray(assistant.warnings) ? assistant.warnings : []),
      issue.code,
    ])
  } else {
    assistant.fatal = true
  }
  return issue
}

export function describeStreamWarning(code) {
  return hasOwn(WARNING_COPY, code) ? WARNING_COPY[code] : WARNING_COPY.stream_warning
}
