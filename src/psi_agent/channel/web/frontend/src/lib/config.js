// Runtime configuration for talking to the Agent Session Protocol backend.
//
// The protocol server (POST /sessions, /sessions/{id}/messages, ...) is a
// separate backend service. This value lets the built frontend point at it
// without rebuilding:
//
//   window.AGENT_API_BASE  — base URL, e.g. "https://api.agent.example.com/v1"
//                            (default "/v1", i.e. same-origin)
//
// It can be injected by a small <script> in index.html, or overridden at
// build time via Vite env (VITE_AGENT_API_BASE).

function pick(runtimeKey, envKey, fallback) {
  if (typeof window !== 'undefined' && window[runtimeKey]) return window[runtimeKey]
  const env = import.meta.env || {}
  if (env[envKey]) return env[envKey]
  return fallback
}

export const API_BASE = pick('AGENT_API_BASE', 'VITE_AGENT_API_BASE', '/v1').replace(/\/+$/, '')
