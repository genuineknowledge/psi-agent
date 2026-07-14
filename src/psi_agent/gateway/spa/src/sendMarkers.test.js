import { describe, expect, it } from 'vitest'
import { stripSendMarkers } from './sendMarkers.js'

describe('stripSendMarkers', () => {
  it('removes SEND markers and trims leftover blanks', () => {
    expect(stripSendMarkers('见附件\n[SEND:/tmp/a.html]\n\n')).toBe('见附件')
  })

  it('handles multiple markers', () => {
    expect(stripSendMarkers('[SEND:/a.md] text [SEND:/b.html]')).toBe('text')
  })

  it('returns empty for markers only', () => {
    expect(stripSendMarkers('[SEND:/only.html]')).toBe('')
  })
})
