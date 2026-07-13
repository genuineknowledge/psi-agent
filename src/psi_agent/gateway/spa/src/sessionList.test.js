import { describe, expect, it } from 'vitest'
import {
  buildWorkspaceGroups,
  getWorkspaceLabel,
  mergeWorkspacePaths,
  normalizeWorkspacePath,
  resolveSessionWorkspace,
  sessionsForWorkspace,
} from './sessionList.js'

describe('normalizeWorkspacePath', () => {
  it('normalizes slashes and trims trailing slash', () => {
    expect(normalizeWorkspacePath('D:\\foo\\bar\\')).toBe('D:/foo/bar')
    expect(normalizeWorkspacePath('/tmp/ws/')).toBe('/tmp/ws')
  })
})

describe('mergeWorkspacePaths', () => {
  it('merges registered paths and session workspaces', () => {
    const sessions = [{ workspace: 'D:/a' }, { workspace: '' }]
    const merged = mergeWorkspacePaths(['D:/b'], sessions, 'D:/cwd')
    expect(merged).toContain('D:/b')
    expect(merged).toContain('D:/a')
    expect(merged).toContain('D:/cwd')
  })
})

describe('sessionsForWorkspace', () => {
  it('groups sessions by resolved workspace', () => {
    const sessions = [
      { id: '1', workspace: 'D:/proj' },
      { id: '2', workspace: '' },
      { id: '3', workspace: 'D:/proj' },
    ]
    const inProj = sessionsForWorkspace(sessions, 'D:/proj', 'D:/ignored')
    expect(inProj.map(s => s.id)).toEqual(['1', '3'])
    const inCwd = sessionsForWorkspace(sessions, 'D:/cwd', 'D:/cwd')
    expect(inCwd.map(s => s.id)).toEqual(['2'])
  })
})

describe('buildWorkspaceGroups', () => {
  it('builds sidebar groups with labels', () => {
    const groups = buildWorkspaceGroups(
      [{ id: 's1', workspace: 'D:/foo/bar' }],
      { registered: ['D:/foo/bar'], titles: { s1: 'Chat A' } },
    )
    expect(groups).toHaveLength(1)
    expect(groups[0].label).toBe('bar')
    expect(groups[0].sessions).toHaveLength(1)
  })
})

describe('getWorkspaceLabel', () => {
  it('uses last path segment', () => {
    expect(getWorkspaceLabel('D:/projects/haitun-workspace')).toBe('haitun-workspace')
  })
})

describe('resolveSessionWorkspace', () => {
  it('falls back to default cwd when empty', () => {
    expect(resolveSessionWorkspace({ workspace: '' }, 'D:/default')).toBe('D:/default')
  })
})
