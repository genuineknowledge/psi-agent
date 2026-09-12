import { beforeEach, describe, expect, it } from 'vitest'
import {
  LEGACY_LS,
  appdataFingerprint,
  bindAppdataFingerprint,
  normalizeAppdataPath,
  readScopedItem,
  scopedLsKey,
  writeScopedItem,
} from './appdataScope'

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const map = new Map<string, string>(Object.entries(initial))
  return {
    get length() {
      return map.size
    },
    clear() {
      map.clear()
    },
    getItem(key: string) {
      return map.has(key) ? map.get(key)! : null
    },
    key(index: number) {
      return [...map.keys()][index] ?? null
    },
    removeItem(key: string) {
      map.delete(key)
    },
    setItem(key: string, value: string) {
      map.set(key, String(value))
    },
  }
}

describe('normalizeAppdataPath', () => {
  it('unifies slashes, strips trailing, lowercases', () => {
    expect(normalizeAppdataPath('C:\\Users\\Me\\AppData\\Local\\Haitun\\')).toBe(
      'c:/users/me/appdata/local/haitun',
    )
  })
})

describe('appdataFingerprint', () => {
  it('is stable for equivalent path spellings', () => {
    const a = appdataFingerprint('C:/Users/Me/AppData/Local/Haitun')
    const b = appdataFingerprint('c:\\Users\\Me\\AppData\\Local\\Haitun\\')
    expect(a).toBe(b)
    expect(a).toMatch(/^[0-9a-f]{8}$/)
  })

  it('differs for different AppData roots', () => {
    expect(appdataFingerprint('/tmp/appdata-a')).not.toBe(
      appdataFingerprint('/tmp/appdata-b'),
    )
  })

  it('does not incorporate origin (port-only change is irrelevant)', () => {
    // Fingerprint is path-only by construction — same path ⇒ same fp.
    const fp = appdataFingerprint('/data/haitun')
    expect(fp).toBe(appdataFingerprint('/data/haitun'))
  })

  it('returns empty for blank input', () => {
    expect(appdataFingerprint('')).toBe('')
    expect(appdataFingerprint('   ')).toBe('')
  })
})

describe('scopedLsKey', () => {
  it('matches design key shapes', () => {
    expect(scopedLsKey('workspace', 'deadbeef')).toBe('gw-v2:deadbeef:workspace')
    expect(scopedLsKey('agent', 'deadbeef')).toBe('gw-v2:deadbeef:agent')
    expect(scopedLsKey('pinned', 'deadbeef')).toBe('gw-v2:deadbeef:pinned-task-ids')
    expect(scopedLsKey('pending', 'deadbeef')).toBe('spa-v2:deadbeef:pending-deliveries')
    expect(scopedLsKey('selectedAi', 'deadbeef')).toBe('spa-v2:deadbeef:selected-ai')
  })
})

describe('readScopedItem / writeScopedItem', () => {
  beforeEach(() => {
    bindAppdataFingerprint('')
  })

  it('migrates legacy once without deleting the old key', () => {
    const fp = 'abcd1234'
    const storage = memoryStorage({
      [LEGACY_LS.workspace]: '/old/ws',
    })
    expect(readScopedItem(storage, 'workspace', fp)).toBe('/old/ws')
    expect(storage.getItem(scopedLsKey('workspace', fp))).toBe('/old/ws')
    expect(storage.getItem(LEGACY_LS.workspace)).toBe('/old/ws')

    writeScopedItem(storage, 'workspace', '/new/ws', fp)
    expect(readScopedItem(storage, 'workspace', fp)).toBe('/new/ws')
    // Legacy left alone after migrate + later write.
    expect(storage.getItem(LEGACY_LS.workspace)).toBe('/old/ws')
  })

  it('does not overwrite an existing scoped value with legacy', () => {
    const fp = 'abcd1234'
    const storage = memoryStorage({
      [LEGACY_LS.workspace]: '/legacy',
      [scopedLsKey('workspace', fp)]: '/scoped',
    })
    expect(readScopedItem(storage, 'workspace', fp)).toBe('/scoped')
  })

  it('isolates two fingerprints', () => {
    const storage = memoryStorage()
    writeScopedItem(storage, 'pinned', '["a"]', 'fp1')
    writeScopedItem(storage, 'pinned', '["b"]', 'fp2')
    expect(readScopedItem(storage, 'pinned', 'fp1')).toBe('["a"]')
    expect(readScopedItem(storage, 'pinned', 'fp2')).toBe('["b"]')
  })

  it('uses bound fingerprint when fp arg omitted', () => {
    const storage = memoryStorage()
    bindAppdataFingerprint('bound000')
    writeScopedItem(storage, 'selectedAi', 'ai-1')
    expect(storage.getItem(scopedLsKey('selectedAi', 'bound000'))).toBe('ai-1')
    expect(readScopedItem(storage, 'selectedAi')).toBe('ai-1')
  })
})
