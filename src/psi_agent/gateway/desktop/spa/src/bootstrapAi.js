import { api } from './api.js'

/**
 * SPA open-and-use defaults. Gateway only exposes POST /ais — no server-side
 * bootstrap. The upstream provider key lives only in the cloud; the client ships
 * a placeholder and the Gateway substitutes the login token when spawning the AI
 * process.
 *
 * `base_url` must stay same-origin with the account service and `api_key` must
 * match `PLACEHOLDER_API_KEY` in `gateway/desktop/_free_model.py` — the Gateway only
 * substitutes when both hold. Keep this in sync with `spa-v2`'s copy.
 */
export const DEFAULT_REMOTE_AI = {
  provider: 'openai',
  model: 'deepseek-v4-flash',
  base_url: 'https://account.genuineknowledge.cn/llm/v1',
  api_key: 'haitun-default',
}

/**
 * When the AI pool is empty, create the remote default via POST /ais.
 * No-op when AIs already exist. Does not open the model pool UI.
 * @returns {Promise<{ id: string } | null>}
 */
export async function ensureDefaultAi() {
  try {
    const ais = await api('GET', '/ais')
    if (Array.isArray(ais) && ais.length > 0) return null
    const info = await api('POST', '/ais', { ...DEFAULT_REMOTE_AI })
    if (info?.id) return info
  } catch (_) {
    // Proxy unreachable or create failed — user can configure via Hub later.
  }
  return null
}
