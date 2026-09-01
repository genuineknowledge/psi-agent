import { describe, expect, it } from 'vitest'
import { stripSendMarkers, stripTransferMarkers } from './sendMarkers.js'

describe('stripTransferMarkers', () => {
  it('removes SEND markers and trims leftover blanks', () => {
    expect(stripTransferMarkers('见附件\n[SEND:/tmp/a.html]\n\n')).toBe('见附件')
  })

  it('removes RECV markers (upload path leak on history reload)', () => {
    expect(
      stripTransferMarkers(
        '帮我分析一下这张图\n[RECV:C:\\Users\\Z\\Downloads\\.psi\\2026-07-14\\a.png]',
      ),
    ).toBe('帮我分析一下这张图')
  })

  it('handles multiple SEND and RECV markers', () => {
    expect(
      stripTransferMarkers('[RECV:/in.png] text [SEND:/a.md] mid [SEND:/b.html]'),
    ).toBe('text  mid')
  })

  it('returns empty for markers only', () => {
    expect(stripTransferMarkers('[RECV:/only.png]')).toBe('')
    expect(stripTransferMarkers('[SEND:/only.html]')).toBe('')
  })

  it('stripSendMarkers stays as alias', () => {
    expect(stripSendMarkers('[RECV:/x] hi [SEND:/y]')).toBe('hi')
  })
})
