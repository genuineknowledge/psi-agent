import { useCallback, useEffect, useState } from 'react'
import WorkspaceGate from './components/WorkspaceGate'
import HaiTunAgentWorkspace from './haitun-agent/HaiTunAgentWorkspace'
import { browseWorkspace, fetchDefaults } from './services/api'
import { BrandLogo } from './haitun-agent/primitives'

const LS_WORKSPACE = 'gw-v2-workspace'

/** Paths that were agent packages, not user workspaces — treat as unset. */
function isLegacyWorkspacePath(path: string): boolean {
  const n = path.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
  if (!n || n === 'workspace') return true
  // Old examples/*-workspace layout (agent pack mistaken for open-folder)
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

async function pathExistsAsDir(path: string): Promise<boolean> {
  try {
    await browseWorkspace(path, { kind: 'directory' })
    return true
  } catch {
    return false
  }
}

/**
 * spa-v2 根编排：
 * - 启动时以 ``GET /defaults``.workspace 为准；localStorage 仅在路径仍存在且非遗留 agent 包时沿用
 * - 「切换工作区」打开选择页；Agent 包路径由 Gateway 默认（``agent`` 字段已留接口）
 */
export default function App() {
  const [workspace, setWorkspace] = useState('')
  const [defaultAgent, setDefaultAgent] = useState('')
  const [bootstrapping, setBootstrapping] = useState(true)
  const [picking, setPicking] = useState(false)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const d = await fetchDefaults()
        if (cancelled) return
        if (d.agent) setDefaultAgent(d.agent)
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
        // Drop stale localStorage so next boot does not revive dead paths
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
          setPicking(false)
          return
        }
        setBootstrapping(false)
        setPicking(true)
      } catch {
        if (cancelled) return
        setBootstrapping(false)
        setPicking(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const ready = useCallback((path: string) => {
    const clean = path.trim()
    try {
      window.localStorage.setItem(LS_WORKSPACE, clean)
    } catch {
      /* ignore quota */
    }
    setWorkspace(clean)
    setPicking(false)
    setBootstrapping(false)
  }, [])

  const changeWorkspace = useCallback(() => {
    setPicking(true)
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

  if (picking) {
    return (
      <WorkspaceGate
        initialPath={workspace}
        onReady={ready}
        onCancel={workspace ? () => setPicking(false) : undefined}
      />
    )
  }

  return (
    <HaiTunAgentWorkspace
      key={workspace}
      workspace={workspace}
      defaultAgent={defaultAgent}
      onChangeWorkspace={changeWorkspace}
    />
  )
}
