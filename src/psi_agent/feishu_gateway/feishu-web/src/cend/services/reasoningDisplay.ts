/**
 * DeepSeek / Cursor-style thinking + tool display helpers.
 * Session packs model thinking + tool markers into SSE ``reasoning``.
 * Post-turn UI splits them: tool activity list (primary) + expandable thinking prose.
 */

import { summarizeToolCall } from './turnProgress'

const TOOL_CALL_PREFIX = '[Tool Call:'
const TOOL_RESULT_PREFIX = '[Tool Result:'

export type ReasoningSegment =
  | { kind: 'thinking'; text: string }
  | { kind: 'tool_call'; name: string; args: string; summary: string }

function findMatchingParen(s: string, openIdx: number): number {
  let depth = 0
  for (let i = openIdx; i < s.length; i++) {
    const ch = s[i]
    if (ch === '(') depth++
    else if (ch === ')') {
      depth--
      if (depth === 0) return i
    }
  }
  return -1
}

function matchToolCall(
  buf: string,
): { end: number; name: string; args: string } | null {
  if (!buf.startsWith(TOOL_CALL_PREFIX)) return null
  let i = TOOL_CALL_PREFIX.length
  while (i < buf.length && /\s/.test(buf[i]!)) i++
  const nameStart = i
  while (i < buf.length && /[A-Za-z0-9_.-]/.test(buf[i]!)) i++
  if (i === nameStart) return null
  const name = buf.slice(nameStart, i)
  while (i < buf.length && /\s/.test(buf[i]!)) i++
  if (buf[i] !== '(') return null
  const close = findMatchingParen(buf, i)
  if (close < 0 || buf[close + 1] !== ']') return null
  return {
    end: close + 2,
    name,
    args: buf.slice(i + 1, close),
  }
}

function matchToolResult(buf: string): { end: number } | null {
  if (!buf.startsWith(TOOL_RESULT_PREFIX)) return null
  let i = TOOL_RESULT_PREFIX.length
  while (i < buf.length && /\s/.test(buf[i]!)) i++
  const nextCall = buf.indexOf(TOOL_CALL_PREFIX, i)
  const nextResult = buf.indexOf(TOOL_RESULT_PREFIX, i)
  let limit = buf.length
  if (nextCall >= 0) limit = Math.min(limit, nextCall)
  if (nextResult >= 0) limit = Math.min(limit, nextResult)
  const close = buf.lastIndexOf(']', limit - 1)
  if (close < i) return null
  return { end: close + 1 }
}

function isPartialToolPrefix(s: string): boolean {
  if (!s.startsWith('[')) return false
  return (
    TOOL_CALL_PREFIX.startsWith(s)
    || TOOL_RESULT_PREFIX.startsWith(s)
    || s.startsWith(TOOL_CALL_PREFIX)
    || s.startsWith(TOOL_RESULT_PREFIX)
  )
}

function pushThinking(out: ReasoningSegment[], text: string): void {
  const cleaned = text
    .replace(/\[Working…\]/g, '')
    .replace(/\[Working\.\.\.\]/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  if (!cleaned) return
  const last = out[out.length - 1]
  if (last?.kind === 'thinking') {
    last.text = `${last.text}\n\n${cleaned}`
    return
  }
  out.push({ kind: 'thinking', text: cleaned })
}

/**
 * Split raw Session reasoning into thinking prose vs tool_call segments.
 * ``tool_result`` markers are consumed (not shown as sealed rows — same as live process axis).
 * Incomplete trailing markers (still streaming) are held back.
 */
export function parseReasoningSegments(raw: string): ReasoningSegment[] {
  let buf = typeof raw === 'string' ? raw : ''
  const out: ReasoningSegment[] = []

  while (buf) {
    const callIdx = buf.indexOf(TOOL_CALL_PREFIX)
    const resultIdx = buf.indexOf(TOOL_RESULT_PREFIX)
    let idx = -1
    let kind: 'call' | 'result' | null = null
    if (callIdx >= 0 && (resultIdx < 0 || callIdx <= resultIdx)) {
      idx = callIdx
      kind = 'call'
    } else if (resultIdx >= 0) {
      idx = resultIdx
      kind = 'result'
    }

    if (idx < 0) {
      const bracket = buf.lastIndexOf('[')
      if (bracket >= 0 && isPartialToolPrefix(buf.slice(bracket))) {
        pushThinking(out, buf.slice(0, bracket))
        break
      }
      pushThinking(out, buf)
      break
    }

    pushThinking(out, buf.slice(0, idx))
    const rest = buf.slice(idx)
    if (kind === 'call') {
      const matched = matchToolCall(rest)
      if (!matched) break
      out.push({
        kind: 'tool_call',
        name: matched.name,
        args: matched.args,
        summary: summarizeToolCall(matched.name, matched.args || '{}'),
      })
      buf = rest.slice(matched.end)
      continue
    }
    const matched = matchToolResult(rest)
    if (!matched) break
    buf = rest.slice(matched.end)
  }

  return out
}

/** Thinking prose only (tool markers stripped). */
export function stripToolMarkersFromReasoning(raw: string): string {
  return parseReasoningSegments(raw)
    .filter((s): s is Extract<ReasoningSegment, { kind: 'thinking' }> => s.kind === 'thinking')
    .map((s) => s.text)
    .join('\n\n')
    .trim()
}

/** Cursor-style sealed tool one-liners from raw reasoning. */
export function toolSummariesFromReasoning(raw: string): string[] {
  const lines: string[] = []
  for (const seg of parseReasoningSegments(raw)) {
    if (seg.kind !== 'tool_call') continue
    const last = lines[lines.length - 1]
    if (last === seg.summary) continue
    lines.push(seg.summary)
  }
  return lines
}

/** Whether cleaned thinking text is worth showing. */
export function hasDisplayableReasoning(raw: string): boolean {
  return !!stripToolMarkersFromReasoning(raw)
}

/** Raw reasoning has tool activity even if prose was stripped for display. */
export function hasToolMarkerReasoning(raw: string): boolean {
  return toolSummariesFromReasoning(raw).length > 0
}

/** Show post-turn process block when thinking and/or tools exist. */
export function hasTurnProcess(raw: string): boolean {
  return hasDisplayableReasoning(raw) || hasToolMarkerReasoning(raw)
}

/** Header label for the thinking panel (Chinese only). */
export function thinkingHeaderLabel(opts: {
  streaming?: boolean
  slow?: boolean
  hasBody?: boolean
  stopped?: boolean
  syncing?: boolean
} = {}): string {
  if (opts.stopped) return '已停止'
  if (opts.syncing) return '正在同步…'
  if (opts.slow && !opts.hasBody) return '仍在处理，比平时久一点…'
  if (opts.streaming) return '思考中'
  return '已思考'
}

/** Header for the tools disclosure. */
export function toolsHeaderLabel(count: number): string {
  if (count <= 0) return '工具'
  if (count === 1) return '已调用 1 个工具'
  return `已调用 ${count} 个工具`
}
