import { describe, expect, it } from 'vitest'

import {
  applyStreamIssueToAssistant,
  describeStreamWarning,
  normalizeStreamIssueEvent,
  normalizeStreamWarningCodes,
} from './streamIssue.js'

describe('normalizeStreamIssueEvent', () => {
  it('treats legacy and invalid severities as fatal', () => {
    expect(normalizeStreamIssueEvent({ type: 'error', error: 'socket failed' })).toEqual({
      severity: 'fatal',
      code: 'chat_failed',
    })
    expect(normalizeStreamIssueEvent({
      type: 'error',
      severity: 'unexpected',
      code: 'output_file_unavailable',
    })).toEqual({
      severity: 'fatal',
      code: 'chat_failed',
    })
  })

  it('keeps only allowlisted warning metadata', () => {
    expect(normalizeStreamIssueEvent({
      type: 'error',
      severity: 'warning',
      code: 'output_file_unavailable',
      error: 'C:/private/generated.txt could not be opened',
      socket: '/private/session.sock',
    })).toEqual({
      severity: 'warning',
      code: 'output_file_unavailable',
    })
  })

  it('maps unknown warning codes to a safe generic warning', () => {
    expect(normalizeStreamIssueEvent({
      type: 'error',
      severity: 'warning',
      code: 'future_warning',
      error: 'private upstream detail',
    })).toEqual({
      severity: 'warning',
      code: 'stream_warning',
    })
  })

  it.each([null, [], {}, { type: 'text' }])('ignores non-error values: %j', value => {
    expect(normalizeStreamIssueEvent(value)).toBeNull()
  })
})

describe('applyStreamIssueToAssistant', () => {
  it('marks partial output fatal without copying raw error details into the message', () => {
    const assistant = { role: 'assistant', text: 'partial answer', warnings: [] }

    const issue = applyStreamIssueToAssistant(assistant, {
      type: 'error',
      severity: 'fatal',
      error: 'private upstream failure',
      socket: '/private/session.sock',
    })

    expect(issue).toEqual({ severity: 'fatal', code: 'chat_failed' })
    expect(assistant).toEqual({
      role: 'assistant',
      text: 'partial answer',
      warnings: [],
      fatal: true,
    })
    expect(JSON.stringify(assistant)).not.toContain('private')
  })

  it('deduplicates recoverable warning codes without marking the answer fatal', () => {
    const assistant = { role: 'assistant', text: 'usable answer' }
    const event = {
      type: 'error',
      severity: 'warning',
      code: 'output_file_unavailable',
      error: 'private file path',
    }

    applyStreamIssueToAssistant(assistant, event)
    applyStreamIssueToAssistant(assistant, event)

    expect(assistant).toEqual({
      role: 'assistant',
      text: 'usable answer',
      warnings: ['output_file_unavailable'],
    })
  })
})

describe('describeStreamWarning', () => {
  it('returns concise copy for known and future warning codes', () => {
    expect(describeStreamWarning('output_file_unavailable')).toBe('部分生成文件未能加载，请重试。')
    expect(describeStreamWarning('future_warning')).toBe('部分内容未能完整加载。')
    expect(describeStreamWarning('toString')).toBe('部分内容未能完整加载。')
  })

  it('normalizes persisted warning codes without duplicates', () => {
    expect(normalizeStreamWarningCodes([
      'output_file_unavailable',
      'private_future_code',
      'output_file_unavailable',
    ])).toEqual(['output_file_unavailable', 'stream_warning'])
    expect(normalizeStreamWarningCodes('output_file_unavailable')).toEqual([])
  })
})
