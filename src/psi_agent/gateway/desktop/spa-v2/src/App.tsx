import { useCallback, useEffect, useState } from 'react'
import WorkspaceGate, { type PathPickKind } from './components/WorkspaceGate'
import HaiTunAgentWorkspace from './haitun-agent/HaiTunAgentWorkspace'
import { browseWorkspace, fetchDefaults } from './services/api'
import { BrandLogo } from './haitun-agent/primitives'

const LS_WORKSPACE = 'gw-v2-workspace'
const LS_AGENT = 'gw-v2-agent'

/** Paths that were agent packages, not user workspaces — treat as unset. */
function isLegacyWorkspacePath(path: string): boolean {
  const n = path.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
  if (!n || n === 'workspace') return true
  // Current agent-pack layout: agents/feishu (was examples/haitun-workspace)
  if (/\/workspace\/tob$/i.test(n)) return true
  // Old examples/*-workspace layout (agent pack mistaken for open-folder).
  // Kept: users upgrading still have these paths saved in localStorage.
  if (/\/examples\/[^/]+-workspace$/i.test(n)) return true
  if (n.endsWith('/haitun-workspace')) return true
  return false
}

function readSavedWorkspace(): string {
  try {
    const raw = window.localStorage.getItem(LS_WORKSPACE)?.trim() || ''
    if (isLegacyWorkspacePath(raw)) return ''
    return raw
  } catch {
    return ''
  }
}

function readSavedAgent(): string {
  try {
    return window.localStorage.getItem(LS_AGENT)?.trim() || ''
  } catch {
    return ''
  }
}

function writeSavedAgent(path: string) {
  try {
    const clean = path.trim()
    if (clean) window.localStorage.setItem(LS_AGENT, clean)
    else window.localStorage.removeItem(LS_AGENT)
  } catch {
    /* ignore quota */
  }
}

async function pathExistsAsDir(path: string): Promise<boolean> {
  try {
    await browseWorkspace(path, { kind: 'directory' })
    return true
  } catch {
    return false
  }
}

/**
 * spa-v2 root:
 * - Boot from GET /defaults (+ localStorage overrides for workspace / agent).
 * - Pass agent into POST /sessions via HaiTunAgentWorkspace.
 * - Settings can switch workspace or agent package (same PathPicker flow).
 */
export default function App() {
  const [workspace, setWorkspace] = useState('')
  const [defaultAgent, setDefaultAgent] = useState('')
  const [bootstrapping, setBootstrapping] = useState(true)
  const [pickingKind, setPickingKind] = useState<PathPickKind | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const d = await fetchDefaults()
        if (cancelled) return

        const savedAgent = readSavedAgent()
        let agent = ''
        if (savedAgent && (await pathExistsAsDir(savedAgent))) {
          agent = savedAgent
        } else if ((d.agent || '').trim()) {
          agent = d.agent.trim()
          if (savedAgent && savedAgent !== agent) writeSavedAgent('')
        }
        if (!cancelled) setDefaultAgent(agent)

        const fromDefaults = (d.workspace || '').trim()
        const saved = readSavedWorkspace()
        let chosen = ''
        if (saved && (await pathExistsAsDir(saved))) {
          chosen = saved
        } else if (fromDefaults && (await pathExistsAsDir(fromDefaults))) {
          chosen = fromDefaults
        } else if (fromDefaults) {
          chosen = fromDefaults
        }
        if (cancelled) return
        if (saved && saved !== chosen) {
          try {
            if (chosen) window.localStorage.setItem(LS_WORKSPACE, chosen)
            else window.localStorage.removeItem(LS_WORKSPACE)
          } catch {
            /* ignore */
          }
        }
        if (chosen) {
          setWorkspace(chosen)
          setBootstrapping(false)
          setPickingKind(null)
          return
        }
        setBootstrapping(false)
        setPickingKind('workspace')
      } catch {
        if (cancelled) return
        setBootstrapping(false)
        setPickingKind('workspace')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const readyWorkspace = useCallback((path: string) => {
    const clean = path.trim()
    try {
      window.localStorage.setItem(LS_WORKSPACE, clean)
    } catch {
      /* ignore quota */
    }
    setWorkspace(clean)
    setPickingKind(null)
    setBootstrapping(false)
  }, [])

  const readyAgent = useCallback((path: string) => {
    const clean = path.trim()
    writeSavedAgent(clean)
    setDefaultAgent(clean)
    setPickingKind(null)
  }, [])

  const changeWorkspace = useCallback(() => {
    setPickingKind('workspace')
  }, [])

  const changeAgent = useCallback(() => {
    setPickingKind('agent')
  }, [])

  if (bootstrapping) {
    return (
      <div className="workspace-gate" aria-busy="true">
        <div className="workspace-gate-card">
          <BrandLogo size="hero" />
          <p>正在连接 Gateway…</p>
        </div>
      </div>
    )
  }

  if (pickingKind === 'workspace') {
    return (
      <WorkspaceGate
        kind="workspace"
        initialPath={workspace}
        onReady={readyWorkspace}
        onCancel={workspace ? () => setPickingKind(null) : undefined}
      />
    )
  }

  if (pickingKind === 'agent') {
    return (
      <WorkspaceGate
        kind="agent"
        initialPath={defaultAgent}
        onReady={readyAgent}
        onCancel={() => setPickingKind(null)}
      />
    )
  }

  return (
    <HaiTunAgentWorkspace
      key={workspace}
      workspace={workspace}
      defaultAgent={defaultAgent}
      onChangeWorkspace={changeWorkspace}
      onChangeAgent={changeAgent}
    />
  )
}
