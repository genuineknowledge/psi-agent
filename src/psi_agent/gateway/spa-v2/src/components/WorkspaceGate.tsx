import { FolderOpen, Loader2 } from 'lucide-react'
import { FormEvent, useEffect, useState } from 'react'
import { BrandLogo } from '../haitun-agent/primitives'
import { fetchCwd, fetchWorkspaceRoots } from '../services/api'

type Props = {
  onReady: (workspace: string) => void
}

function normalizeRoots(raw: unknown): string[] {
  if (!raw) return []
  if (Array.isArray(raw)) {
    return raw
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && 'path' in item) {
          return String((item as { path: string }).path)
        }
        return ''
      })
      .filter(Boolean)
  }
  if (typeof raw === 'object' && raw && 'roots' in raw) {
    return normalizeRoots((raw as { roots: unknown }).roots)
  }
  return []
}

/** First-run gate: pick a workspace before creating Gateway sessions. */
export default function WorkspaceGate({ onReady }: Props) {
  const [roots, setRoots] = useState<string[]>([])
  const [path, setPath] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [rootsRaw, cwd] = await Promise.all([
          fetchWorkspaceRoots().catch(() => null),
          fetchCwd().catch(() => null),
        ])
        if (cancelled) return
        const list = normalizeRoots(rootsRaw)
        setRoots(list)
        const initial = cwd?.cwd || list[0] || ''
        setPath(initial)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const clean = path.trim()
    if (!clean) {
      setError('请选择或输入工作区路径')
      return
    }
    onReady(clean)
  }

  return (
    <div className="workspace-gate">
      <div className="workspace-gate-card">
        <BrandLogo size="hero" />
        <span className="eyebrow">HaiTun Agent</span>
        <h1>打开工作区</h1>
        <p>任务会绑定到 Gateway Session。请先选择本机工作区目录，Agent 的 tools 与 history 都落在该目录下。</p>
        {loading ? (
          <div className="workspace-gate-loading"><Loader2 className="spin" size={22} /> 正在连接 Gateway…</div>
        ) : (
          <form onSubmit={submit}>
            {roots.length > 0 && (
              <div className="workspace-gate-roots">
                {roots.map((root) => (
                  <button key={root} type="button" className={path === root ? 'active' : ''} onClick={() => setPath(root)}>
                    <FolderOpen size={16} />
                    <span>{root}</span>
                  </button>
                ))}
              </div>
            )}
            <label>
              <span>工作区路径</span>
              <input
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="例如 D:\Haitun develop\examples\haitun-workspace"
                autoFocus
              />
            </label>
            {error && <div className="workspace-gate-error" role="alert">{error}</div>}
            <button type="submit" className="primary-button" disabled={!path.trim()}>
              <FolderOpen size={16} /> 进入任务工作台
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
