import { useCallback, useEffect, useState } from 'react'
import WorkspaceGate from './components/WorkspaceGate'
import HaiTunAgentWorkspace from './haitun-agent/HaiTunAgentWorkspace'
import { fetchCwd } from './services/api'
import { BrandLogo } from './haitun-agent/primitives'

const LS_WORKSPACE = 'gw-v2-workspace'

/** Read saved path; treat legacy default ``workspace`` as unset. */
function readSavedWorkspace(): string {
  try {
    const raw = window.localStorage.getItem(LS_WORKSPACE)?.trim() || ''
    if (!raw || raw === 'workspace') return ''
    return raw
  } catch {
    return ''
  }
}

/**
 * spa-v2 根编排：
 * - 无记忆时默认 Gateway cwd（启动目录即 haitun-workspace）
 * - 「切换工作区」打开选择页（含浏览目录，对齐 spa v1 PathPicker）
 */
export default function App() {
  const [workspace, setWorkspace] = useState(readSavedWorkspace)
  const [bootstrapping, setBootstrapping] = useState(() => !readSavedWorkspace())
  const [picking, setPicking] = useState(false)

  useEffect(() => {
    if (!bootstrapping) return
    let cancelled = false
    void fetchCwd()
      .then((info) => {
        if (cancelled) return
        const cwd = (info.cwd || '').trim()
        if (cwd) {
          setWorkspace(cwd)
          setBootstrapping(false)
          return
        }
        setBootstrapping(false)
        setPicking(true)
      })
      .catch(() => {
        if (cancelled) return
        setBootstrapping(false)
        setPicking(true)
      })
    return () => {
      cancelled = true
    }
  }, [bootstrapping])

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
      onChangeWorkspace={changeWorkspace}
    />
  )
}
