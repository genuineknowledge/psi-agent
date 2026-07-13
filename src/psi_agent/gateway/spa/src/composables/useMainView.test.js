import { describe, expect, it } from 'vitest'
import { MainView, computeMainView } from './useMainView.js'

describe('computeMainView', () => {
  const base = {
    loadingEnv: false,
    selectedWorkspacePath: 'D:/proj',
    draftSession: null,
    selectedSessionId: null,
  }

  it('returns loading while bootstrapping', () => {
    expect(computeMainView({ ...base, loadingEnv: true })).toBe(MainView.LOADING)
  })

  it('returns no-workspace when path is empty', () => {
    expect(computeMainView({ ...base, selectedWorkspacePath: '' })).toBe(MainView.NO_WORKSPACE)
  })

  it('returns no-session when workspace selected but no chat target', () => {
    expect(computeMainView(base)).toBe(MainView.NO_SESSION)
  })

  it('returns chat when a real session is selected', () => {
    expect(computeMainView({ ...base, selectedSessionId: 'sess-1' })).toBe(MainView.CHAT)
  })

  it('returns chat when a draft is open', () => {
    expect(
      computeMainView({
        ...base,
        draftSession: { draftId: 'draft-1', workspace: 'D:/proj', aiId: 'ai-1' },
      }),
    ).toBe(MainView.CHAT)
  })
})
