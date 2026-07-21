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

/** Wipe the local AI pool (user config). Empty pool = free/remote path. */
export async function clearAiPool(): Promise<void> {
  const existing = await listAis()
  if (!Array.isArray(existing) || existing.length === 0) return
  await Promise.all(existing.map((a) => deleteAi(a.id)))
}

/**
 * Resolve an AI for chat/session when the pool is empty: create the remote
 * free default. If AIs already exist, return the first. Call only at use time
 * (new task / new session), never on SPA boot.
 */
export async function ensureDefaultAi(): Promise<AiInfo | null> {
  try {
    const existing = await listAis()
    if (Array.isArray(existing) && existing.length > 0) return existing[0]
    const info = await createAi({ ...DEFAULT_REMOTE_AI })
    if (info?.id) return info
  } catch {
    // Proxy unreachable or create failed — Hub models panel can still configure.
  }
  try {
    const again = await listAis()
    return again[0] ?? null
  } catch {
    return null
  }
}
