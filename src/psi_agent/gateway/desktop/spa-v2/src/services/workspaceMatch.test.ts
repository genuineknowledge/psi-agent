import { describe, expect, it } from 'vitest'
import {
  normalizeWorkspacePath,
  sessionBackendId,
  sessionMatchesWorkspace,
} from './workspaceMatch'

describe('normalizeWorkspacePath', () => {
  it('normalizes slashes and trailing separators', () => {
    expect(normalizeWorkspacePath('C:\\Users\\Z\\ws\\')).toBe('c:/users/z/ws')
    expect(normalizeWorkspacePath('/tmp/ws/')).toBe('/tmp/ws')
  })
})

describe('sessionMatchesWorkspace', () => {
  const ws = normalizeWorkspacePath('/Users/me/project')

  it('matches same path ignoring case and trailing slash', () => {
    expect(sessionMatchesWorkspace('/Users/me/project/', ws)).toBe(true)
    expect(sessionMatchesWorkspace('/USERS/ME/PROJECT', ws)).toBe(true)
  })

  it('includes empty session workspace (legacy)', () => {
    expect(sessionMatchesWorkspace('', ws)).toBe(true)
    expect(sessionMatchesWorkspace(undefined, ws)).toBe(true)
  })

  it('excludes other workspaces', () => {
    expect(sessionMatchesWorkspace('/Users/me/other', ws)).toBe(false)
  })
})

describe('sessionBackendId', () => {
  it('prefers ai_id then backend_id', () => {
    expect(sessionBackendId({ ai_id: 'a', backend_id: 'b' })).toBe('a')
    expect(sessionBackendId({ backend_id: 'b' })).toBe('b')
    expect(sessionBackendId({})).toBe(null)
  })
})
