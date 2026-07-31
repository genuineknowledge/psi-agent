import { describe, expect, it } from 'vitest'
import {
  filterWorkflowOptions,
  formatWorkflowCommand,
  getWorkflowCommandQuery,
  moveWorkflowSelection,
  parseWorkflowCommand,
} from './workflowCommands.js'

describe('parseWorkflowCommand', () => {
  it('leaves ordinary messages untouched', () => {
    expect(parseWorkflowCommand('hello')).toEqual({ kind: 'text' })
    expect(parseWorkflowCommand('please use /workflow:daily-brief')).toEqual({ kind: 'text' })
    expect(parseWorkflowCommand('/workflows:daily-brief')).toEqual({ kind: 'text' })
  })

  it('parses the exact workflow command', () => {
    expect(parseWorkflowCommand('/workflow:daily-brief')).toEqual({
      kind: 'workflow',
      name: 'daily-brief',
    })
    expect(parseWorkflowCommand('  /workflow:daily-brief  ')).toEqual({
      kind: 'workflow',
      name: 'daily-brief',
    })
  })

  it('rejects missing and invalid workflow names', () => {
    expect(parseWorkflowCommand('/workflow:').kind).toBe('invalid')
    expect(parseWorkflowCommand('/workflow:Daily').kind).toBe('invalid')
    expect(parseWorkflowCommand('/workflow:daily_brief').kind).toBe('invalid')
    expect(parseWorkflowCommand('/workflow:1daily').kind).toBe('invalid')
    expect(parseWorkflowCommand('/workflow:con').kind).toBe('invalid')
    expect(parseWorkflowCommand('/workflow:com9').kind).toBe('invalid')
    expect(parseWorkflowCommand('/workflow:lpt1').kind).toBe('invalid')
    expect(parseWorkflowCommand(`/workflow:a${'b'.repeat(64)}`).kind).toBe('invalid')
    expect(parseWorkflowCommand(`/workflow:a${'b'.repeat(63)}`).kind).toBe('workflow')
  })

  it('rejects every command suffix', () => {
    expect(parseWorkflowCommand('/workflow:daily anything').kind).toBe('invalid')
    expect(parseWorkflowCommand('/workflow:daily []').kind).toBe('invalid')
    expect(parseWorkflowCommand('/workflow:daily {}').kind).toBe('invalid')
  })
})

describe('getWorkflowCommandQuery', () => {
  it('returns the active partial command query', () => {
    expect(getWorkflowCommandQuery('/')).toBe('')
    expect(getWorkflowCommandQuery('/work')).toBe('')
    expect(getWorkflowCommandQuery('/workflow:')).toBe('')
    expect(getWorkflowCommandQuery('/workflow:daily')).toBe('daily')
  })

  it('closes autocomplete for ordinary text or any suffix', () => {
    expect(getWorkflowCommandQuery('hello')).toBeNull()
    expect(getWorkflowCommandQuery('/workflow:daily ')).toBeNull()
    expect(getWorkflowCommandQuery('/workflow:daily anything')).toBeNull()
  })
})

describe('filterWorkflowOptions', () => {
  const workflows = [
    { name: 'weekly-review' },
    { name: 'daily-brief' },
    { name: 'research' },
    { name: 'Invalid_Name' },
    null,
  ]

  it('filters malformed records and sorts all workflows by name', () => {
    expect(filterWorkflowOptions(workflows, '').map(item => item.name)).toEqual([
      'daily-brief',
      'research',
      'weekly-review',
    ])
  })

  it('ranks exact and prefix name matches', () => {
    expect(filterWorkflowOptions(workflows, 'research').map(item => item.name)).toEqual([
      'research',
    ])
  })
})

describe('formatWorkflowCommand', () => {
  it('formats only the exact command', () => {
    expect(formatWorkflowCommand({ name: 'daily-brief' })).toBe('/workflow:daily-brief')
    expect(() => formatWorkflowCommand({ name: '../escape' })).toThrow()
  })
})

describe('moveWorkflowSelection', () => {
  it('wraps in both directions and handles an unset selection', () => {
    expect(moveWorkflowSelection(-1, 3, 1)).toBe(0)
    expect(moveWorkflowSelection(-1, 3, -1)).toBe(2)
    expect(moveWorkflowSelection(2, 3, 1)).toBe(0)
    expect(moveWorkflowSelection(0, 3, -1)).toBe(2)
    expect(moveWorkflowSelection(0, 0, 1)).toBe(-1)
  })
})
