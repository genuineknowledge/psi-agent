import { useCallback, useEffect, useState } from 'react'
import WorkspaceGate, { type PathPickKind } from './components/WorkspaceGate'
import HaiTunAgentWorkspace from './haitun-agent/HaiTunAgentWorkspace'
import { browseWorkspace, fetchDefaults } from './services/api'
import {
  appdataFingerprint,
  bindAppdataFingerprint,
  readScopedItem,
  writeScopedItem,
} from './services/appdataScope'
import { BrandLogo } from './haitun-agent/primitives'
import { useI18n } from './i18n'

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

function readSavedWorkspace(fp: string): string {
  try {
    const raw = readScopedItem(window.localStorage, 'workspace', fp)?.trim() || ''
    if (isLegacyWorkspacePath(raw)) return ''
    return raw
  } catch {
    return ''
  }
}

function readSavedAgent(fp: string): string {
  try {
    return readScopedItem(window.localStorage, 'agent', fp)?.trim() || ''
  } catch {
    return ''
  }
}

function writeSavedAgent(fp: string, path: string) {
  writeScopedItem(window.localStorage, 'agent', path.trim() || null, fp)
}

function writeSavedWorkspace(fp: string, path: string) {
  writeScopedItem(window.localStorage, 'workspace', path.trim() || null, fp)
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
 * - Boot from GET /defaults (+ AppData-scoped localStorage for workspace / agent).
 * - Remount workbench when AppData fingerprint or workspace changes.
 * - Pass agent into POST /sessions via HaiTunAgentWorkspace.
 */
export default function App() {
  const { t } = useI18n()
  const [workspace, setWorkspace] = useState('')
  const [defaultsWorkspace, setDefaultsWorkspace] = useState('')
  const [defaultAgent, setDefaultAgent] = useState('')
  const [appdataPath, setAppdataPath] = useState('')
  const [appdataFp, setAppdataFp] = useState('')
  const [bootstrapping, setBootstrapping] = useState(true)
  const [bootError, setBootError] = useState('')
  const [pickingKind, setPickingKind] = useState<PathPickKind | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const d = await fetchDefaults()
        if (cancelled) return

        const appdata = (d.appdata || '').trim()
        if (!appdata) {
          setBootError(t('app.appdataMissing'))
          setBootstrapping(false)
          return
        }
        const fp = appdataFingerprint(appdata)
        if (!fp) {
          setBootError(t('app.appdataMissing'))
          setBootstrapping(false)
          return
        }
        bindAppdataFingerprint(fp)
        setAppdataPath(appdata)
        setAppdataFp(fp)
        setDefaultsWorkspace((d.workspace || '').trim())

        const savedAgent = readSavedAgent(fp)
        let agent = ''
        if (savedAgent && (await pathExistsAsDir(savedAgent))) {
          agent = savedAgent
        } else if ((d.agent || '').trim()) {
          agent = d.agent.trim()
          if (savedAgent && savedAgent !== agent) writeSavedAgent(fp, '')
        }
        if (!cancelled) setDefaultAgent(agent)

        const fromDefaults = (d.workspace || '').trim()
        const saved = readSavedWorkspace(fp)
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
          writeSavedWorkspace(fp, chosen)
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
  }, [t])

  const readyWorkspace = useCallback((path: string) => {
    const clean = path.trim()
    if (appdataFp) writeSavedWorkspace(appdataFp, clean)
    setWorkspace(clean)
    setPickingKind(null)
    setBootstrapping(false)
  }, [appdataFp])

  const readyAgent = useCallback((path: string) => {
    const clean = path.trim()
    if (appdataFp) writeSavedAgent(appdataFp, clean)
    setDefaultAgent(clean)
    setPickingKind(null)
  }, [appdataFp])

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
          <p>{t('app.connecting')}</p>
        </div>
      </div>
    )
  }

  if (bootError) {
    return (
      <div className="workspace-gate">
        <div className="workspace-gate-card">
          <BrandLogo size="hero" />
          <p role="alert">{bootError}</p>
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

  // Remount when AppData fingerprint or open workspace changes so hydrate
  // cannot keep a dirty in-memory task list across GATEWAY_ORIGIN / folder switches.
  return (
    <HaiTunAgentWorkspace
      key={`${appdataFp}|${workspace}`}
      workspace={workspace}
      defaultsWorkspace={defaultsWorkspace}
      appdataPath={appdataPath}
      defaultAgent={defaultAgent}
      onChangeWorkspace={changeWorkspace}
      onChangeAgent={changeAgent}
    />
  )
}
