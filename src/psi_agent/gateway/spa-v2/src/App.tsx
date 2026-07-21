import { useCallback, useState } from 'react'
import WorkspaceGate from './components/WorkspaceGate'
import HaiTunAgentWorkspace from './haitun-agent/HaiTunAgentWorkspace'

const LS_WORKSPACE = 'gw-v2-workspace'

function readWorkspace(): string {
  try {
    return window.localStorage.getItem(LS_WORKSPACE)?.trim() || ''
  } catch {
    return ''
  }
}

/** spa-v2 根编排：工作区门禁 → 任务工作台（Gateway Session）。 */
export default function App() {
  const [workspace, setWorkspace] = useState(readWorkspace)

  const ready = useCallback((path: string) => {
    const clean = path.trim()
    try {
      window.localStorage.setItem(LS_WORKSPACE, clean)
    } catch {
      /* ignore quota */
    }
    setWorkspace(clean)
  }, [])

  const changeWorkspace = useCallback(() => {
    try {
      window.localStorage.removeItem(LS_WORKSPACE)
    } catch {
      /* ignore */
    }
    setWorkspace('')
  }, [])

  if (!workspace) {
    return <WorkspaceGate onReady={ready} />
  }

  return (
    <HaiTunAgentWorkspace
      key={workspace}
      workspace={workspace}
      onChangeWorkspace={changeWorkspace}
    />
  )
}
