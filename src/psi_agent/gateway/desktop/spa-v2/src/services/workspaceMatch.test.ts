import { describe, expect, it } from 'vitest'
import {
  normalizeWorkspacePath,
  sessionMatchesWorkspace,
} from './workspaceMatch'

describe('sessionMatchesWorkspace', () => {
  const ws = normalizeWorkspacePath('/Users/me/project')

  it('matches equivalent path spellings', () => {
    expect(sessionMatchesWorkspace('/Users/me/project/', ws)).toBe(true)
    expect(sessionMatchesWorkspace('/USERS/ME/PROJECT', ws)).toBe(true)
  })

  it('empty session workspace matches any open folder when defaults unset', () => {
    expect(sessionMatchesWorkspace('', ws)).toBe(true)
    expect(sessionMatchesWorkspace(undefined, ws)).toBe(true)
  })

  it('empty session workspace matches only the Gateway default folder', () => {
    const defaults = normalizeWorkspacePath('/Users/me/Desktop/haitun交付')
    expect(sessionMatchesWorkspace('', defaults, defaults)).toBe(true)
    expect(
      sessionMatchesWorkspace('', normalizeWorkspacePath('/other'), defaults),
    ).toBe(false)
  })

  it('rejects other folders', () => {
    expect(sessionMatchesWorkspace('/Users/me/other', ws)).toBe(false)
  })
})
