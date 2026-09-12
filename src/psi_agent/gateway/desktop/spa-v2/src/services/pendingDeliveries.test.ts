import { beforeEach, describe, expect, it, vi } from 'vitest'
import { bindAppdataFingerprint } from './appdataScope'
import {
  addPendingDeliveries,
  clearPendingDeliveries,
  pendingDeliveriesFor,
  remapPendingDeliveries,
} from './pendingDeliveries'

function stubWindowLocalStorage() {
  const store = new Map<string, string>()
  const localStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      store.set(k, String(v))
    },
    removeItem: (k: string) => {
      store.delete(k)
    },
    clear: () => {
      store.clear()
    },
  }
  vi.stubGlobal('window', { localStorage })
}

describe('remapPendingDeliveries', () => {
  beforeEach(() => {
    stubWindowLocalStorage()
    bindAppdataFingerprint('test0001')
    clearPendingDeliveries('old')
    clearPendingDeliveries('new')
  })

  it('moves names from old session id to new', () => {
    addPendingDeliveries('old', ['a.pdf', 'b.md'])
    remapPendingDeliveries('old', 'new')
    expect(pendingDeliveriesFor('old')).toEqual([])
    expect(pendingDeliveriesFor('new').sort()).toEqual(['a.pdf', 'b.md'])
  })

  it('merges into existing new-id pending', () => {
    addPendingDeliveries('old', ['a.pdf'])
    addPendingDeliveries('new', ['c.pdf'])
    remapPendingDeliveries('old', 'new')
    expect(pendingDeliveriesFor('new').sort()).toEqual(['a.pdf', 'c.pdf'])
  })
})
