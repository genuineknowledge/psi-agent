import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useUiStore } from './ui.js'

describe('ui store focusSessionSearch', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('focusSessionSearch 递增 token', () => {
    const ui = useUiStore()
    expect(ui.sessionSearchFocusToken).toBe(0)
    ui.focusSessionSearch()
    expect(ui.sessionSearchFocusToken).toBe(1)
    ui.focusSessionSearch()
    expect(ui.sessionSearchFocusToken).toBe(2)
  })
})
