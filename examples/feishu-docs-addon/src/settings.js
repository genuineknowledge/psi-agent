/**
 * Backend settings — where the gateway lives and the shared token to reach it.
 *
 * Kept configurable rather than hardcoded because the add-on runs inside a
 * Feishu-hosted iframe whose CSP only permits requests to domains whitelisted in
 * the developer console. Feishu's docs do not state whether `localhost` / plain
 * `http` can be whitelisted, so the same build has to work against a local
 * gateway during development and a real host once published.
 *
 * Settings live in the block's Record, i.e. they travel with the block: whoever
 * opens the document gets the same backend without reconfiguring. Note the token
 * is therefore readable by anyone who can read the document — it gates access to
 * the gateway, so treat it as a shared team secret, not a personal credential.
 */

const RECORD_KEY = 'haitunBackend'

export const DEFAULT_SETTINGS = {
  baseUrl: 'http://localhost:8000',
  token: '',
}

/** Strip trailing slashes so `${baseUrl}/docs-addon/chat` never doubles up. */
export function normalizeBaseUrl(raw) {
  return String(raw || '').trim().replace(/\/+$/, '')
}

export function readSettings(record) {
  const stored = (record && record[RECORD_KEY]) || {}
  return {
    baseUrl: normalizeBaseUrl(stored.baseUrl) || DEFAULT_SETTINGS.baseUrl,
    token: typeof stored.token === 'string' ? stored.token : DEFAULT_SETTINGS.token,
  }
}

export function settingsChangeset(settings) {
  return [
    {
      type: 'replace',
      path: [RECORD_KEY],
      value: {
        baseUrl: normalizeBaseUrl(settings.baseUrl),
        token: settings.token || '',
      },
    },
  ]
}

export function isConfigured(settings) {
  return Boolean(settings.baseUrl && settings.token)
}
