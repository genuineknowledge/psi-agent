import { describe, expect, it } from 'vitest'
import { buildQueuedSend, setQueuedForCard } from './queuedSend'

describe('buildQueuedSend', () => {
  it('returns null when empty', () => {
    expect(buildQueuedSend('', [], '已上传：')).toBeNull()
    expect(buildQueuedSend('   ', [], '已上传：')).toBeNull()
  })

  it('uses text as display', () => {
    const q = buildQueuedSend('下一句', [], '已上传：')
    expect(q).toEqual({ text: '下一句', files: [], display: '下一句' })
  })

  it('falls back to uploaded prefix for file-only', () => {
    const f = new File(['x'], 'a.png')
    const q = buildQueuedSend('', [f], '已上传：')
    expect(q?.text).toBe('')
    expect(q?.display).toBe('已上传：a.png')
    expect(q?.files).toHaveLength(1)
  })
})

describe('setQueuedForCard', () => {
  it('replaces prior queue for the same card', () => {
    const first = buildQueuedSend('a', [], '')!
    const second = buildQueuedSend('b', [], '')!
    const mid = setQueuedForCard({}, 't1', first)
    expect(setQueuedForCard(mid, 't1', second).t1?.display).toBe('b')
  })
})
