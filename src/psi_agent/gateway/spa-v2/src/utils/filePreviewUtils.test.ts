import { describe, expect, it } from 'vitest'
import {
  isAbsoluteFsPath,
  resolveDeliverablePath,
} from './filePreviewUtils'

describe('resolveDeliverablePath', () => {
  it('keeps absolute windows / posix paths', () => {
    expect(isAbsoluteFsPath('D:\\ws\\a.md')).toBe(true)
    expect(isAbsoluteFsPath('/tmp/a.md')).toBe(true)
    expect(resolveDeliverablePath('D:\\ws\\a.md', 'C:\\other')).toBe('D:\\ws\\a.md')
    expect(resolveDeliverablePath('/tmp/a.md', '/ws')).toBe('/tmp/a.md')
  })

  it('joins relative paths under workspace', () => {
    expect(resolveDeliverablePath('out/a.md', 'D:/ws')).toBe('D:/ws/out/a.md')
    expect(resolveDeliverablePath('\\out\\a.md', 'D:/ws/')).toBe('D:/ws/out/a.md')
  })
})
