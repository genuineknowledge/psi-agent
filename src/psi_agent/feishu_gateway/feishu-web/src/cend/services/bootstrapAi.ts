import { createAi, listAis, type AiInfo } from './api'

/**
 * Remote free-model endpoint (company domain). The upstream provider key lives
 * only in the cloud; the SPA ships a placeholder and the Gateway substitutes the
 * login token when spawning the AI process — the SPA never holds a token.
 *
 * `PLACEHOLDER_API_KEY` below is a cross-boundary contract with
 * `gateway/_free_model.py`. Change one side only and the free model silently
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

const LS_SELECTED_AI = 'spa-v2-selected-ai'

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

/** True for free-path / broken placeholder entries (must not win over real keys). */
export function isPlaceholderAi(ai: Pick<AiInfo, 'api_key'> | null | undefined): boolean {
  const key = (ai?.api_key ?? '').trim()
  return !key || key === PLACEHOLDER_API_KEY
}

export function readStoredAiId(): string | null {
  try {
    const raw = localStorage.getItem(LS_SELECTED_AI)
    return raw?.trim() || null
  } catch {
    return null
  }
}

export function writeStoredAiId(id: string | null): void {
  try {
    if (id?.trim()) localStorage.setItem(LS_SELECTED_AI, id.trim())
    else localStorage.removeItem(LS_SELECTED_AI)
  } catch {
    // ignore quota / private mode
  }
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
