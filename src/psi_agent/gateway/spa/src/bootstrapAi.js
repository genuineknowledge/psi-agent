import { api } from './api.js'

/**
 * SPA open-and-use defaults. Gateway only exposes POST /ais — no server-side
 * bootstrap endpoint. Upstream secrets stay on the company proxy (Nginx);
 * the client ships a placeholder Bearer only.
 */
export const DEFAULT_REMOTE_AI = {
  provider: 'openai',
  model: 'glm-4-flash',
  base_url: 'https://haitun.genuineknowledge.cn',
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
