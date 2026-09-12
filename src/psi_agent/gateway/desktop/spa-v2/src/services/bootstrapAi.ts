import { createAi, listAis, type AiInfo } from './api'
import { readScopedItem, writeScopedItem } from './appdataScope'

/**
 * Remote free-model endpoint (company domain). The upstream provider key lives
 * only in the cloud; the SPA ships a placeholder and the Gateway substitutes the
 * login token when spawning the AI process — the SPA never holds a token.
 *
 * `PLACEHOLDER_API_KEY` below is a cross-boundary contract with
 * `gateway/desktop/_free_model.py`. Change one side only and the free model silently
 * ships the placeholder to the cloud, which answers 401.
 *
 * Do NOT POST this on boot when the pool is empty and there are no Sessions —
 * open the models panel first. If a Session's bound AI was deleted, the next
 * chat falls back to the currently selected model (see ``ensureSessionAi``).
 */
/**
 * Aligns with Hub model pool DeepSeek preset (`deepseek-v4-flash`).
 *
 * `base_url` must stay same-origin with the account service: the Gateway only
 * swaps the placeholder for a real token when the two origins match, so a
 * different host silently gets an empty key (and a 401 from upstream).
 */
export const DEFAULT_REMOTE_AI = {
  provider: 'openai',
  model: 'deepseek-v4-flash',
  base_url: 'https://account.genuineknowledge.cn/llm/v1',
  api_key: 'haitun-default',
}

export const PLACEHOLDER_API_KEY = 'haitun-default'

/** User-chosen display names, keyed by ``aiConfigKey`` (survives id rebind; not AppData-scoped). */
const LS_AI_ALIASES = 'spa-v2-ai-aliases'

/** Config fingerprint — same provider/model/key/base ⇒ one row in the Hub list. */
export function aiConfigKey(
  ai: Pick<AiInfo, 'provider' | 'model' | 'api_key' | 'base_url'>,
): string {
  const base = (ai.base_url ?? '').trim().replace(/\/+$/, '')
  return [ai.provider ?? '', ai.model ?? '', ai.api_key ?? '', base].join('\0')
}

/**
 * Collapse AIs that differ only by instance id (e.g. free-path revive under
 * multiple Session ``ai_id``s). Different ``api_key`` (or model/base) stay separate.
 * When ``preferredId`` is in a duplicate group, that instance is the survivor.
 */
export function dedupeAisForDisplay(
  ais: AiInfo[],
  preferredId?: string | null,
): AiInfo[] {
  if (!Array.isArray(ais) || ais.length === 0) return []
  const prefer = preferredId?.trim() || ''
  const byKey = new Map<string, AiInfo>()
  for (const a of ais) {
    const key = aiConfigKey(a)
    const prev = byKey.get(key)
    if (!prev) {
      byKey.set(key, a)
      continue
    }
    if (prefer && a.id === prefer) byKey.set(key, a)
  }
  return [...byKey.values()]
}

export type AiDisplayRow = {
  ai: AiInfo
  /** Primary label shown in the pool (alias or model, plus (n) when needed). */
  title: string
  /** Secondary line: free vs own-key tip + provider. */
  subtitle: string
}

function readAliasMap(): Record<string, string> {
  try {
    const raw = localStorage.getItem(LS_AI_ALIASES)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const out: Record<string, string> = {}
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof v === 'string' && v.trim()) out[k] = v.trim()
    }
    return out
  } catch {
    return {}
  }
}

function writeAliasMap(map: Record<string, string>): void {
  try {
    if (Object.keys(map).length === 0) localStorage.removeItem(LS_AI_ALIASES)
    else localStorage.setItem(LS_AI_ALIASES, JSON.stringify(map))
  } catch {
    // ignore quota / private mode
  }
}

/** Custom display name for this config, or ``null`` when unset. */
export function readAiAlias(
  ai: Pick<AiInfo, 'provider' | 'model' | 'api_key' | 'base_url'>,
): string | null {
  const name = readAliasMap()[aiConfigKey(ai)]
  return name?.trim() || null
}

/** Persist / clear a custom display name. Empty string clears. */
export function writeAiAlias(
  ai: Pick<AiInfo, 'provider' | 'model' | 'api_key' | 'base_url'>,
  alias: string | null,
): void {
  const key = aiConfigKey(ai)
  const map = readAliasMap()
  const next = (alias ?? '').trim()
  if (!next) delete map[key]
  else map[key] = next
  writeAliasMap(map)
}

/** Last 4 chars of a key for UI tips (never the full secret). */
export function maskApiKeyTip(apiKey: string | null | undefined): string {
  const key = (apiKey ?? '').trim()
  if (!key) return '无 Key'
  if (key.length <= 4) return `···${key}`
  return `···${key.slice(-4)}`
}

function baseTitle(
  ai: AiInfo,
  aliases: Record<string, string>,
): string {
  const alias = aliases[aiConfigKey(ai)]
  if (alias?.trim()) return alias.trim()
  return (ai.model || ai.id || '未命名模型').trim()
}

function subtitleFor(ai: AiInfo): string {
  const provider = (ai.provider || '').trim() || 'unknown'
  if (isPlaceholderAi(ai)) return `免费 · ${provider}`
  return `自有 Key ${maskApiKeyTip(ai.api_key)} · ${provider}`
}

/**
 * Dedupe then label rows for the Hub list.
 *
 * Same model name across different keys stays as separate rows (dedupe already
 * keeps them). Titles that still collide get `` (1)`` / `` (2)``; the subtitle
 * always shows free vs own-key so paid entries are distinguishable without
 * opening the full key.
 */
export function labelAisForDisplay(
  ais: AiInfo[],
  preferredId?: string | null,
): AiDisplayRow[] {
  const rows = dedupeAisForDisplay(ais, preferredId)
  if (rows.length === 0) return []
  const aliases = readAliasMap()
  const bases = rows.map((ai) => baseTitle(ai, aliases))
  const counts = new Map<string, number>()
  for (const t of bases) counts.set(t, (counts.get(t) ?? 0) + 1)
  const seen = new Map<string, number>()
  return rows.map((ai, i) => {
    const base = bases[i] ?? '未命名模型'
    let title = base
    if ((counts.get(base) ?? 0) > 1) {
      const n = (seen.get(base) ?? 0) + 1
      seen.set(base, n)
      title = `${base} (${n})`
    }
    return { ai, title, subtitle: subtitleFor(ai) }
  })
}

/** True for free-path / broken placeholder entries (must not win over real keys). */
export function isPlaceholderAi(ai: Pick<AiInfo, 'api_key'> | null | undefined): boolean {
  const key = (ai?.api_key ?? '').trim()
  return !key || key === PLACEHOLDER_API_KEY
}

export function readStoredAiId(): string | null {
  try {
    return readScopedItem(localStorage, 'selectedAi')?.trim() || null
  } catch {
    return null
  }
}

export function writeStoredAiId(id: string | null): void {
  writeScopedItem(localStorage, 'selectedAi', id?.trim() || null)
}

/**
 * Prefer: user's explicit/stored id when it still exists (free placeholder
 * included — they deliberately selected it) → first real key → first entry.
 * Unselected `haitun-default` placeholders never win over real keys.
 */
export function pickPreferredAi(
  ais: AiInfo[],
  preferredId?: string | null,
): AiInfo | null {
  if (!Array.isArray(ais) || ais.length === 0) return null

  const want = preferredId?.trim()
  if (want) {
    const hit = ais.find((a) => a.id === want)
    if (hit) return hit
  }

  const stored = readStoredAiId()
  if (stored) {
    const hit = ais.find((a) => a.id === stored)
    if (hit) return hit
  }

  const real = ais.filter((a) => !isPlaceholderAi(a))
  const pool = real.length > 0 ? real : ais
  return pool[0] ?? null
}

/**
 * Resolve an AI for chat/session when the pool is empty: create the remote
 * free default. If AIs already exist, return the preferred real one.
 * Call only at use time (new task / new session), never on SPA boot alone.
 */
export async function ensureDefaultAi(
  preferredId?: string | null,
): Promise<AiInfo | null> {
  try {
    const existing = await listAis()
    if (Array.isArray(existing) && existing.length > 0) {
      return pickPreferredAi(existing, preferredId)
    }
    const info = await createAi({ ...DEFAULT_REMOTE_AI })
    if (info?.id) {
      writeStoredAiId(info.id)
      return info
    }
  } catch {
    // Proxy unreachable or create failed — Hub models panel can still configure.
  }
  try {
    const again = await listAis()
    return pickPreferredAi(again, preferredId)
  } catch {
    return null
  }
}

/**
 * Single workbench AI hydrate (boot + Hub free-switch share this).
 *
 * Loads the current pool and picks the UI selection; only opens models when the
 * pool is still empty. Connected AIs are never removed or revived here — only
 * the delete button removes models.
 */
export async function hydrateAiForSessions(
  preferredId?: string | null,
): Promise<{ ais: AiInfo[]; preferred: AiInfo | null; openModels: boolean }> {
  const ais = await listAis()
  const preferred = pickPreferredAi(ais, preferredId)
  if (preferred?.id) writeStoredAiId(preferred.id)
  return {
    ais,
    preferred,
    openModels: ais.length === 0,
  }
}

/**
 * Resolve the AI used for one chat turn.
 *
 * Prefer the Session's bound ``ai_id`` when it is still in the pool. If that
 * model was deleted, rebind the old id to the currently selected model's
 * config: the Session keeps its id (history/titles survive), but its AI socket
 * comes back alive with the new model so the next chat actually uses it.
 */
export async function ensureSessionAi(
  sessionAiId?: string | null,
): Promise<AiInfo | null> {
  const want = sessionAiId?.trim() || null
  let existing: AiInfo[] = []
  try {
    existing = await listAis()
  } catch {
    existing = []
  }

  const bound = want ? existing.find((a) => a.id === want) : undefined
  if (bound) {
    writeStoredAiId(bound.id)
    return bound
  }

  let current = pickPreferredAi(existing, readStoredAiId())
  if (!current) {
    current = await ensureDefaultAi(want)
    if (!current) return null
  }

  // Rebind the dangling Session id to the current model so its channel socket
  // becomes reachable again (same id, current config).
  if (want && current.id !== want) {
    try {
      await createAi({
        provider: current.provider,
        model: current.model,
        api_key: current.api_key ?? '',
        base_url: current.base_url,
        id: want,
      })
    } catch {
      // Race (already exists) or transient backend issue — next turn retries.
    }
  }

  writeStoredAiId(current.id)
  return current
}
