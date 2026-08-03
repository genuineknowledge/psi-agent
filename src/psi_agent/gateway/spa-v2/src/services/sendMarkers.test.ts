import { describe, expect, it } from 'vitest'
import { stripTransferMarkers } from './sendMarkers'

describe('stripTransferMarkers', () => {
  it('strips space-padded and lowercase markers', () => {
    expect(
      stripTransferMarkers('好的\n[Send:/tmp/out.md]\n[ RECV:C:/docs/a.png ]'),
    ).toBe('好的')
  })
})
