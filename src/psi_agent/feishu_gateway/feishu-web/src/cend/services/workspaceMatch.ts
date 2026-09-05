/** Normalize workspace paths for spa-v2 session filtering (boot / refresh). */
export function normalizeWorkspacePath(path: string): string {
  return path.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
}

/**
 * Whether a Session belongs in the current workbench list.
 * Empty session.workspace matches any open folder (legacy / unset).
 */
export function sessionMatchesWorkspace(
  sessionWorkspace: string | undefined | null,
  workspaceNorm: string,
): boolean {
  const w = normalizeWorkspacePath(sessionWorkspace || '')
  return !w || w === workspaceNorm
}

export function sessionBackendId(session: {
  ai_id?: string
  backend_id?: string
}): string | null {
  const id = (session.ai_id || session.backend_id || '').trim()
  return id || null
}
