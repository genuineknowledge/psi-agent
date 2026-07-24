import { FolderOpen, Loader2 } from 'lucide-react'
import { FormEvent, useEffect, useState } from 'react'
import { BrandLogo } from '../haitun-agent/primitives'
import { fetchCwd } from '../services/api'
import PathPickerDialog from './PathPickerDialog'

type Props = {
  /** Prefill when switching from an existing workspace. */
  initialPath?: string
  onReady: (workspace: string) => void
  /** Return to previous workspace without changing (settings → 切换). */
  onCancel?: () => void
}

/** Pick / confirm a workspace directory (used when switching; first-run defaults to Gateway cwd). */
export default function WorkspaceGate({ initialPath = '', onReady, onCancel }: Props) {
  const [path, setPath] = useState(initialPath)
  const [loading, setLoading] = useState(!initialPath)
  const [error, setError] = useState<string | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)

  useEffect(() => {
    if (initialPath.trim()) {
      setPath(initialPath.trim())
      setLoading(false)
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const cwd = await fetchCwd()
        if (!cancelled) setPath(cwd?.cwd || '')
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [initialPath])

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
        <p>任务会绑定到 Gateway Session。请选择本机工作区目录，Agent 的 tools 与 history 都落在该目录下。</p>
        {loading ? (
          <div className="workspace-gate-loading"><Loader2 className="spin" size={22} /> 正在连接 Gateway…</div>
        ) : (
          <form onSubmit={submit}>
            <label>
              <span>工作区路径</span>
              <div className="workspace-gate-path-row">
                <button
                  type="button"
                  className="workspace-gate-browse"
                  onClick={() => setPickerOpen(true)}
                  aria-label="浏览文件夹"
                  title="浏览文件夹"
                >
                  <FolderOpen size={18} />
                </button>
                <input
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  placeholder="例如 D:\Haitun develop\examples\haitun-workspace"
                  autoFocus
                />
              </div>
            </label>
            {error && <div className="workspace-gate-error" role="alert">{error}</div>}
            <div className="workspace-gate-actions">
              {onCancel && (
                <button type="button" className="secondary-button" onClick={onCancel}>
                  取消
                </button>
              )}
              <button type="submit" className="primary-button" disabled={!path.trim()}>
                <FolderOpen size={16} /> 进入任务工作台
              </button>
            </div>
          </form>
        )}
      </div>

      <PathPickerDialog
        open={pickerOpen}
        initialPath={path}
        title="打开工作区"
        confirmLabel="打开"
        hint="选择本地文件夹作为 Agent 工作区。"
        onCancel={() => setPickerOpen(false)}
        onConfirm={(picked) => {
          setPath(picked)
          setPickerOpen(false)
          setError(null)
        }}
      />
    </div>
  )
}
