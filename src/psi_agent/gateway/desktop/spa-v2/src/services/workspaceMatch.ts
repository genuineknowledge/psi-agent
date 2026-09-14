/** Normalize workspace paths for spa-v2 session filtering (boot / refresh). */
export function normalizeWorkspacePath(path: string): string {
  return path.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
}

/**
 * Whether a Session belongs in the current workbench list.
 *
 * 刻意为之: empty ``session.workspace`` matches **only** when the open folder
 * equals the Gateway default workspace — otherwise orphan rows (created without
 * a workspace) would appear in every non-default folder's sidebar.
 */
export function sessionMatchesWorkspace(
  sessionWorkspace: string | undefined | null,
  workspaceNorm: string,
  defaultsWorkspaceNorm?: string,
): boolean {
  const w = normalizeWorkspacePath(sessionWorkspace || '')
  if (!w) {
    const defaults = normalizeWorkspacePath(defaultsWorkspaceNorm ?? '')
    if (!defaults) return true
    return workspaceNorm === defaults
  }
  return w === workspaceNorm
}

export function sessionBackendId(session: {
  ai_id?: string
  backend_id?: string
}): string | null {
  const id = (session.ai_id || session.backend_id || '').trim()
  return id || null
}
