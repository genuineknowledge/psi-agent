/**
 * AppData-scoped localStorage for spa-v2 workbench prefs.
 *
 * Fingerprint is a short hash of the normalized AppData path only — never
 * ``window.location.origin`` (installer random ports would otherwise split the
 * same memory root into many preference buckets).
 *
 * 刻意为之: ``bindAppdataFingerprint`` is the single writer (App boot / remount).
 * Storage helpers read the bound fp so deep call sites need not thread it.
 */

export const LEGACY_LS = {
  workspace: 'gw-v2-workspace',
  agent: 'gw-v2-agent',
  pinned: 'gw-v2-pinned-task-ids',
  pending: 'spa-v2-pending-deliveries',
  selectedAi: 'spa-v2-selected-ai',
} as const

export type ScopedLsKind = keyof typeof LEGACY_LS

/** Align with workspaceMatch: slash unify, strip trailing, lowercase. */
export function normalizeAppdataPath(path: string): string {
  return path.trim().replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
}

/** FNV-1a 32-bit → 8 hex chars. Empty input → empty fingerprint. */
export function appdataFingerprint(appdata: string): string {
  const n = normalizeAppdataPath(appdata)
  if (!n) return ''
  let h = 2166136261 >>> 0
  for (let i = 0; i < n.length; i++) {
    h ^= n.charCodeAt(i)
    h = Math.imul(h, 16777619) >>> 0
  }
  return h.toString(16).padStart(8, '0')
}

export function scopedLsKey(kind: ScopedLsKind, fp: string): string {
  const clean = fp.trim()
  switch (kind) {
    case 'workspace':
      return `gw-v2:${clean}:workspace`
    case 'agent':
      return `gw-v2:${clean}:agent`
    case 'pinned':
      return `gw-v2:${clean}:pinned-task-ids`
    case 'pending':
      return `spa-v2:${clean}:pending-deliveries`
    case 'selectedAi':
      return `spa-v2:${clean}:selected-ai`
  }
}

let _activeFp = ''

/** App sets this after GET /defaults before mounting the workbench. */
export function bindAppdataFingerprint(fp: string): void {
  _activeFp = fp.trim()
}

export function currentAppdataFingerprint(): string {
  return _activeFp
}

/**
 * Read a scoped item. If the scoped key is absent and a legacy key exists,
 * copy once into the scoped key (do not delete legacy).
 */
export function readScopedItem(
  storage: Storage,
  kind: ScopedLsKind,
  fp: string = _activeFp,
): string | null {
  const cleanFp = fp.trim()
  if (!cleanFp) {
    try {
      return storage.getItem(LEGACY_LS[kind])
    } catch {
      return null
    }
  }
  const sk = scopedLsKey(kind, cleanFp)
  try {
    const scoped = storage.getItem(sk)
    if (scoped !== null) return scoped
    const legacy = storage.getItem(LEGACY_LS[kind])
    if (legacy === null) return null
    try {
      storage.setItem(sk, legacy)
    } catch {
      /* quota — still return legacy value */
    }
    return legacy
  } catch {
    return null
  }
}

export function writeScopedItem(
  storage: Storage,
  kind: ScopedLsKind,
  value: string | null,
  fp: string = _activeFp,
): void {
  const cleanFp = fp.trim()
  const key = cleanFp ? scopedLsKey(kind, cleanFp) : LEGACY_LS[kind]
  try {
    if (value === null || value === '') storage.removeItem(key)
    else storage.setItem(key, value)
  } catch {
    /* ignore quota / private mode */
  }
}
