import { describe, expect, it } from 'vitest'
import { buildSavePath, filterPickerEntries, sortPickerEntries } from './pathPicker.js'

describe('pathPicker', () => {
  it('filterPickerEntries matches case-insensitive substring', () => {
    const entries = [{ name: 'Archive' }, { name: 'bin' }]
    expect(filterPickerEntries(entries, 'arc')).toHaveLength(1)
    expect(filterPickerEntries(entries, '')).toHaveLength(2)
  })

  it('sortPickerEntries lists directories first', () => {
    const sorted = sortPickerEntries([
      { name: 'b.txt', kind: 'file' },
      { name: 'aaa', kind: 'directory' },
    ])
    expect(sorted[0].kind).toBe('directory')
  })

  it('buildSavePath joins directory and file name', () => {
    expect(buildSavePath('D:/out', 'chat.md')).toBe('D:/out/chat.md')
  })
})
