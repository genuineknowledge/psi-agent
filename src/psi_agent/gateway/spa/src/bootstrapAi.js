import { api } from './api.js'

/**
 * Server-side open-and-use bootstrap: env + built-in GLM defaults.
 * Does not open the model pool UI; no-op when AIs already exist.
 * @returns {Promise<{ id: string } | null>}
 */
export async function ensureDefaultAi() {
  try {
    const info = await api('POST', '/ais/bootstrap')
    if (info?.id) return info
  } catch (_) {
    // Missing API key or bootstrap unavailable — user can configure via Hub later.
  }
  return null
}
