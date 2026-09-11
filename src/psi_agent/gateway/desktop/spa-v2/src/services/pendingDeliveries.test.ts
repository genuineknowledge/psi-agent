import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  addPendingDeliveries,
  clearPendingDeliveries,
  pendingDeliveriesFor,
  remapPendingDeliveries,
} from './pendingDeliveries'

function stubLocalStorage() {
  const store = new Map<string, string>()
  vi.stubGlobal('localStorage', {
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
  })
}

describe('remapPendingDeliveries', () => {
  beforeEach(() => {
    stubLocalStorage()
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
