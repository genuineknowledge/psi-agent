import { describe, it, expect } from 'vitest'
import { matchSidebarShortcut } from './shortcuts.js'

const ev = (o) => ({ ctrlKey: false, metaKey: false, shiftKey: false, code: '', ...o })

describe('matchSidebarShortcut', () => {
  it('Ctrl+Shift+O → new-session', () => {
    expect(matchSidebarShortcut(ev({ ctrlKey: true, shiftKey: true, code: 'KeyO' }))).toBe('new-session')
  })
  it('Cmd+Shift+O → new-session', () => {
    expect(matchSidebarShortcut(ev({ metaKey: true, shiftKey: true, code: 'KeyO' }))).toBe('new-session')
  })
  it('Ctrl+Shift+K → focus-search', () => {
    expect(matchSidebarShortcut(ev({ ctrlKey: true, shiftKey: true, code: 'KeyK' }))).toBe('focus-search')
  })
  it('无 shift → null', () => {
    expect(matchSidebarShortcut(ev({ ctrlKey: true, code: 'KeyO' }))).toBe(null)
  })
  it('无修饰键 → null', () => {
    expect(matchSidebarShortcut(ev({ shiftKey: true, code: 'KeyO' }))).toBe(null)
  })
  it('其它按键 → null', () => {
    expect(matchSidebarShortcut(ev({ ctrlKey: true, shiftKey: true, code: 'KeyJ' }))).toBe(null)
  })
})
