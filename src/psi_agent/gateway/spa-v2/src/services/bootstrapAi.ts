import { createAi, deleteAi, listAis, type AiInfo } from './api'

/**
 * Remote free-model endpoint (company domain). Real upstream key lives only on
 * the VM behind this proxy; SPA ships a placeholder Bearer.
 *
 * Do NOT POST this on boot — empty pool must open the models panel first.
 * Create only when the user chose free mode and a session actually needs an AI.
 */
export const DEFAULT_REMOTE_AI = {
  provider: 'openai',
  model: 'deepseek-v4-flash-free',
  base_url: 'https://misakamikoto.genuineknowledge.cn',
  api_key: 'haitun-default',
}

export const PLACEHOLDER_API_KEY = 'haitun-default'

const LS_SELECTED_AI = 'spa-v2-selected-ai'

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
 * Prefer: explicit id (if still present) → stored id → first real key → first entry.
 * Never prefer a placeholder when any real AI exists.
 */
export function pickPreferredAi(
  ais: AiInfo[],
  preferredId?: string | null,
): AiInfo | null {
  if (!Array.isArray(ais) || ais.length === 0) return null
  const real = ais.filter((a) => !isPlaceholderAi(a))
  const pool = real.length > 0 ? real : ais

  const want = preferredId?.trim()
  if (want) {
    const hit = pool.find((a) => a.id === want)
    if (hit) return hit
    // Preferred was a placeholder while real AIs exist — fall through.
    const anyHit = ais.find((a) => a.id === want)
    if (anyHit && real.length === 0) return anyHit
  }

  const stored = readStoredAiId()
  if (stored) {
    const hit = pool.find((a) => a.id === stored)
    if (hit) return hit
  }

  return pool[0] ?? null
}

/** Wipe the local AI pool (user config). Empty pool = free/remote path. */
export async function clearAiPool(): Promise<void> {
  const existing = await listAis()
  if (!Array.isArray(existing) || existing.length === 0) return
  await Promise.all(existing.map((a) => deleteAi(a.id)))
  writeStoredAiId(null)
}

/**
 * Remove placeholder free AIs when the user already has a real key configured.
 * Prevents boot/`ais[0]` from binding new sessions to `haitun-default`.
 */
export async function purgePlaceholderAis(): Promise<AiInfo[]> {
  const existing = await listAis()
  if (!Array.isArray(existing) || existing.length === 0) return []
  const placeholders = existing.filter((a) => isPlaceholderAi(a))
  const real = existing.filter((a) => !isPlaceholderAi(a))
  if (placeholders.length === 0 || real.length === 0) return existing
  await Promise.all(placeholders.map((a) => deleteAi(a.id)))
  return listAis()
}

/**
 * Resolve an AI for chat/session when the pool is empty: create the remote
 * free default. If AIs already exist, return the preferred real one.
 * Call only at use time (new task / new session), never on SPA boot.
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
